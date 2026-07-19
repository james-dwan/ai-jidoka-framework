"""The Kaizen Teammate — an agent that works the board autonomously.

This is the heart of AI Jidoka as partnership: the agent doesn't wait to be
summoned. It watches the shared Kanban board and advances every open exception
ticket as far as the *evidence* allows:

- completes the problem statement from the run log and rule configuration
- fills in the 5 Whys where evidence supports a cause — and leaves a why OPEN
  where only a human could know (observations at the process, business context)
- has its own analysis reviewed by the Sensei *before* posting (an
  agent-to-agent kata — the AI's thinking is gated the same way the humans' is)
- writes precise **questions to the team** into the ticket when it is blocked
- picks up the team's answers (ticket notes/comments, description edits) on
  its next pass and continues
- proposes a countermeasure and pilot when the chain is sound — but **never
  closes a ticket**: verifying the pilot and dragging the ticket to Done is
  the humans' non-optional gate

The only channel is the board itself. Everything the teammate does here works
identically against Microsoft Planner — it reads and writes tickets, nothing
more — so the collaboration feels the same in Teams as it does locally.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from .config import KaizenConfig
from .exception_handler import FiveWhysAnalysis
from .kanban_integration import KanbanBoard, KanbanTicket, rule_from_title
from .runlog import RunLog
from .sensei_agent import SenseiAgent


def _with_pilot(analysis: FiveWhysAnalysis, pilot: str) -> FiveWhysAnalysis:
    """For sensei review: the pilot is part of the countermeasure's verification story."""
    if not pilot:
        return analysis
    return FiveWhysAnalysis(
        problem=analysis.problem,
        whys=list(analysis.whys),
        root_cause=analysis.root_cause,
        countermeasure=f"{analysis.countermeasure} Pilot: {pilot}",
    )

AGENT_MARKER = "<!-- kaizen-teammate"
_NOTE_RE = re.compile(r"^\*\*Note \([^)]+\):\*\*", re.MULTILINE)


class KaizenTeammate:
    def __init__(
        self,
        config: KaizenConfig,
        board: KanbanBoard,
        runlog: Optional[RunLog] = None,
        llm: Any = None,
        sensei: Optional[SenseiAgent] = None,
    ):
        self.config = config
        self.board = board
        self.runlog = runlog or RunLog()
        self.llm = llm
        self.sensei = sensei or SenseiAgent(config, llm=llm)

    # ------------------------------------------------------------------
    # The loop
    # ------------------------------------------------------------------

    def watch(self, interval: float = 15.0) -> None:
        """Work the board forever (Ctrl-C to stop)."""
        print(f"Kaizen teammate watching the board every {interval:.0f}s (Ctrl-C to stop)")
        while True:
            try:
                worked = self.work_board()
                if worked:
                    print(f"[teammate] updated {worked} ticket(s)")
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                print(f"[teammate] pass failed: {exc}")
            time.sleep(interval)

    def work_board(self) -> int:
        """One pass: advance every open/in-progress exception ticket whose
        human-authored content changed since the last pass."""
        bucket = self.config.kanban.get("buckets", {}).get("exceptions", "Exceptions")
        worked = 0
        for ticket in self.board.list_tickets(bucket=bucket):
            if ticket.status == "done":
                continue
            if self.work_ticket(ticket):
                worked += 1
        return worked

    # ------------------------------------------------------------------
    # Working one ticket
    # ------------------------------------------------------------------

    def work_ticket(self, ticket: KanbanTicket) -> bool:
        header, notes = _split_description(ticket.description)
        rev = _rev(header, notes)
        if f"rev:{rev}" in ticket.description:
            return False  # nothing new from the humans since the last pass

        if self.llm is None:
            # Without an LLM the teammate still reviews socratically.
            return self.sensei.coach_ticket(self.board, ticket, recoach=True)

        analysis, pilot, questions = self._investigate(ticket, header, notes)

        # The sensei gates the agent's own thinking — but only once there IS a
        # complete proposal to gate. A partial analysis waiting on team input
        # is blocked on knowledge, not on thinking quality.
        review = None
        complete = bool(analysis.root_cause and analysis.countermeasure and not questions)
        if complete:
            review = self.sensei.review(_with_pilot(analysis, pilot))
            if not review.ready:
                analysis, pilot, questions = self._investigate(
                    ticket, header, notes, sensei_feedback=review.questions
                )
                complete = bool(analysis.root_cause and analysis.countermeasure and not questions)
                review = self.sensei.review(_with_pilot(analysis, pilot)) if complete else None

        description = self._compose(header, notes, analysis, pilot, questions, review, rev)
        changes: Dict[str, Any] = {"description": description}
        if ticket.status == "open":
            changes["status"] = "in_progress"  # visible: the agent is on it
        self.board.update_ticket(ticket.id, **changes)

        self.runlog.record(
            "teammate_update",
            ticket_id=ticket.id,
            rule=rule_from_title(ticket.title),
            questions_for_team=len(questions),
            proposal_ready=complete and (review is None or review.ready),
        )
        return True

    # ------------------------------------------------------------------
    # Investigation (LLM) — evidence in, structured analysis out
    # ------------------------------------------------------------------

    def _investigate(
        self,
        ticket: KanbanTicket,
        header: str,
        notes: List[str],
        sensei_feedback: Optional[List[str]] = None,
    ) -> Tuple[FiveWhysAnalysis, str, List[str]]:
        prompt = self._prompt(ticket, header, notes, sensei_feedback)
        response = self.llm.invoke(prompt)
        text = getattr(response, "content", str(response))
        return _parse_response(text)

    def _prompt(self, ticket, header, notes, sensei_feedback) -> str:
        rule_name = rule_from_title(ticket.title)
        rule = next((r for r in self.config.rules if r.get("name") == rule_name), {})
        evidence = self._evidence(rule_name)
        notes_text = "\n".join(f"- {n}" for n in notes) or "- (none yet)"
        feedback = ""
        if sensei_feedback:
            feedback = ("\nThe team's sensei reviewed your draft and asks:\n"
                        + "\n".join(f"- {q}" for q in sensei_feedback)
                        + "\nRevise your analysis to address these.\n")
        return f"""You are the Kaizen investigator agent on a joint human-AI improvement team \
for the process '{self.config.process_name}'. You collaborate with the humans THROUGH this \
Kanban ticket: complete as much of the root cause analysis as the evidence supports, and ask \
the team only for what you genuinely cannot know (observations at the actual process, business \
context, decisions). Never invent facts. Blame processes, never people.

TICKET:
{header}

RULE CONFIGURATION:
- condition: {rule.get('condition', 'unknown')}
- severity: {rule.get('severity', 'unknown')} | SQDIP: {rule.get('sqdip_category', 'unknown')}
- countermeasure hint from standard work: {rule.get('countermeasure_hint', '(none)')}

EVIDENCE FROM THE RUN LOG:
{evidence}

TEAM NOTES ON THIS TICKET (the humans' answers and observations, oldest first):
{notes_text}
{feedback}
Respond in EXACTLY this format, nothing else. Use OPEN for anything the evidence does not \
support yet. Ask at most 3 questions, each precise enough that a one-sentence answer unblocks you.

PROBLEM: <specific, measurable, blame-free problem statement>
WHY1: <cause, or OPEN>
WHY2: <cause, or OPEN>
WHY3: <cause, or OPEN>
WHY4: <cause, or OPEN>
WHY5: <cause, or OPEN>
ROOT: <root cause, or OPEN>
COUNTERMEASURE: <process/tool change proposal, or OPEN>
PILOT: <smallest safe experiment (sandbox mode exists), or OPEN>
QUESTION: <question for the team — omit these lines entirely if you have none>
"""

    def _evidence(self, rule_name: str) -> str:
        events = self.runlog.events()
        exceptions = [e for e in events if e.get("type") == "exception"]
        pareto = Counter(e.get("rule", "unknown") for e in exceptions)
        mine = [e for e in exceptions if e.get("rule") == rule_name]
        runs = sum(1 for e in events if e.get("type") == "run_started")
        lines = [
            f"- {len(mine)} occurrence(s) of '{rule_name}' across {runs} run(s); "
            f"all exception rules by frequency: {dict(pareto.most_common())}",
        ]
        for e in mine[-3:]:
            lines.append(f"- occurrence at node '{e.get('node')}': {e.get('summary')} "
                         f"(sandbox={e.get('sandbox', False)})")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Composing the ticket back
    # ------------------------------------------------------------------

    def _compose(self, header, notes, analysis, pilot, questions, review, rev) -> str:
        parts = [header.rstrip(), "---", AGENT_MARKER + f" rev:{rev} -->", analysis.to_markdown()]
        if pilot:
            parts.append(f"**Pilot:** {pilot}")
        if questions:
            parts.append("**Needs from the team** _(add a note/comment to this ticket — "
                         "I'll pick it up on my next pass)_:\n"
                         + "\n".join(f"- [ ] {q}" for q in questions))
        if review is not None and review.ready:
            parts.append("**Proposal ready for team review.** If you agree, pilot it "
                         "(sandbox mode is available) and drag this ticket to Done once "
                         "the result is verified. I will not close it myself.")
        elif review is not None and review.questions:
            parts.append("**Sensei still asks:**\n" + "\n".join(f"- {q}" for q in review.questions))
        if notes:
            parts.append("\n".join(notes))
        return "\n\n".join(parts)


# ----------------------------------------------------------------------
# Parsing helpers
# ----------------------------------------------------------------------

def _split_description(description: str) -> Tuple[str, List[str]]:
    """Header (immutable ticket context) + the humans' notes, discarding any
    previous agent-authored sections (they are regenerated each pass)."""
    notes = [p.strip() for p in description.split("\n\n") if _NOTE_RE.match(p.strip())]
    if AGENT_MARKER in description:
        header = description.split("\n\n---\n\n" + AGENT_MARKER)[0]
        header = header.split("---\n\n" + AGENT_MARKER)[0]
    else:
        header = description.split("\n\n---\n\n")[0]
    return header.strip(), notes


def _rev(header: str, notes: List[str]) -> str:
    digest = hashlib.sha1(("\n".join([header, *notes])).encode()).hexdigest()
    return digest[:10]


def _parse_response(text: str) -> Tuple[FiveWhysAnalysis, str, List[str]]:
    fields: Dict[str, str] = {}
    questions: List[str] = []
    for line in text.splitlines():
        line = line.strip().lstrip("-* ")
        match = re.match(r"(PROBLEM|WHY[1-5]|ROOT|COUNTERMEASURE|PILOT|QUESTION)\s*:\s*(.*)", line)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        if key == "QUESTION":
            if value and value.upper() != "OPEN":
                questions.append(value)
        else:
            fields[key] = "" if value.upper() == "OPEN" else value
    whys = [fields.get(f"WHY{i}", "") for i in range(1, 6)]
    whys = [w for w in whys if w]
    return (
        FiveWhysAnalysis(
            problem=fields.get("PROBLEM", ""),
            whys=whys,
            root_cause=fields.get("ROOT", ""),
            countermeasure=fields.get("COUNTERMEASURE", ""),
        ),
        fields.get("PILOT", ""),
        questions[:3],
    )
