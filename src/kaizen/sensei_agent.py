"""The Sensei Agent — a socratic coach for root cause analysis.

A Lean sensei never gives the answer; they ask the question that makes the
team see the gap themselves. This agent reviews 5 Whys analyses (from
exception tickets or directly) and raises socratic questions when the analysis
shows classic anti-patterns:

- vague, unmeasurable problem statements
- causal chains that stop at a symptom
- "blame the person" instead of "interrogate the process"
- weak countermeasures (reminders, training, "be more careful")
- root causes that merely restate the problem

Like the Reflection Agent, it works deterministically out of the box; give it
a LangChain chat model and the questions become richer and more contextual.

This is the first of what may become a roster of specialist kata agents
(problem-statement writer, Pareto analyst, fishbone facilitator, pilot
designer, team communicator). The Sensei comes first because it protects the
quality of the thinking the others depend on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, List, Optional

from .config import KaizenConfig
from .exception_handler import FiveWhysAnalysis
from .kanban_integration import KanbanBoard


# Words that suggest the analysis blamed a person instead of the process.
_BLAME_PATTERN = re.compile(
    r"\b(careless|carelessness|lazy|forgot|human error|didn'?t pay attention|"
    r"wasn'?t paying attention|should have known|incompetent|negligen\w+)\b",
    re.IGNORECASE,
)

# Countermeasures that fix nothing structural.
_WEAK_COUNTERMEASURE_PATTERN = re.compile(
    r"\b(remind|reminder|re-?train|training|be (more )?careful|pay (more )?attention|"
    r"try harder|tell (him|her|them|people)|awareness)\b",
    re.IGNORECASE,
)

_VAGUE_PROBLEM_WORDS = re.compile(
    r"\b(sometimes|often|occasionally|issues?|problems?|stuff|things|bad|slow|broken)\b",
    re.IGNORECASE,
)


@dataclass
class SenseiReview:
    """The sensei's questions about one analysis. Questions, never answers."""

    problem: str
    questions: List[str] = field(default_factory=list)
    ready: bool = False  # True when the analysis looks solid enough to act on

    def to_markdown(self) -> str:
        if self.ready:
            return ("**Sensei:** This analysis looks ready to act on. Before you do: "
                    "how will you verify the countermeasure worked, and by when?")
        lines = ["**Sensei questions** _(answer these together before choosing a countermeasure)_:", ""]
        lines += [f"- {q}" for q in self.questions]
        return "\n".join(lines)


class SenseiAgent:
    def __init__(self, config: KaizenConfig, llm: Any = None):
        self.config = config
        self.llm = llm

    # -- reviewing a single analysis ---------------------------------------

    def review(self, analysis: FiveWhysAnalysis, require_countermeasure: bool = True) -> SenseiReview:
        """Review an analysis. Set ``require_countermeasure=False`` when coaching
        mid-investigation, before the countermeasure stage has been reached."""
        review = SenseiReview(problem=analysis.problem)
        review.questions = self._heuristic_questions(analysis, require_countermeasure)
        if self.llm is not None:
            review.questions.extend(self._llm_questions(analysis))
        review.ready = not review.questions
        return review

    # -- coaching the board -------------------------------------------------

    #: Marks where the sensei's section begins in a ticket description.
    SECTION_MARKER = "\n\n---\n\n**Sensei"

    def coach_open_exceptions(self, board: KanbanBoard, bucket: Optional[str] = None,
                              recoach: bool = False) -> int:
        """Append sensei questions to open exception tickets.

        With ``recoach=True``, tickets that were already coached are reviewed
        again against their *current* description — so after humans answer the
        questions in the ticket, the sensei responds to the updated analysis.
        Returns the number of tickets coached this pass.
        """
        bucket = bucket or self.config.kanban.get("buckets", {}).get("problems", "Problems")
        coached = 0
        for ticket in board.list_tickets(bucket=bucket, status="open"):
            if self.coach_ticket(board, ticket, recoach=recoach):
                coached += 1
        return coached

    def coach_ticket(self, board: KanbanBoard, ticket, recoach: bool = True) -> bool:
        """Review one ticket's analysis and (re)write the sensei section of its
        description. Returns True if the ticket was coached."""
        if "**Sensei" in ticket.description and not recoach:
            return False
        # Review the human-authored part only; replace any prior sensei section.
        body = ticket.description.split(self.SECTION_MARKER)[0].rstrip()
        review = self.review(_parse_five_whys(body))
        board.update_ticket(ticket.id, description=body + "\n\n---\n\n" + review.to_markdown())
        return True

    # -- internals ----------------------------------------------------------

    def _heuristic_questions(self, analysis: FiveWhysAnalysis,
                             require_countermeasure: bool = True) -> List[str]:
        questions: List[str] = []
        answered = [w for w in analysis.whys if w and not w.startswith("_(")]

        if _VAGUE_PROBLEM_WORDS.search(analysis.problem) or len(analysis.problem.split()) < 4:
            questions.append(
                "Is the problem statement specific and measurable? What exactly happened, "
                "where, when, and how big is the gap versus the standard?"
            )
        if not answered:
            questions.append(
                "The 5 Whys haven't been started. Go and see first — what did you observe "
                "at the actual place the work happens?"
            )
        elif len(answered) < 3:
            questions.append(
                f"You stopped after {len(answered)} why(s). If you fixed "
                "this cause, could the same problem still recur through another path?"
            )

        full_text = " ".join([analysis.problem, *answered, analysis.root_cause])
        if _BLAME_PATTERN.search(full_text):
            questions.append(
                "The analysis points at a person. What in the *process* allowed a normal "
                "human action to become a defect? What would make this mistake impossible "
                "(poka-yoke) rather than merely discouraged?"
            )
        if analysis.root_cause and analysis.root_cause.strip().lower() in analysis.problem.strip().lower():
            questions.append(
                "The stated root cause restates the problem. Keep asking why — what "
                "condition upstream produced it?"
            )
        if analysis.countermeasure and _WEAK_COUNTERMEASURE_PATTERN.search(analysis.countermeasure):
            questions.append(
                "Reminders and training fade. What change to the process, the tool, or the "
                "standard work would remove the cause even on a bad day?"
            )
        if require_countermeasure and answered and not analysis.countermeasure:
            questions.append(
                "You have a causal chain but no countermeasure. What is the smallest "
                "experiment that would test whether removing this cause prevents the problem?"
            )
        if analysis.countermeasure and not re.search(
                r"verif|measur|compar|monitor|check", analysis.countermeasure, re.IGNORECASE):
            questions.append(
                "How will you check the countermeasure actually worked — what will you "
                "measure, and when will you look?"
            )
        return questions

    def _llm_questions(self, analysis: FiveWhysAnalysis) -> List[str]:
        prompt = self.config.prompts.get("sensei", DEFAULT_SENSEI_PROMPT).format(
            process_name=self.config.process_name,
            analysis=analysis.to_markdown(),
        )
        try:
            response = self.llm.invoke(prompt)
            text = getattr(response, "content", str(response))
        except Exception:
            return []
        if text.strip().upper().startswith("READY"):
            return []
        questions = [line.lstrip("-* ").strip() for line in text.splitlines()
                     if line.strip().startswith(("-", "*"))]
        return [q for q in questions if q][:3]


DEFAULT_SENSEI_PROMPT = (
    "You are a Lean sensei coaching a joint human-AI team working on the process "
    "'{process_name}'. Review this root cause analysis:\n\n{analysis}\n\n"
    "If the analysis is genuinely solid — a specific, measurable problem statement, "
    "a causal chain where each why follows from the last and ends at a controllable "
    "process cause, no blame on individuals — respond with the single word READY.\n"
    "Otherwise respond ONLY with up to three socratic questions as markdown bullet "
    "points. Never give answers, diagnoses, or countermeasures — only questions that "
    "expose gaps in the causal chain, missing evidence (would a Pareto of the data "
    "support this focus?), or blame masquerading as a root cause."
)


def _parse_five_whys(description: str) -> FiveWhysAnalysis:
    """Best-effort extraction of the 5 Whys scaffold from a ticket description."""
    problem_match = re.search(r"\*\*Problem:\*\*\s*(.+)", description)
    root_match = re.search(r"\*\*Root cause:\*\*\s*(.+)", description)
    counter_match = re.search(r"\*\*Countermeasure:\*\*\s*(.+)", description)
    whys = re.findall(r"^\d\.\s*Why\?\s*—\s*(.+)$", description, re.MULTILINE)

    def clean(match: Optional[re.Match]) -> str:
        if not match:
            return ""
        value = match.group(1).strip()
        return "" if value.startswith("_(") else value

    problem = clean(problem_match)
    if not problem and description:
        problem = description.splitlines()[0][:200]
    return FiveWhysAnalysis(
        problem=problem,
        whys=[w for w in whys if not w.startswith("_(")],
        root_cause=clean(root_match),
        countermeasure=clean(counter_match),
    )
