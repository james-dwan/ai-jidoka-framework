"""Proposing, piloting, and approving changes to standard work.

This closes the Kaizen loop. The config is the single **standard-work register**
holding both:

- **agent standard work** — prompts, rules, thresholds, targets
- **human standard work** — ``standard_work`` (the daily kata and practices)

Humans *and* agents may propose changes to either during daily Kaizen. But the
governance is strict, and it extends the framework's existing stance ("the AI
never closes tickets — humans hold the gates"):

    Agents may PROPOSE and PILOT changes to their own standard work and the
    humans'. Only a process OWNER may APPROVE and standardize them.

So an agent can propose rewriting its own prompt, pilot it, and show the owner
the evidence — but it can never self-modify without a human owner's sign-off.

**Safe piloting.** A candidate change is applied to a *copy* of the config; for
metric-affecting changes (the stop threshold, a rule's severity, a target) the
recorded run log is replayed as a what-if to show the before/after — e.g. "under
'high' there were 6 line-stops last week; under the proposed 'medium', there'd
be 12." Text changes (prompts, kata steps) are reviewed directly. Config
versioning gives free rollback.
"""

from __future__ import annotations

import copy
import datetime as _dt
import json
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import KaizenConfig
from .exception_handler import Severity
from .kanban_integration import KanbanBoard, KanbanTicket
from .runlog import RunLog
from .targets import MeasureTarget


# --------------------------------------------------------------------------
# Walking a config path (supports dict keys and list-of-dict selectors)
# --------------------------------------------------------------------------

def _walk(data: Any, path: List[Any]) -> Any:
    node = data
    for key in path:
        if isinstance(key, dict) and "name" in key:
            node = next(x for x in node if x.get("name") == key["name"])
        else:
            node = node[key]
    return node


def get_at(data: Dict[str, Any], path: List[Any]) -> Any:
    return _walk(data, path)


def set_at(data: Dict[str, Any], path: List[Any], value: Any) -> None:
    parent = _walk(data, path[:-1]) if len(path) > 1 else data
    parent[path[-1]] = value


# --------------------------------------------------------------------------
# The change proposal
# --------------------------------------------------------------------------

@dataclass
class ChangeProposal:
    """A proposed change to one item of standard work."""

    title: str
    path: List[Any]                 # config path, e.g. ["jidoka", "stop_on_severity"]
    new_value: Any
    rationale: str = ""
    proposed_by: str = "agent:teammate"   # "agent:..." or "human:<name>"
    old_value: Any = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: str = "proposed"        # proposed | piloted | approved | rejected
    created_at: str = field(
        default_factory=lambda: _dt.datetime.now(_dt.timezone.utc).isoformat()
    )
    pilot: Optional[Dict[str, Any]] = None
    approver: Optional[str] = None
    decided_at: Optional[str] = None
    reject_reason: Optional[str] = None
    resulting_version: Optional[int] = None

    @property
    def register(self) -> str:
        """Which standard work this touches: 'human' or 'agent'."""
        return "human" if self.path and self.path[0] == "standard_work" else "agent"

    @property
    def by_agent(self) -> bool:
        return self.proposed_by.startswith("agent")

    def apply_to(self, config_data: Dict[str, Any]) -> Dict[str, Any]:
        """Return a deep copy of the config with this change applied."""
        candidate = copy.deepcopy(config_data)
        set_at(candidate, self.path, self.new_value)
        return candidate

    def to_card(self, bucket: str = "Experiments", assignee: Optional[str] = None) -> KanbanTicket:
        lines = [
            f"**Proposed by:** {self.proposed_by}  \n"
            f"**Standard work:** {self.register} ({_path_label(self.path)})",
            f"**Change:** `{self.old_value!r}` → `{self.new_value!r}`",
        ]
        if self.rationale:
            lines.append(f"**Rationale:** {self.rationale}")
        if self.pilot:
            lines.append("**Pilot:**\n" + self.pilot.get("summary", ""))
        lines.append(
            "_Agents may propose and pilot; only the process owner approves. "
            "Approving updates the standard work (config) and versions it; "
            "rollback is a previous file in `config_history/`._"
        )
        return KanbanTicket(
            title=f"Proposed change: {self.title}"[:250],
            description="\n\n".join(lines),
            bucket=bucket,
            labels=["proposal", self.register, "ai-raised" if self.by_agent else "human-raised"],
            priority="low",
            assignee=assignee,   # the decision is the owner's: on Planner this
                                 # puts the card in their own task list
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _path_label(path: List[Any]) -> str:
    parts = [p["name"] if isinstance(p, dict) else str(p) for p in path]
    return ".".join(parts)


# --------------------------------------------------------------------------
# Piloting — replay the recorded run log as a what-if
# --------------------------------------------------------------------------

def pilot(proposal: ChangeProposal, config: KaizenConfig, runlog: RunLog) -> Dict[str, Any]:
    """Compare the current standard against the candidate over recorded history.

    Metric-affecting changes get a real before/after from the run log; text
    changes (prompts, kata) are flagged for direct human review.
    """
    events = runlog.events()
    current = config.data
    candidate = proposal.apply_to(current)

    if proposal.path == ["jidoka", "stop_on_severity"] or _is_rule_severity(proposal.path):
        before = _stop_count(events, current)
        after = _stop_count(events, candidate)
        verb = "more" if after > before else "fewer" if after < before else "the same number of"
        return {
            "metric_based": True,
            "before": {"line_stops": before},
            "after": {"line_stops": after},
            "summary": (f"Over {_period(events)}, the current standard produced **{before}** "
                        f"line-stop(s); the proposed standard would produce **{after}** "
                        f"— {abs(after - before)} {verb}."),
        }

    if proposal.path and proposal.path[0] == "targets" and len(proposal.path) >= 2:
        return _pilot_target(proposal, events, current, candidate)

    return {
        "metric_based": False,
        "summary": ("No automatic metric for this change — review the before/after text "
                    "directly, then pilot in sandbox if it affects the running process."),
    }


def _is_rule_severity(path: List[Any]) -> bool:
    return (len(path) == 3 and path[0] == "rules"
            and isinstance(path[1], dict) and path[2] == "severity")


def _stop_count(events: List[Dict[str, Any]], config_data: Dict[str, Any]) -> int:
    """How many recorded defects would stop the line under this config."""
    stop_rank = Severity(config_data.get("jidoka", {}).get("stop_on_severity", "high")).rank
    rule_sev = {r["name"]: r.get("severity", "medium") for r in config_data.get("rules", [])}
    count = 0
    for e in events:
        if e.get("type") != "exception":
            continue
        sev = rule_sev.get(e.get("rule"), e.get("severity", "medium"))
        if Severity(sev).rank >= stop_rank:
            count += 1
    return count


def _pilot_target(proposal, events, current, candidate) -> Dict[str, Any]:
    name = proposal.path[1].get("name")
    before_spec = next((t for t in current.get("targets", []) if t.get("name") == name), None)
    after_spec = next((t for t in candidate.get("targets", []) if t.get("name") == name), None)
    if not before_spec or not after_spec:
        return {"metric_based": False, "summary": "Target not found for replay."}
    period = _period(events)
    before = MeasureTarget.from_dict(before_spec).evaluate(events, period, current["process"]["name"])
    after = MeasureTarget.from_dict(after_spec).evaluate(events, period, candidate["process"]["name"])
    return {
        "metric_based": True,
        "before": {"missed": before.missed, "target": before.target},
        "after": {"missed": after.missed, "target": after.target},
        "summary": (f"Actual over {period}: {int(before.actual)}. "
                    f"Current target {before._sym()}{before.target} → "
                    f"{'MISSED' if before.missed else 'met'}; "
                    f"proposed target {after._sym()}{after.target} → "
                    f"{'MISSED' if after.missed else 'met'}."),
    }


def _period(events: List[Dict[str, Any]]) -> str:
    days = sorted({e.get("timestamp", "")[:10] for e in events if e.get("timestamp")})
    if not days:
        return "the recorded history"
    return days[0] if len(days) == 1 else f"{days[0]} to {days[-1]}"


# --------------------------------------------------------------------------
# The registry — propose, pilot, and the owner-gated approval
# --------------------------------------------------------------------------

class ProposalRegistry:
    """JSON-backed store of change proposals, plus the approval gate.

    Agents reach ``propose`` and ``pilot``. Only ``approve`` mutates the
    standard work, and it requires a human process owner — there is no
    agent-approve path.
    """

    def __init__(self, config: KaizenConfig, runlog: Optional[RunLog] = None,
                 board: Optional[KanbanBoard] = None, path: str = "kaizen_proposals.json"):
        self.config = config
        self.runlog = runlog or RunLog()
        self.board = board
        self.path = Path(path)

    # -- persistence -------------------------------------------------------

    def _load(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        with open(self.path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def _save(self, records: List[Dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(records, fh, indent=2, ensure_ascii=False, default=str)

    def list(self) -> List[ChangeProposal]:
        return [ChangeProposal(**r) for r in self._load()]

    def get(self, proposal_id: str) -> Optional[ChangeProposal]:
        return next((p for p in self.list() if p.id == proposal_id), None)

    def _upsert(self, proposal: ChangeProposal) -> None:
        records = [r for r in self._load() if r["id"] != proposal.id]
        records.append(proposal.to_dict())
        self._save(records)

    # -- propose (human or agent) -----------------------------------------

    def propose(self, title: str, path: List[Any], new_value: Any, rationale: str = "",
                proposed_by: str = "agent:teammate") -> ChangeProposal:
        proposal = ChangeProposal(
            title=title, path=path, new_value=new_value, rationale=rationale,
            proposed_by=proposed_by, old_value=get_at(self.config.data, path),
        )
        self._upsert(proposal)
        self.runlog.record("proposal_raised", proposal_id=proposal.id, title=title,
                           register=proposal.register, proposed_by=proposed_by)
        if self.board and not self.config.sandbox:
            bucket = self.config.kanban.get("buckets", {}).get("experiments", "Experiments")
            # Assign the card to the process owner — the decision is theirs.
            # On Planner, `owner_user_id` (an Azure AD object id) makes it a
            # real assignment in their Teams/Planner view; locally the owner's
            # name is stored on the card.
            assignee = self.config.kanban.get("owner_user_id") or self.config.process_owner or None
            self.board.create_ticket(proposal.to_card(bucket, assignee=assignee))
        return proposal

    # -- pilot (safe, evidence-gathering) ---------------------------------

    def pilot(self, proposal_id: str) -> ChangeProposal:
        proposal = self._require(proposal_id)
        proposal.pilot = pilot(proposal, self.config, self.runlog)
        proposal.status = "piloted"
        self._upsert(proposal)
        self.runlog.record("proposal_piloted", proposal_id=proposal.id,
                           metric_based=proposal.pilot.get("metric_based"))
        return proposal

    # -- approve / reject (process owner only) ----------------------------

    def approve(self, proposal_id: str, owner: str) -> ChangeProposal:
        """Approve and standardize — updates the config and versions it.

        ``owner`` must be a human process owner; an agent cannot approve.
        """
        if not owner or owner.startswith("agent"):
            raise PermissionError("Only a human process owner may approve a change to standard work.")
        allowed = self.config.data.get("process_owner")
        if allowed and owner != allowed:
            raise PermissionError(f"'{owner}' is not the process owner ('{allowed}').")

        proposal = self._require(proposal_id)
        set_at(self.config.data, proposal.path, proposal.new_value)
        self.config.save()  # bumps version, archives the previous standard
        proposal.status = "approved"
        proposal.approver = owner
        proposal.decided_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
        proposal.resulting_version = self.config.data.get("version")
        self._upsert(proposal)
        self.runlog.record("proposal_approved", proposal_id=proposal.id, owner=owner,
                           version=proposal.resulting_version, register=proposal.register)
        return proposal

    def reject(self, proposal_id: str, owner: str, reason: str = "") -> ChangeProposal:
        proposal = self._require(proposal_id)
        proposal.status = "rejected"
        proposal.approver = owner
        proposal.reject_reason = reason
        proposal.decided_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
        self._upsert(proposal)
        self.runlog.record("proposal_rejected", proposal_id=proposal.id, owner=owner, reason=reason)
        return proposal

    def _require(self, proposal_id: str) -> ChangeProposal:
        proposal = self.get(proposal_id)
        if proposal is None:
            raise KeyError(f"No proposal {proposal_id!r}")
        return proposal
