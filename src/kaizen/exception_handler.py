"""Jidoka exception management.

Jidoka means the process stops itself when an abnormality occurs and makes the
problem visible instead of passing defects downstream. Here that translates to:

1. Business-editable abnormality *rules* evaluated against workflow state
2. Structured :class:`ExceptionRecord` objects with a 5 Whys scaffold attached
3. Automatic Kanban ticket creation so the problem is visible to the whole team
4. A stop decision ("pull the andon cord") based on configurable severity

FMEA support lives here too: teams maintain a registry of anticipated failure
modes, and the Reflection Agent uses it to suggest proactive countermeasures.
"""

from __future__ import annotations

import datetime as _dt
import traceback
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

import yaml

from .kanban_integration import KanbanBoard, KanbanTicket, rule_from_title
from .runlog import RunLog


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return ["low", "medium", "high", "critical"].index(self.value)


SQDIP_CATEGORIES = ("safety", "quality", "delivery", "inventory", "productivity")


# --------------------------------------------------------------------------
# Abnormality rules (business-editable via YAML config)
# --------------------------------------------------------------------------

@dataclass
class AbnormalityRule:
    """One abnormality check.

    ``condition`` is either a Python callable ``state -> bool`` or a string
    expression evaluated with ``state`` in scope, e.g.::

        condition: "state.get('invoice_total', 0) > 10000"

    String conditions come from the trusted config file (they are standard
    work, reviewed like code); no user input is ever evaluated.
    """

    name: str
    condition: Union[str, Callable[[Dict[str, Any]], bool]]
    severity: Severity = Severity.MEDIUM
    sqdip_category: str = "quality"
    description: str = ""
    countermeasure_hint: str = ""
    nodes: Optional[List[str]] = None  # restrict to specific nodes; None = all

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "AbnormalityRule":
        return cls(
            name=raw["name"],
            condition=raw["condition"],
            severity=Severity(raw.get("severity", "medium")),
            sqdip_category=raw.get("sqdip_category", "quality"),
            description=raw.get("description", ""),
            countermeasure_hint=raw.get("countermeasure_hint", ""),
            nodes=raw.get("nodes"),
        )

    def applies_to(self, node: str) -> bool:
        return self.nodes is None or node in self.nodes

    def check(self, state: Dict[str, Any]) -> bool:
        """Return True when the state is ABNORMAL."""
        if callable(self.condition):
            return bool(self.condition(state))
        return bool(eval(self.condition, {"__builtins__": _SAFE_BUILTINS}, {"state": state}))  # noqa: S307


#: The only builtins available to string rule conditions.
_SAFE_BUILTINS: Dict[str, Any] = {
    name: obj for name, obj in {
        "len": len, "sum": sum, "min": min, "max": max, "abs": abs,
        "round": round, "any": any, "all": all, "sorted": sorted,
        "set": set, "str": str, "int": int, "float": float, "bool": bool,
    }.items()
}


# --------------------------------------------------------------------------
# 5 Whys
# --------------------------------------------------------------------------

@dataclass
class FiveWhysAnalysis:
    problem: str
    whys: List[str] = field(default_factory=list)
    root_cause: str = ""
    countermeasure: str = ""

    def to_markdown(self) -> str:
        lines = [f"**Problem:** {self.problem}", "", "**5 Whys:**"]
        for i in range(5):
            answer = self.whys[i] if i < len(self.whys) else "_(to be answered together)_"
            lines.append(f"{i + 1}. Why? — {answer}")
        lines += [
            "",
            f"**Root cause:** {self.root_cause or '_(agree during the daily kata)_'}",
            f"**Countermeasure:** {self.countermeasure or '_(agree during the daily kata)_'}",
        ]
        return "\n".join(lines)


# --------------------------------------------------------------------------
# FMEA
# --------------------------------------------------------------------------

@dataclass
class FMEAEntry:
    failure_mode: str
    effect: str = ""
    cause: str = ""
    severity: int = 5      # 1-10
    occurrence: int = 5    # 1-10
    detection: int = 5     # 1-10 (10 = hardest to detect)
    recommended_action: str = ""
    poka_yoke: str = ""    # error-proofing measure, if implemented

    @property
    def rpn(self) -> int:
        """Risk Priority Number = severity x occurrence x detection."""
        return self.severity * self.occurrence * self.detection


class FMEARegistry:
    """YAML-backed registry of anticipated failure modes."""

    def __init__(self, entries: Optional[List[FMEAEntry]] = None, path: Optional[Path] = None):
        self.entries = entries or []
        self.path = path

    @classmethod
    def load(cls, path: str) -> "FMEARegistry":
        p = Path(path)
        if not p.exists():
            return cls(path=p)
        with open(p, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or []
        return cls([FMEAEntry(**e) for e in raw], path=p)

    def save(self, path: Optional[str] = None) -> None:
        target = Path(path) if path else self.path
        if target is None:
            raise ValueError("No path for FMEA registry.")
        with open(target, "w", encoding="utf-8") as fh:
            yaml.safe_dump([asdict(e) for e in self.entries], fh, sort_keys=False)

    def top_risks(self, n: int = 5) -> List[FMEAEntry]:
        return sorted(self.entries, key=lambda e: e.rpn, reverse=True)[:n]

    def to_markdown(self, n: int = 5) -> str:
        rows = ["| Failure mode | RPN | Recommended action |", "|---|---|---|"]
        for e in self.top_risks(n):
            rows.append(f"| {e.failure_mode} | {e.rpn} | {e.recommended_action or '-'} |")
        return "\n".join(rows)


# --------------------------------------------------------------------------
# Exception records + handler
# --------------------------------------------------------------------------

@dataclass
class ExceptionRecord:
    process: str
    node: str
    rule_name: str
    severity: Severity
    sqdip_category: str
    summary: str
    details: str = ""
    countermeasure_hint: str = ""
    state_snapshot: Dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: str = field(
        default_factory=lambda: _dt.datetime.now(_dt.timezone.utc).isoformat()
    )
    five_whys: Optional[FiveWhysAnalysis] = None
    ticket_id: Optional[str] = None

    def to_ticket(self, bucket: str = "Problems") -> KanbanTicket:
        five_whys = self.five_whys or FiveWhysAnalysis(problem=self.summary)
        description = "\n\n".join(
            part for part in [
                f"**Process:** {self.process}  \n**Node:** {self.node}  \n"
                f"**Severity:** {self.severity.value}  \n**SQDIP:** {self.sqdip_category}",
                self.details,
                f"**Suggested starting point:** {self.countermeasure_hint}" if self.countermeasure_hint else "",
                "---",
                five_whys.to_markdown(),
            ] if part
        )
        priority = {"low": "low", "medium": "medium", "high": "high", "critical": "urgent"}[self.severity.value]
        return KanbanTicket(
            title=f"[{self.severity.value.upper()}] {self.rule_name}: {self.summary}"[:250],
            description=description,
            bucket=bucket,
            labels=[self.sqdip_category, self.process],
            priority=priority,
            checklist=[
                "Confirm the abnormality (go and see)",
                "Complete the 5 Whys together",
                "Agree on a countermeasure and owner",
                "Update standard work / rules / prompts",
                "Verify the countermeasure worked",
            ],
        )


class ExceptionHandler:
    """Detects abnormalities, records them, raises tickets, and decides stops."""

    def __init__(
        self,
        process_name: str,
        rules: Optional[List[AbnormalityRule]] = None,
        board: Optional[KanbanBoard] = None,
        runlog: Optional[RunLog] = None,
        stop_on_severity: Union[str, Severity] = Severity.HIGH,
        sandbox: bool = False,
        problem_bucket: str = "Problems",
        tickets_on_stop: bool = True,
    ):
        self.process_name = process_name
        self.rules = rules or []
        self.board = board
        self.runlog = runlog
        self.stop_on_severity = Severity(stop_on_severity)
        self.sandbox = sandbox
        self.problem_bucket = problem_bucket
        self.tickets_on_stop = tickets_on_stop

    # -- detection ---------------------------------------------------------

    def evaluate(self, state: Dict[str, Any], node: str) -> List[ExceptionRecord]:
        """Run all applicable rules against the state after a node executes."""
        records = []
        for rule in self.rules:
            if not rule.applies_to(node):
                continue
            try:
                abnormal = rule.check(state)
            except Exception as exc:  # a broken rule is itself an abnormality
                records.append(self._record(
                    node=node,
                    rule_name=f"rule-error:{rule.name}",
                    severity=Severity.MEDIUM,
                    sqdip_category="quality",
                    summary=f"Rule '{rule.name}' failed to evaluate: {exc}",
                    state=state,
                ))
                continue
            if abnormal:
                records.append(self._record(
                    node=node,
                    rule_name=rule.name,
                    severity=rule.severity,
                    sqdip_category=rule.sqdip_category,
                    summary=rule.description or f"Abnormality detected by rule '{rule.name}'",
                    countermeasure_hint=rule.countermeasure_hint,
                    state=state,
                ))
        return records

    def handle_error(self, exc: Exception, state: Dict[str, Any], node: str) -> ExceptionRecord:
        """An uncaught exception in a node is always a HIGH severity abnormality."""
        return self._record(
            node=node,
            rule_name="uncaught-exception",
            severity=Severity.HIGH,
            sqdip_category="quality",
            summary=f"{type(exc).__name__}: {exc}",
            details=f"```\n{traceback.format_exc()}\n```",
            state=state,
        )

    def should_stop(self, records: List[ExceptionRecord]) -> bool:
        """The andon-cord decision: stop the line at/above the configured severity."""
        return any(r.severity.rank >= self.stop_on_severity.rank for r in records)

    # -- internals ---------------------------------------------------------

    def _record(
        self,
        node: str,
        rule_name: str,
        severity: Severity,
        sqdip_category: str,
        summary: str,
        state: Dict[str, Any],
        details: str = "",
        countermeasure_hint: str = "",
    ) -> ExceptionRecord:
        record = ExceptionRecord(
            process=self.process_name,
            node=node,
            rule_name=rule_name,
            severity=severity,
            sqdip_category=sqdip_category,
            summary=summary,
            details=details,
            countermeasure_hint=countermeasure_hint,
            state_snapshot=_safe_snapshot(state),
            five_whys=FiveWhysAnalysis(problem=summary),
        )
        if self.runlog:
            self.runlog.record(
                "exception",
                exception_id=record.id,
                run_id=state.get("kaizen_run_id"),
                process=record.process,
                node=node,
                rule=rule_name,
                severity=severity.value,
                sqdip_category=sqdip_category,
                summary=summary,
                sandbox=self.sandbox,
            )
        # Defects are counted (run log), not auto-carded. A card is raised only
        # when this abnormality STOPS the line — a genuine andon event that
        # halts the process and needs action now. Everything below the stop
        # threshold is just counted; the daily review turns missed *targets*
        # into problem cards (see ReflectionAgent.raise_target_miss_cards).
        stops_the_line = severity.rank >= self.stop_on_severity.rank
        if self.board and not self.sandbox and self.tickets_on_stop and stops_the_line:
            # One card per stop PATTERN: recurrences of the same rule append to
            # the existing open card rather than creating duplicates.
            existing = self._open_ticket_for_rule(rule_name)
            if existing is not None:
                stamp = record.timestamp[:16].replace("T", " ")
                self.board.update_ticket(
                    existing.id,
                    description=existing.description
                    + f"\n\n**Occurrence ({stamp} UTC):** {node} — {summary}",
                )
                record.ticket_id = existing.id
            else:
                ticket = self.board.create_ticket(record.to_ticket(self.problem_bucket))
                record.ticket_id = ticket.id
        return record

    def _open_ticket_for_rule(self, rule_name: str) -> Optional[KanbanTicket]:
        for ticket in self.board.list_tickets(bucket=self.problem_bucket):
            if ticket.status != "done" and rule_from_title(ticket.title) == rule_name:
                return ticket
        return None


def _safe_snapshot(state: Dict[str, Any], max_len: int = 500) -> Dict[str, Any]:
    """A JSON-safe, truncated copy of state for tickets and logs."""
    snapshot = {}
    for key, value in state.items():
        if key.startswith("kaizen_"):
            continue
        text = repr(value)
        snapshot[key] = text if len(text) <= max_len else text[:max_len] + "...(truncated)"
    return snapshot
