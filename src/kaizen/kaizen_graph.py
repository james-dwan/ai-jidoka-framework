"""LangGraph integration: build workflows with Jidoka built in.

:class:`KaizenGraphBuilder` wraps ``langgraph.graph.StateGraph``. Every node you
add is instrumented so that, after it runs (or raises), the configured
abnormality rules are evaluated. Abnormalities become structured exception
records, Kanban tickets, and run-log events; at or above the configured
severity the workflow routes to the ``andon`` node and stops — the problem is
made visible instead of being passed downstream.

Usage::

    from kaizen import KaizenConfig, KaizenGraphBuilder

    config = KaizenConfig.load("config/kaizen_config.yaml")
    builder = KaizenGraphBuilder(MyState, config)
    builder.add_node("collect", collect)
    builder.add_node("calculate", calculate)
    builder.add_edge("collect", "calculate")
    builder.set_entry_point("collect")
    builder.set_finish_point("calculate")
    graph = builder.compile()
    result = graph.invoke({"reports": [...]})
"""

from __future__ import annotations

import uuid
from typing import Any, Callable, Dict, List, Optional, TypedDict

try:
    from langgraph.graph import StateGraph, END
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "The AI Kaizen Framework requires LangGraph. Install it with: pip install langgraph"
    ) from exc

from .config import KaizenConfig
from .exception_handler import AbnormalityRule, ExceptionHandler, ExceptionRecord
from .kanban_integration import KanbanBoard, create_board
from .runlog import RunLog


class KaizenState(TypedDict, total=False):
    """Fields the framework manages. Extend this TypedDict with your own
    process fields (or use any TypedDict that includes these keys)."""

    kaizen_run_id: str
    kaizen_stopped: bool
    kaizen_stop_reason: str
    kaizen_exceptions: List[Dict[str, Any]]


ANDON_NODE = "andon"


class KaizenGraphBuilder:
    """A thin, opinionated wrapper around ``StateGraph``.

    - ``add_node`` wraps each node with abnormality detection and error capture
    - edges automatically pass through a Jidoka gate: when a stop is triggered,
      the flow is diverted to the ``andon`` node instead of the next step
    - every run and node completion is written to the shared run log
    """

    def __init__(
        self,
        state_schema: type,
        config: KaizenConfig,
        board: Optional[KanbanBoard] = None,
        runlog: Optional[RunLog] = None,
        token_provider: Optional[Callable[[], str]] = None,
    ):
        self.config = config
        self.runlog = runlog or RunLog()
        self.board = board or create_board(config.kanban, token_provider)
        self.handler = ExceptionHandler(
            process_name=config.process_name,
            rules=[AbnormalityRule.from_dict(r) for r in config.rules],
            board=self.board,
            runlog=self.runlog,
            stop_on_severity=config.stop_on_severity,
            sandbox=config.sandbox,
            problem_bucket=config.kanban.get("buckets", {}).get("problems", "Problems"),
            tickets_on_stop=config.tickets.get("on_stop", True),
        )
        self._graph = StateGraph(state_schema)
        self._entry: Optional[str] = None
        self._graph.add_node(ANDON_NODE, self._andon)

    # -- graph construction -----------------------------------------------

    def add_node(self, name: str, fn: Callable[[Dict[str, Any]], Dict[str, Any]]) -> "KaizenGraphBuilder":
        self._graph.add_node(name, self._wrap(name, fn))
        return self

    def add_edge(self, start: str, end: str) -> "KaizenGraphBuilder":
        """Add an edge with a Jidoka gate: stop diverts to the andon node."""
        if end == END:
            self._graph.add_edge(start, END)
            return self
        self._graph.add_conditional_edges(
            start,
            self._make_gate(end),
            {end: end, ANDON_NODE: ANDON_NODE},
        )
        return self

    def add_conditional_edges(self, start: str, router: Callable, mapping: Dict[str, str]) -> "KaizenGraphBuilder":
        """Custom routing, still protected by the Jidoka gate."""

        def gated_router(state: Dict[str, Any]) -> str:
            if state.get("kaizen_stopped"):
                return ANDON_NODE
            return router(state)

        mapping = dict(mapping)
        mapping[ANDON_NODE] = ANDON_NODE
        self._graph.add_conditional_edges(start, gated_router, mapping)
        return self

    def set_entry_point(self, name: str) -> "KaizenGraphBuilder":
        self._entry = name
        self._graph.set_entry_point(name)
        return self

    def set_finish_point(self, name: str) -> "KaizenGraphBuilder":
        self._graph.add_edge(name, END)
        return self

    def compile(self, **kwargs: Any):
        self._graph.add_edge(ANDON_NODE, END)
        compiled = self._graph.compile(**kwargs)
        return _KaizenRunner(compiled, self.runlog, self.config)

    # -- instrumentation ---------------------------------------------------

    def _wrap(self, name: str, fn: Callable[[Dict[str, Any]], Dict[str, Any]]):
        def wrapped(state: Dict[str, Any]) -> Dict[str, Any]:
            if state.get("kaizen_stopped"):
                return {}
            try:
                update = fn(state) or {}
            except Exception as exc:
                record = self.handler.handle_error(exc, dict(state), name)
                return self._apply(state, [record], node=name)
            merged = {**state, **update}
            records = self.handler.evaluate(merged, name)
            self.runlog.record(
                "node_completed",
                run_id=state.get("kaizen_run_id"),
                process=self.config.process_name,
                node=name,
                exceptions=len(records),
                sandbox=self.config.sandbox,
            )
            if records:
                update = {**update, **self._apply(merged, records, node=name)}
            return update

        return wrapped

    def _apply(self, state: Dict[str, Any], records: List[ExceptionRecord], node: str) -> Dict[str, Any]:
        existing = list(state.get("kaizen_exceptions") or [])
        existing.extend({
            "id": r.id,
            "node": r.node,
            "rule": r.rule_name,
            "severity": r.severity.value,
            "sqdip_category": r.sqdip_category,
            "summary": r.summary,
            "ticket_id": r.ticket_id,
        } for r in records)
        update: Dict[str, Any] = {"kaizen_exceptions": existing}
        if self.handler.should_stop(records):
            worst = max(records, key=lambda r: r.severity.rank)
            update["kaizen_stopped"] = True
            update["kaizen_stop_reason"] = f"{node}: {worst.rule_name} — {worst.summary}"
        return update

    def _make_gate(self, next_node: str) -> Callable[[Dict[str, Any]], str]:
        def gate(state: Dict[str, Any]) -> str:
            return ANDON_NODE if state.get("kaizen_stopped") else next_node

        return gate

    def _andon(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """The andon node — the line has stopped; make the problem visible."""
        self.runlog.record(
            "jidoka_stop",
            run_id=state.get("kaizen_run_id"),
            process=self.config.process_name,
            reason=state.get("kaizen_stop_reason", ""),
            sandbox=self.config.sandbox,
        )
        return {}


class _KaizenRunner:
    """Wraps the compiled graph so every invoke is bracketed with run events."""

    def __init__(self, compiled: Any, runlog: RunLog, config: KaizenConfig):
        self._compiled = compiled
        self._runlog = runlog
        self._config = config

    def invoke(self, state: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
        run_id = uuid.uuid4().hex[:12]
        state = {**state, "kaizen_run_id": run_id, "kaizen_stopped": False,
                 "kaizen_exceptions": state.get("kaizen_exceptions", [])}
        self._runlog.record("run_started", run_id=run_id,
                            process=self._config.process_name, sandbox=self._config.sandbox)
        result = self._compiled.invoke(state, **kwargs)
        if result.get("kaizen_stopped"):
            self._runlog.record("run_stopped", run_id=run_id,
                                process=self._config.process_name,
                                reason=result.get("kaizen_stop_reason", ""),
                                sandbox=self._config.sandbox)
        else:
            self._runlog.record("run_completed", run_id=run_id,
                                process=self._config.process_name,
                                sandbox=self._config.sandbox)
        return result

    def __getattr__(self, item: str) -> Any:
        # stream(), get_graph(), etc. pass straight through to the compiled graph
        return getattr(self._compiled, item)
