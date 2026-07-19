"""AI Jidoka Framework — Human-AI Collaborative Kaizen for LangGraph agentic systems.

AI Jidoka is the practice of intelligent human-machine partnership — where AI
and humans work together with shared awareness, perform daily improvement
katas, conduct root cause analysis, and continuously evolve both the automated
process and human standard work.

Author: James Dwan, Catalyst Consulting. MIT licensed.
"""

from __future__ import annotations

from .config import KaizenConfig, load_env
from .runlog import RunLog
from .exception_handler import (
    AbnormalityRule,
    ExceptionHandler,
    ExceptionRecord,
    FiveWhysAnalysis,
    FMEAEntry,
    FMEARegistry,
    Severity,
)
from .kanban_integration import (
    KanbanBoard,
    KanbanTicket,
    ListsKanbanBoard,
    LocalKanbanBoard,
    PlannerKanbanBoard,
    create_board,
)
from .reflection_agent import (
    DEFAULT_MODEL,
    KaizenSummary,
    ReflectionAgent,
    SQDIPSnapshot,
    build_default_llm,
)
from .sensei_agent import SenseiAgent, SenseiReview
from .teammate import KaizenTeammate
from .dashboard import generate_dashboard

__version__ = "0.1.0"

__all__ = [
    "KaizenConfig",
    "load_env",
    "RunLog",
    "AbnormalityRule",
    "ExceptionHandler",
    "ExceptionRecord",
    "FiveWhysAnalysis",
    "FMEAEntry",
    "FMEARegistry",
    "Severity",
    "KanbanBoard",
    "KanbanTicket",
    "LocalKanbanBoard",
    "PlannerKanbanBoard",
    "ListsKanbanBoard",
    "create_board",
    "ReflectionAgent",
    "SQDIPSnapshot",
    "KaizenSummary",
    "DEFAULT_MODEL",
    "build_default_llm",
    "SenseiAgent",
    "SenseiReview",
    "KaizenTeammate",
    "generate_dashboard",
    "KaizenGraphBuilder",
    "KaizenState",
    "ANDON_NODE",
    "InvestigationGraphBuilder",
    "InvestigationState",
    "a3_markdown",
]


def __getattr__(name: str):
    # Graph builders require langgraph; import lazily so config/kanban/
    # reflection tooling stays usable without it.
    if name in ("KaizenGraphBuilder", "KaizenState", "ANDON_NODE"):
        from . import kaizen_graph

        return getattr(kaizen_graph, name)
    if name in ("InvestigationGraphBuilder", "InvestigationState", "a3_markdown"):
        from . import investigation_graph

        return getattr(investigation_graph, name)
    raise AttributeError(f"module 'kaizen' has no attribute {name!r}")
