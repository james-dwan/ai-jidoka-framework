"""Targets, and the problem cards a *missed target* creates.

A defect on its own is data: it is recorded in the run log and counted, but it
never automatically becomes a Kanban card. A call centre with thousands of
calls might log 20-30 defects a day — you want to *know* that, not drown the
board in 30 tickets.

What the team writes a card about is a **missed target**. At the daily review,
each measure is compared against its target, and one card is raised for each
target that was missed, with a problem statement framed as the gap to target:

    "On 20 July, 30 out of 1000 calls to the Acme helpdesk had customer
     complaints, against the target of <20."

That keeps the board a workspace for the vital few problems the team has chosen
to solve, and ties it to the metrics the team already reviews. This is the
same shape whether the board is a local JSON file or Microsoft Planner.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .exception_handler import FiveWhysAnalysis
from .kanban_integration import KanbanTicket


def _num(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


@dataclass
class TargetResult:
    """The outcome of comparing one measure against its target for a period."""

    name: str
    actual: float
    target: float
    direction: str          # "below" (lower is good) | "above" (higher is good)
    period_label: str       # e.g. "20 July"
    process: str
    description: str = ""    # noun phrase for the statement, e.g. "customer complaints"
    unit: str = ""           # e.g. "%" for rates; "" for counts
    volume: Optional[int] = None    # denominator for context, e.g. total calls
    volume_unit: str = "runs"       # e.g. "calls to the Acme helpdesk"

    @property
    def missed(self) -> bool:
        return self.actual > self.target if self.direction == "below" else self.actual < self.target

    @property
    def marker(self) -> str:
        # Embedded in the card so the same target+period is never carded twice.
        return f"<!-- target-miss:{self.name}:{self.period_label} -->"

    def _sym(self) -> str:
        return "<" if self.direction == "below" else ">"

    def problem_statement(self) -> str:
        target_txt = f"{self._sym()}{_num(self.target)}{self.unit}"
        if self.volume is not None:
            measure = self.description or self.name
            return (f"On {self.period_label}, {_num(self.actual)} out of {self.volume} "
                    f"{self.volume_unit} had {measure}, against the target of {target_txt}.")
        measure = self.description or f"{self.name} for {self.process}"
        return (f"On {self.period_label}, {measure} was {_num(self.actual)}{self.unit}, "
                f"against the target of {target_txt}.")

    def to_ticket(self, bucket: str) -> KanbanTicket:
        scaffold = FiveWhysAnalysis(problem=self.problem_statement()).to_markdown()
        description = (
            f"{self.marker}\n\n{scaffold}\n\n"
            "_Raised because a target was missed. Work the 5 Whys with the team, "
            "agree a countermeasure, and trial it in sandbox before standardizing._"
        )
        gap = f"{_num(self.actual)}{self.unit} vs {self._sym()}{_num(self.target)}{self.unit}"
        return KanbanTicket(
            title=f"Target missed: {self.name} — {gap} ({self.period_label})"[:250],
            description=description,
            bucket=bucket,
            labels=["target-miss", self.name],
            priority="high",
            checklist=[
                "Confirm the figures against the run log / source data",
                "Complete the 5 Whys together",
                "Agree a countermeasure and owner",
                "Trial in sandbox, then standardize",
                "Verify the target is back on track",
            ],
        )


@dataclass
class MeasureTarget:
    """A config-defined target on a specific measure — the Acme-complaints case.

    ``rule`` names an abnormality rule; the actual is how many times it occurred
    in the period. ``volume_from`` gives the denominator for the problem
    statement (e.g. total runs = total calls).
    """

    name: str
    target: float
    direction: str = "below"
    description: str = ""
    rule: Optional[str] = None
    unit: str = ""
    volume_from: Optional[str] = None   # "runs" -> count of run_started events
    volume_unit: str = "runs"

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MeasureTarget":
        return cls(
            name=d["name"],
            target=d["target"],
            direction=d.get("direction", "below"),
            description=d.get("description", ""),
            rule=d.get("rule"),
            unit=d.get("unit", ""),
            volume_from=d.get("volume_from"),
            volume_unit=d.get("volume_unit", "runs"),
        )

    def evaluate(self, events: List[Dict[str, Any]], period_label: str, process: str) -> TargetResult:
        actual = 0
        if self.rule is not None:
            actual = sum(1 for e in events
                         if e.get("type") == "exception" and e.get("rule") == self.rule)
        volume = None
        if self.volume_from == "runs":
            volume = sum(1 for e in events if e.get("type") == "run_started")
        return TargetResult(
            name=self.name,
            actual=actual,
            target=self.target,
            direction=self.direction,
            period_label=period_label,
            process=process,
            description=self.description,
            unit=self.unit,
            volume=volume,
            volume_unit=self.volume_unit,
        )


#: Direction of "good" for each SQDIP metric (below = lower is better).
SQDIP_DIRECTION = {
    "safety": "below",
    "quality": "below",
    "delivery": "above",
    "inventory": "below",
    "productivity": "above",
}
