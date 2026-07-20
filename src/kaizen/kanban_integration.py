"""Shared Kanban integration — the collaboration surface where humans and AI meet.

The framework treats the Kanban board as the joint problem-solving workspace:
the AI creates structured tickets for exceptions and daily Kaizen summaries;
humans (and the AI) update them together.

Three providers are included:

- ``LocalKanbanBoard``   — zero-config JSON file board (default; great for dev/sandbox)
- ``PlannerKanbanBoard`` — Microsoft Planner via Microsoft Graph
- ``ListsKanbanBoard``   — Microsoft Lists (SharePoint list) via Microsoft Graph

Implement :class:`KanbanBoard` to plug in anything else (Trello, Jira, GitHub
Projects, ...).
"""

from __future__ import annotations

import datetime as _dt
import json
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


@dataclass
class KanbanTicket:
    title: str
    description: str = ""
    bucket: str = "Problems"
    labels: List[str] = field(default_factory=list)
    priority: str = "medium"  # low | medium | high | urgent
    checklist: List[str] = field(default_factory=list)
    due: Optional[str] = None  # ISO date
    assignee: Optional[str] = None  # who owns the card (Planner: AAD user id -> assignment)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: str = "open"  # open | in_progress | done
    created_at: str = field(
        default_factory=lambda: _dt.datetime.now(_dt.timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class KanbanBoard(ABC):
    """Minimal contract every Kanban provider must satisfy."""

    @abstractmethod
    def create_ticket(self, ticket: KanbanTicket) -> KanbanTicket:
        ...

    @abstractmethod
    def update_ticket(self, ticket_id: str, **changes: Any) -> None:
        ...

    @abstractmethod
    def list_tickets(self, bucket: Optional[str] = None, status: Optional[str] = None) -> List[KanbanTicket]:
        ...

    def open_ticket_count(self) -> int:
        """Used as the SQDIP 'Inventory' metric."""
        return len(self.list_tickets(status="open"))


class LocalKanbanBoard(KanbanBoard):
    """A JSON-file board. No credentials, no setup — ideal for development,
    sandbox experiments, and teams not on Microsoft 365."""

    def __init__(self, path: str = "kaizen_board.json"):
        self.path = Path(path)

    def _load(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        with open(self.path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def _save(self, tickets: List[Dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(tickets, fh, indent=2, ensure_ascii=False)

    def create_ticket(self, ticket: KanbanTicket) -> KanbanTicket:
        tickets = self._load()
        tickets.append(ticket.to_dict())
        self._save(tickets)
        return ticket

    def update_ticket(self, ticket_id: str, **changes: Any) -> None:
        tickets = self._load()
        for t in tickets:
            if t["id"] == ticket_id:
                t.update(changes)
        self._save(tickets)

    def list_tickets(self, bucket: Optional[str] = None, status: Optional[str] = None) -> List[KanbanTicket]:
        result = []
        for t in self._load():
            if bucket and t.get("bucket") != bucket:
                continue
            if status and t.get("status") != status:
                continue
            result.append(KanbanTicket(**t))
        return result


class _GraphBoard(KanbanBoard):
    """Shared plumbing for Microsoft Graph-backed boards.

    ``token_provider`` is any zero-argument callable returning a valid Graph
    access token — e.g. ``lambda: credential.get_token(scope).token`` with an
    ``azure.identity`` credential. Keeping auth outside the framework lets each
    organization use whatever flow (device code, client secret, managed
    identity) their tenant requires.
    """

    GRAPH = "https://graph.microsoft.com/v1.0"

    def __init__(self, token_provider: Callable[[], str]):
        self._token_provider = token_provider

    def _request(self, method: str, url: str, payload: Optional[dict] = None,
                 etag: Optional[str] = None) -> dict:
        """All Graph traffic goes through here (tests fake this one seam).

        ``etag`` sends ``If-Match`` — Graph requires it on every PATCH so a
        concurrent edit (a human in the Planner UI) fails loudly instead of
        being silently overwritten.
        """
        import requests  # optional dependency: pip install ai-kaizen-framework[m365]

        headers = {
            "Authorization": f"Bearer {self._token_provider()}",
            "Content-Type": "application/json",
        }
        if etag:
            headers["If-Match"] = etag
        response = requests.request(method, url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        return response.json() if response.content else {}


#: KanbanTicket priority <-> Planner's 0-10 priority bands.
_PRIORITY_TO_PLANNER = {"urgent": 1, "high": 3, "medium": 5, "low": 9}


def _planner_priority_to_label(value: int) -> str:
    if value <= 1:
        return "urgent"
    if value <= 4:
        return "high"
    if value <= 7:
        return "medium"
    return "low"


class PlannerKanbanBoard(_GraphBoard):
    """Microsoft Planner board via Microsoft Graph — the production surface.

    ``bucket_ids`` maps friendly bucket names (as used in the config, e.g.
    "Problems", "Daily Kaizen") to Planner bucket IDs.

    Mapping notes:

    - ``status`` <-> ``percentComplete`` (0 / 50 / 100) — Planner's "Group by
      progress" columns, so a human dragging a task IS the status change
    - ``description``/``checklist`` live on task *details* (a second resource
      with its own etag); ``list_tickets`` fetches them per task by default —
      one extra Graph call per task, so agents polling large plans should keep
      the pass interval in minutes, not seconds
    - ``assignee`` (an Azure AD user id) <-> a Planner assignment — this is how
      the process owner gets a proposal card in their own Planner/Teams view
    - every PATCH carries the current etag (``If-Match``), so a concurrent
      human edit in the Planner UI surfaces as a 409/412 rather than being
      silently clobbered; agents just retry on their next pass
    """

    def __init__(self, plan_id: str, bucket_ids: Dict[str, str],
                 token_provider: Callable[[], str]):
        super().__init__(token_provider)
        self.plan_id = plan_id
        self.bucket_ids = bucket_ids

    # -- create ------------------------------------------------------------

    def create_ticket(self, ticket: KanbanTicket) -> KanbanTicket:
        bucket_id = self.bucket_ids.get(ticket.bucket)
        if bucket_id is None:
            raise KeyError(f"No Planner bucket ID configured for bucket '{ticket.bucket}'")
        payload: Dict[str, Any] = {
            "planId": self.plan_id,
            "bucketId": bucket_id,
            "title": ticket.title,
            "priority": _PRIORITY_TO_PLANNER.get(ticket.priority, 5),
        }
        if ticket.due:
            payload["dueDateTime"] = f"{ticket.due}T00:00:00Z"
        if ticket.assignee:
            payload["assignments"] = {
                ticket.assignee: {"@odata.type": "#microsoft.graph.plannerAssignment",
                                  "orderHint": " !"},
            }
        task = self._request("POST", f"{self.GRAPH}/planner/tasks", payload)
        ticket.id = task["id"]
        if ticket.description or ticket.checklist:
            self._patch_description(ticket.id, ticket.description, ticket.checklist)
        return ticket

    # -- update ------------------------------------------------------------

    def update_ticket(self, ticket_id: str, **changes: Any) -> None:
        payload: Dict[str, Any] = {}
        if "status" in changes:
            payload["percentComplete"] = {"open": 0, "in_progress": 50, "done": 100}[changes["status"]]
        if "title" in changes:
            payload["title"] = changes["title"]
        if "bucket" in changes:
            bucket_id = self.bucket_ids.get(changes["bucket"])
            if bucket_id is None:
                raise KeyError(f"No Planner bucket ID configured for bucket '{changes['bucket']}'")
            payload["bucketId"] = bucket_id
        if "priority" in changes:
            payload["priority"] = _PRIORITY_TO_PLANNER.get(changes["priority"], 5)
        if "assignee" in changes and changes["assignee"]:
            payload["assignments"] = {
                changes["assignee"]: {"@odata.type": "#microsoft.graph.plannerAssignment",
                                      "orderHint": " !"},
            }
        if payload:
            task = self._request("GET", f"{self.GRAPH}/planner/tasks/{ticket_id}")
            self._request("PATCH", f"{self.GRAPH}/planner/tasks/{ticket_id}",
                          payload, etag=task["@odata.etag"])
        if "description" in changes:
            self._patch_description(ticket_id, changes["description"], None)

    def _patch_description(self, task_id: str, description: Optional[str],
                           checklist: Optional[List[str]]) -> None:
        # Description and checklist live on the task's *details* resource,
        # which has its own etag independent of the task's.
        details = self._request("GET", f"{self.GRAPH}/planner/tasks/{task_id}/details")
        payload: Dict[str, Any] = {}
        if description is not None:
            payload["description"] = description
        if checklist:
            payload["checklist"] = {
                uuid.uuid4().hex: {"@odata.type": "microsoft.graph.plannerChecklistItem",
                                   "title": item, "isChecked": False}
                for item in checklist
            }
        self._request("PATCH", f"{self.GRAPH}/planner/tasks/{task_id}/details",
                      payload, etag=details["@odata.etag"])

    # -- read --------------------------------------------------------------

    def list_tickets(self, bucket: Optional[str] = None, status: Optional[str] = None,
                     include_details: bool = True) -> List[KanbanTicket]:
        tasks = self._request("GET", f"{self.GRAPH}/planner/plans/{self.plan_id}/tasks").get("value", [])
        id_to_bucket = {v: k for k, v in self.bucket_ids.items()}
        result = []
        for task in tasks:
            assignments = task.get("assignments") or {}
            t = KanbanTicket(
                title=task["title"],
                bucket=id_to_bucket.get(task.get("bucketId"), task.get("bucketId", "")),
                id=task["id"],
                status={0: "open", 100: "done"}.get(task.get("percentComplete", 0), "in_progress"),
                priority=_planner_priority_to_label(task.get("priority", 5)),
                assignee=next(iter(assignments), None),
            )
            if bucket and t.bucket != bucket:
                continue
            if status and t.status != status:
                continue
            if include_details:
                # The agents' whole collaboration loop reads/writes the
                # description, so details are fetched by default. Pass
                # include_details=False for cheap counts (e.g. Inventory).
                details = self._request("GET", f"{self.GRAPH}/planner/tasks/{t.id}/details")
                t.description = details.get("description") or ""
                t.checklist = [item.get("title", "")
                               for item in (details.get("checklist") or {}).values()]
            result.append(t)
        return result

    def open_ticket_count(self) -> int:
        # Counting doesn't need descriptions — skip the per-task details calls.
        return len(self.list_tickets(status="open", include_details=False))


class ListsKanbanBoard(_GraphBoard):
    """Microsoft Lists (SharePoint list) via Microsoft Graph.

    Expects a list with at least Title, Description, Bucket, Priority, and
    Status text columns.
    """

    def __init__(self, site_id: str, list_id: str, token_provider: Callable[[], str]):
        super().__init__(token_provider)
        self.site_id = site_id
        self.list_id = list_id

    @property
    def _items_url(self) -> str:
        return f"{self.GRAPH}/sites/{self.site_id}/lists/{self.list_id}/items"

    def create_ticket(self, ticket: KanbanTicket) -> KanbanTicket:
        item = self._request(
            "POST",
            self._items_url,
            {"fields": {
                "Title": ticket.title,
                "Description": ticket.description,
                "Bucket": ticket.bucket,
                "Priority": ticket.priority,
                "Status": ticket.status,
            }},
        )
        ticket.id = item["id"]
        return ticket

    def update_ticket(self, ticket_id: str, **changes: Any) -> None:
        fields = {k.capitalize(): v for k, v in changes.items()}
        self._request("PATCH", f"{self._items_url}/{ticket_id}/fields", fields)

    def list_tickets(self, bucket: Optional[str] = None, status: Optional[str] = None) -> List[KanbanTicket]:
        items = self._request("GET", f"{self._items_url}?expand=fields").get("value", [])
        result = []
        for item in items:
            f = item.get("fields", {})
            t = KanbanTicket(
                title=f.get("Title", ""),
                description=f.get("Description", ""),
                bucket=f.get("Bucket", ""),
                priority=f.get("Priority", "medium"),
                status=f.get("Status", "open"),
                id=item["id"],
            )
            if bucket and t.bucket != bucket:
                continue
            if status and t.status != status:
                continue
            result.append(t)
        return result


def rule_from_title(title: str) -> str:
    """Exception tickets are titled '[SEVERITY] rule-name: summary'."""
    if "]" in title and ":" in title:
        return title.split("]", 1)[1].split(":", 1)[0].strip()
    return ""


def create_board(kanban_config: Dict[str, Any],
                 token_provider: Optional[Callable[[], str]] = None) -> KanbanBoard:
    """Build a board from the ``kanban`` section of the config."""
    provider = kanban_config.get("provider", "local")
    if provider == "local":
        return LocalKanbanBoard(kanban_config.get("board_path", "kaizen_board.json"))
    if token_provider is None:
        raise ValueError(f"Kanban provider '{provider}' requires a token_provider.")
    if provider == "planner":
        return PlannerKanbanBoard(
            plan_id=kanban_config["plan_id"],
            bucket_ids=kanban_config["bucket_ids"],
            token_provider=token_provider,
        )
    if provider == "lists":
        return ListsKanbanBoard(
            site_id=kanban_config["site_id"],
            list_id=kanban_config["list_id"],
            token_provider=token_provider,
        )
    raise ValueError(f"Unknown Kanban provider: {provider!r}")
