"""The Reflection Agent — the AI half of the daily Kaizen kata.

Each day it computes an SQDIP snapshot from the run log, summarizes exceptions,
folds in the FMEA top risks, and produces a daily Kaizen summary with concrete
improvement suggestions. The summary is posted to the shared Kanban board so
humans and AI review it together in the daily kata.

If a LangChain chat model is supplied, the narrative section is written by the
LLM using the (business-editable) ``daily_reflection`` prompt from the config.
Without one, a fully deterministic template is used — the loop degrades
gracefully and never depends on model availability.
"""

from __future__ import annotations

import datetime as _dt
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import KaizenConfig
from .exception_handler import FMEARegistry
from .kanban_integration import KanbanBoard, KanbanTicket
from .runlog import RunLog

# Default model for the LLM-written narrative (requires `pip install ai-jidoka-framework[llm]`).
DEFAULT_MODEL = "claude-opus-4-8"


def build_default_llm(model: str = DEFAULT_MODEL):
    """Convenience: a Claude chat model via langchain-anthropic.

    Reads ANTHROPIC_API_KEY from the environment. Any LangChain chat model
    works — pass your own to ReflectionAgent to use a different provider.
    """
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(model=model, max_tokens=4096)


@dataclass
class SQDIPSnapshot:
    day: _dt.date
    safety_incidents: int = 0
    quality_exception_rate: Optional[float] = None  # % of runs with >=1 exception
    delivery_completion_rate: Optional[float] = None  # % of started runs completed
    inventory_open_tickets: Optional[int] = None
    productivity_runs_completed: int = 0
    runs_started: int = 0
    exceptions_total: int = 0
    exceptions_by_category: Dict[str, int] = field(default_factory=dict)
    exceptions_by_rule: Dict[str, int] = field(default_factory=dict)

    def to_markdown(self, targets: Optional[Dict[str, Any]] = None) -> str:
        targets = targets or {}

        def target(name: str) -> str:
            t = targets.get(name, {}).get("target")
            return f" (target: {t})" if t is not None else ""

        def fmt(value: Optional[float], suffix: str = "") -> str:
            return "n/a" if value is None else f"{value}{suffix}"

        return "\n".join([
            "| SQDIP | Today |",
            "|---|---|",
            f"| **S**afety | {self.safety_incidents} incidents{target('safety')} |",
            f"| **Q**uality | {fmt(self.quality_exception_rate, '%')} exception rate{target('quality')} |",
            f"| **D**elivery | {fmt(self.delivery_completion_rate, '%')} runs completed{target('delivery')} |",
            f"| **I**nventory | {fmt(self.inventory_open_tickets)} open tickets{target('inventory')} |",
            f"| **P**roductivity | {self.productivity_runs_completed} runs completed{target('productivity')} |",
        ])


@dataclass
class KaizenSummary:
    day: _dt.date
    process: str
    sqdip: SQDIPSnapshot
    markdown: str
    ticket_id: Optional[str] = None
    report_path: Optional[str] = None


class ReflectionAgent:
    def __init__(
        self,
        config: KaizenConfig,
        runlog: RunLog,
        board: Optional[KanbanBoard] = None,
        llm: Any = None,
        fmea: Optional[FMEARegistry] = None,
        reports_dir: str = "kaizen_reports",
    ):
        self.config = config
        self.runlog = runlog
        self.board = board
        self.llm = llm
        self.fmea = fmea
        self.reports_dir = Path(reports_dir)

    # -- metrics -----------------------------------------------------------

    def compute_sqdip(self, day: _dt.date) -> SQDIPSnapshot:
        events = self.runlog.events(day=day)
        runs_started = sum(1 for e in events if e["type"] == "run_started")
        runs_completed = sum(1 for e in events if e["type"] == "run_completed")
        exceptions = [e for e in events if e["type"] == "exception"]
        runs_with_exceptions = len({e.get("run_id") for e in exceptions if e.get("run_id")})

        snapshot = SQDIPSnapshot(
            day=day,
            safety_incidents=sum(1 for e in exceptions if e.get("sqdip_category") == "safety"),
            runs_started=runs_started,
            productivity_runs_completed=runs_completed,
            exceptions_total=len(exceptions),
            exceptions_by_category=dict(Counter(e.get("sqdip_category", "quality") for e in exceptions)),
            exceptions_by_rule=dict(Counter(e.get("rule", "unknown") for e in exceptions)),
        )
        if runs_started:
            snapshot.quality_exception_rate = round(100.0 * runs_with_exceptions / runs_started, 1)
            snapshot.delivery_completion_rate = round(100.0 * runs_completed / runs_started, 1)
        if self.board is not None:
            # Inventory = open problem-solving work. Daily Kaizen summary posts
            # are agenda items, not WIP, so they don't count against the target.
            kaizen_bucket = self.config.kanban.get("buckets", {}).get("kaizen", "Daily Kaizen")
            snapshot.inventory_open_tickets = sum(
                1 for t in self.board.list_tickets(status="open") if t.bucket != kaizen_bucket
            )
        return snapshot

    # -- the daily reflection ---------------------------------------------

    def daily_reflection(self, day: Optional[_dt.date] = None, post_to_kanban: bool = True) -> KaizenSummary:
        day = day or _dt.datetime.now(_dt.timezone.utc).date()
        sqdip = self.compute_sqdip(day)
        exceptions_md = self._exceptions_markdown(day)
        narrative = self._narrative(sqdip, exceptions_md)

        sections = [
            f"# Daily Kaizen — {self.config.process_name} — {day.isoformat()}",
            "",
            "## SQDIP",
            sqdip.to_markdown(self.config.sqdip_targets),
            "",
            "## Exceptions",
            exceptions_md,
        ]
        if self.fmea and self.fmea.entries:
            sections += ["", "## FMEA — top risks", self.fmea.to_markdown()]
        sections += ["", "## Reflection", narrative, "", "## Today's kata"]
        sections += [f"- {step}" for step in self.config.standard_work.get("daily_kata", [])]
        markdown = "\n".join(sections)

        summary = KaizenSummary(day=day, process=self.config.process_name, sqdip=sqdip, markdown=markdown)

        # Persist the report so the improvement history is browsable.
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        report_path = self.reports_dir / f"kaizen-{day.isoformat()}.md"
        report_path.write_text(markdown, encoding="utf-8")
        summary.report_path = str(report_path)

        if post_to_kanban and self.board and not self.config.sandbox:
            bucket = self.config.kanban.get("buckets", {}).get("kaizen", "Daily Kaizen")
            ticket = self.board.create_ticket(KanbanTicket(
                title=f"Daily Kaizen {day.isoformat()} — {self.config.process_name}",
                description=markdown,
                bucket=bucket,
                labels=["kaizen"],
            ))
            summary.ticket_id = ticket.id
            self.raise_improvement_ideas(sqdip)

        self.runlog.record("daily_reflection", day=day.isoformat(), report=str(report_path))
        return summary

    # -- improvement ideas --------------------------------------------------

    def raise_improvement_ideas(self, sqdip: SQDIPSnapshot) -> List[KanbanTicket]:
        """The AI's own suggestion cards — things it noticed in the process.

        Ideas go to the 'Improvement Ideas' bucket (humans add theirs the same
        way, from the board or Planner). Deduplicated by title so the agent
        never spams the board; the team triages them in the daily kata.
        """
        if self.board is None or self.config.sandbox:
            return []
        bucket = self.config.kanban.get("buckets", {}).get("ideas", "Improvement Ideas")
        existing_titles = {t.title.strip().lower() for t in self.board.list_tickets(bucket=bucket)}
        raised = []
        for title, rationale in self._idea_candidates(sqdip):
            if title.strip().lower() in existing_titles:
                continue
            ticket = self.board.create_ticket(KanbanTicket(
                title=title[:250],
                description=(f"{rationale}\n\n_Raised by the reflection agent — if the team "
                             "agrees it's worth trying, move it to Experiments and trial it "
                             "in sandbox mode first._"),
                bucket=bucket,
                labels=["idea", "ai-raised"],
                priority="low",
            ))
            raised.append(ticket)
            self.runlog.record("idea_raised", ticket_id=ticket.id, title=title, source="ai")
        return raised

    def _idea_candidates(self, sqdip: SQDIPSnapshot) -> List[tuple]:
        """Up to two concrete ideas: LLM-drafted when available, else derived
        deterministically from the most recurrent abnormality."""
        if self.llm is not None:
            prompt = (
                f"You are the reflection agent on a joint human-AI Kaizen team for the process "
                f"'{self.config.process_name}'. Based on this SQDIP snapshot and exception "
                f"pattern, propose at most TWO concrete process-improvement ideas (poka-yokes, "
                f"upstream gates, standard-work changes). Only propose what the data supports.\n\n"
                f"{sqdip.to_markdown(self.config.sqdip_targets)}\n\n"
                f"Exceptions by rule: {sqdip.exceptions_by_rule}\n\n"
                "Respond ONLY with lines in the form 'IDEA: <short title> — <one-sentence "
                "rationale>'. Respond NONE if nothing is warranted."
            )
            try:
                text = getattr(self.llm.invoke(prompt), "content", "")
                ideas = []
                for line in text.splitlines():
                    line = line.strip().lstrip("-* ")
                    if line.upper().startswith("IDEA:") and "—" in line:
                        title, rationale = line[5:].split("—", 1)
                        ideas.append((f"Idea: {title.strip()}", rationale.strip()))
                return ideas[:2]
            except Exception:
                pass  # fall through to the deterministic candidate
        if sqdip.exceptions_by_rule:
            top_rule, count = max(sqdip.exceptions_by_rule.items(), key=lambda kv: kv[1])
            if count >= 2:
                return [(
                    f"Idea: error-proof '{top_rule}' at the source",
                    f"'{top_rule}' occurred {count}x today — recurring abnormalities are "
                    "candidates for a poka-yoke upstream rather than repeated detection.",
                )]
        return []

    # -- internals ---------------------------------------------------------

    def _exceptions_markdown(self, day: _dt.date) -> str:
        events = [e for e in self.runlog.events(day=day) if e["type"] == "exception"]
        if not events:
            return "No exceptions today. Ask: was detection working, or was it genuinely a good day?"
        counts = Counter((e.get("rule", "unknown"), e.get("severity", "?"), e.get("sqdip_category", "?"))
                         for e in events)
        lines = ["| Rule | Severity | SQDIP | Count |", "|---|---|---|---|"]
        for (rule, severity, category), count in counts.most_common():
            lines.append(f"| {rule} | {severity} | {category} | {count} |")
        return "\n".join(lines)

    def _narrative(self, sqdip: SQDIPSnapshot, exceptions_md: str) -> str:
        if self.llm is None:
            return self._template_narrative(sqdip)
        prompt = self.config.prompts.get("daily_reflection", "").format(
            process_name=self.config.process_name,
            sqdip=sqdip.to_markdown(self.config.sqdip_targets),
            exceptions=exceptions_md,
        )
        try:
            response = self.llm.invoke(prompt)
            return getattr(response, "content", str(response))
        except Exception as exc:
            return (f"_LLM reflection unavailable ({exc}); deterministic summary below._\n\n"
                    + self._template_narrative(sqdip))

    def _template_narrative(self, sqdip: SQDIPSnapshot) -> str:
        lines = []
        targets = self.config.sqdip_targets
        q_target = targets.get("quality", {}).get("target")
        if sqdip.exceptions_total == 0:
            lines.append("A clean day — consider whether current rules would actually catch "
                         "the failure modes in the FMEA registry.")
        else:
            top_rule, top_count = max(sqdip.exceptions_by_rule.items(), key=lambda kv: kv[1])
            lines.append(f"Most frequent abnormality: **{top_rule}** ({top_count}x). "
                         "This is the natural candidate for today's 5 Whys.")
            if q_target is not None and sqdip.quality_exception_rate is not None \
                    and sqdip.quality_exception_rate > q_target:
                lines.append(f"Quality is off target ({sqdip.quality_exception_rate}% vs {q_target}%).")
        if sqdip.delivery_completion_rate is not None and sqdip.delivery_completion_rate < 100:
            lines.append("Some runs did not complete — check whether the Jidoka stop was "
                         "appropriate or whether the stop threshold needs tuning.")
        lines.append("Suggested experiment: pick one small change (a rule threshold, a prompt "
                     "wording, one step of human standard work), run it in sandbox mode, and "
                     "compare tomorrow's SQDIP.")
        return "\n\n".join(lines)
