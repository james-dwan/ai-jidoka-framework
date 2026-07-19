"""Smoke tests for the AI Jidoka Framework core loop."""

from __future__ import annotations

from typing import List, TypedDict

import pytest

from kaizen import (
    AbnormalityRule,
    KaizenConfig,
    KaizenGraphBuilder,
    KanbanTicket,
    LocalKanbanBoard,
    ReflectionAgent,
    RunLog,
    Severity,
)
from kaizen.kaizen_graph import KaizenState


def make_config(tmp_path, rules=None, stop_on="high", sandbox=False) -> KaizenConfig:
    config = KaizenConfig.default()
    config.data["process"]["name"] = "test-process"
    config.data["rules"] = rules or []
    config.data["jidoka"]["stop_on_severity"] = stop_on
    config.data["sandbox"] = sandbox
    config.data["kanban"]["board_path"] = str(tmp_path / "board.json")
    return config


class State(KaizenState, TypedDict, total=False):
    value: int
    doubled: int


def double(state):
    return {"doubled": state["value"] * 2}


def build(tmp_path, config):
    builder = KaizenGraphBuilder(State, config, runlog=RunLog(str(tmp_path / "log.jsonl")))
    builder.add_node("double", double)
    builder.set_entry_point("double")
    builder.set_finish_point("double")
    return builder.compile()


def test_clean_run_completes(tmp_path):
    graph = build(tmp_path, make_config(tmp_path))
    result = graph.invoke({"value": 3})
    assert result["doubled"] == 6
    assert result["kaizen_stopped"] is False
    assert result["kaizen_exceptions"] == []


def test_rule_triggers_stop_and_ticket(tmp_path):
    rules = [{
        "name": "too-big",
        "condition": "state.get('doubled', 0) > 5",
        "severity": "high",
        "sqdip_category": "quality",
        "description": "Doubled value exceeded the limit",
    }]
    config = make_config(tmp_path, rules=rules)
    graph = build(tmp_path, config)
    result = graph.invoke({"value": 10})
    assert result["kaizen_stopped"] is True
    assert "too-big" in result["kaizen_stop_reason"]
    board = LocalKanbanBoard(config.data["kanban"]["board_path"])
    tickets = board.list_tickets(bucket="Exceptions")
    assert len(tickets) == 1
    assert "5 Whys" in tickets[0].description


def test_low_severity_does_not_stop(tmp_path):
    rules = [{"name": "warn", "condition": "True", "severity": "low"}]
    graph = build(tmp_path, make_config(tmp_path, rules=rules))
    result = graph.invoke({"value": 1})
    assert result["kaizen_stopped"] is False
    assert len(result["kaizen_exceptions"]) == 1


def test_uncaught_error_becomes_high_severity_stop(tmp_path):
    config = make_config(tmp_path)
    builder = KaizenGraphBuilder(State, config, runlog=RunLog(str(tmp_path / "log.jsonl")))
    builder.add_node("boom", lambda state: 1 / 0)
    builder.set_entry_point("boom")
    builder.set_finish_point("boom")
    result = builder.compile().invoke({"value": 1})
    assert result["kaizen_stopped"] is True
    assert result["kaizen_exceptions"][0]["rule"] == "uncaught-exception"
    assert result["kaizen_exceptions"][0]["severity"] == Severity.HIGH.value


def test_sandbox_creates_no_tickets(tmp_path):
    rules = [{"name": "always", "condition": "True", "severity": "high"}]
    config = make_config(tmp_path, rules=rules, sandbox=True)
    graph = build(tmp_path, config)
    result = graph.invoke({"value": 1})
    assert result["kaizen_stopped"] is True  # the stop still happens in sandbox
    assert LocalKanbanBoard(config.data["kanban"]["board_path"]).list_tickets() == []


def test_config_save_bumps_version_and_archives(tmp_path):
    path = tmp_path / "config.yaml"
    config = KaizenConfig.default()
    config.save(str(path))
    assert config.data["version"] == 1
    config.save()
    assert config.data["version"] == 2
    archived = list((tmp_path / "config_history").glob("*.yaml"))
    assert len(archived) == 1


def test_reflection_computes_sqdip_and_posts(tmp_path):
    rules = [{"name": "always", "condition": "True", "severity": "low"}]
    config = make_config(tmp_path, rules=rules)
    runlog = RunLog(str(tmp_path / "log.jsonl"))
    builder = KaizenGraphBuilder(State, config, runlog=runlog)
    builder.add_node("double", double)
    builder.set_entry_point("double")
    builder.set_finish_point("double")
    graph = builder.compile()
    graph.invoke({"value": 1})
    graph.invoke({"value": 2})

    board = LocalKanbanBoard(config.data["kanban"]["board_path"])
    agent = ReflectionAgent(config, runlog, board=board, reports_dir=str(tmp_path / "reports"))
    summary = agent.daily_reflection()
    assert summary.sqdip.runs_started == 2
    assert summary.sqdip.productivity_runs_completed == 2
    assert summary.sqdip.quality_exception_rate == 100.0
    assert summary.ticket_id is not None
    assert (tmp_path / "reports" / f"kaizen-{summary.day.isoformat()}.md").exists()


def test_callable_rule_condition(tmp_path):
    rule = AbnormalityRule(name="fn", condition=lambda s: s["value"] < 0)
    assert rule.check({"value": -1}) is True
    assert rule.check({"value": 1}) is False


# -- Sensei Agent ----------------------------------------------------------

from kaizen import FiveWhysAnalysis, SenseiAgent  # noqa: E402


def test_sensei_questions_blame_and_weak_countermeasures(tmp_path):
    sensei = SenseiAgent(make_config(tmp_path))
    analysis = FiveWhysAnalysis(
        problem="Invoice was wrong",
        whys=["The consultant was careless with the timesheet"],
        root_cause="Human error",
        countermeasure="Remind everyone to be more careful",
    )
    review = sensei.review(analysis)
    assert review.ready is False
    text = " ".join(review.questions)
    assert "process" in text          # blame -> process question
    assert "poka-yoke" in text
    assert "Reminders and training fade" in text


def test_sensei_accepts_solid_analysis(tmp_path):
    sensei = SenseiAgent(make_config(tmp_path))
    analysis = FiveWhysAnalysis(
        problem="Invoice INV-2026-07 overstated Contoso hours by 12.5 on 2026-07-15",
        whys=[
            "The timesheet export duplicated the final week",
            "The export job ran twice on the cutoff day",
            "The scheduler retries on timeout without checking for a prior success",
            "The job has no idempotency key",
            "The integration was built before the retry policy was introduced",
        ],
        root_cause="Export job is not idempotent under the current retry policy",
        countermeasure="Add an idempotency key per period; verify by replaying July with forced retries",
    )
    review = sensei.review(analysis)
    assert review.ready is True
    assert "ready to act on" in review.to_markdown()


def test_sensei_coaches_open_tickets(tmp_path):
    rules = [{"name": "always", "condition": "True", "severity": "high",
              "description": "Something abnormal happened in the run"}]
    config = make_config(tmp_path, rules=rules)
    graph = build(tmp_path, config)
    graph.invoke({"value": 1})

    board = LocalKanbanBoard(config.data["kanban"]["board_path"])
    sensei = SenseiAgent(config)
    assert sensei.coach_open_exceptions(board) == 1
    ticket = board.list_tickets(bucket="Exceptions")[0]
    assert "**Sensei" in ticket.description
    # Second pass is idempotent — already-coached tickets are skipped.
    assert sensei.coach_open_exceptions(board) == 0


# -- Investigation flow ----------------------------------------------------

from langgraph.types import Command  # noqa: E402

from kaizen import InvestigationGraphBuilder  # noqa: E402


def test_investigation_flow_end_to_end_with_sensei_gate(tmp_path):
    # 1. Produce an open exception ticket via a real run.
    rules = [{"name": "too-big", "condition": "state.get('doubled', 0) > 5",
              "severity": "high", "description": "Doubled value exceeded the limit"}]
    config = make_config(tmp_path, rules=rules)
    graph = build(tmp_path, config)
    graph.invoke({"value": 10})

    board = LocalKanbanBoard(config.data["kanban"]["board_path"])
    ticket = board.list_tickets(bucket="Exceptions", status="open")[0]

    # 2. Drive the investigation through every human gate.
    builder = InvestigationGraphBuilder(config, board, runlog=RunLog(str(tmp_path / "log.jsonl")))
    flow = builder.build()
    thread = {"configurable": {"thread_id": ticket.id}}

    def stage(state):
        return state["__interrupt__"][0].value["stage"]

    state = flow.invoke(builder.start_input(ticket.id), thread)
    assert stage(state) == "frame_problem"
    state = flow.invoke(Command(resume=(
        "Run 2026-07-19 doubled the value to 20 against a limit of 5 in the double node"
    )), thread)
    assert stage(state) == "collect_data"
    state = flow.invoke(Command(resume="Observed: input value arrived pre-doubled from upstream"), thread)
    assert stage(state) == "brainstorm_causes"
    state = flow.invoke(Command(resume="Process: upstream feed doubles values\nData: no schema check"), thread)
    assert stage(state) == "five_whys"

    # Weak analysis: blame + short chain -> the sensei must bounce it back.
    state = flow.invoke(Command(resume="The operator was careless\nHuman error"), thread)
    assert stage(state) == "five_whys"
    assert state["__interrupt__"][0].value["sensei_questions"]

    # Solid chain -> the gate opens.
    state = flow.invoke(Command(resume="\n".join([
        "The doubled value exceeded the limit",
        "The input value was already doubled upstream",
        "The upstream feed changed units last week",
        "No contract test exists between the feed and this process",
        "The integration predates the team's contract-testing standard",
        "Upstream feed has no contract test enforcing units",
    ])), thread)
    assert stage(state) == "design_countermeasure"

    state = flow.invoke(Command(resume=(
        "countermeasure: add a contract test on the upstream feed units\n"
        "pilot: run the feed in sandbox with the contract test for one week"
    )), thread)
    assert stage(state) == "verify"
    state = flow.invoke(Command(resume="yes: one week in sandbox, zero unit mismatches"), thread)

    # 3. Complete: A3 written back, ticket closed, run log updated.
    assert state["status"] == "standardized"
    assert state["verified"] is True
    updated = [t for t in board.list_tickets() if t.id == ticket.id][0]
    assert updated.status == "done"
    assert "## 4. Causal chain (5 Whys)" in updated.description
    events = RunLog(str(tmp_path / "log.jsonl")).events()
    assert any(e["type"] == "investigation_completed" for e in events)


def test_sensei_override_after_max_rounds(tmp_path):
    from kaizen.investigation_graph import MAX_SENSEI_ROUNDS

    rules = [{"name": "always", "condition": "True", "severity": "high"}]
    config = make_config(tmp_path, rules=rules)
    graph = build(tmp_path, config)
    graph.invoke({"value": 1})
    board = LocalKanbanBoard(config.data["kanban"]["board_path"])
    ticket = board.list_tickets(bucket="Exceptions", status="open")[0]

    builder = InvestigationGraphBuilder(config, board, runlog=RunLog(str(tmp_path / "log.jsonl")))
    flow = builder.build()
    thread = {"configurable": {"thread_id": ticket.id}}

    state = flow.invoke(builder.start_input(ticket.id), thread)
    state = flow.invoke(Command(resume="Something vague happened"), thread)   # frame
    state = flow.invoke(Command(resume="ok"), thread)                          # data
    state = flow.invoke(Command(resume="ok"), thread)                          # fishbone
    # Keep submitting a weak analysis until the override gate appears.
    for _ in range(MAX_SENSEI_ROUNDS):
        state = flow.invoke(Command(resume="Human error\nCarelessness"), thread)
    assert state["__interrupt__"][0].value["stage"] == "sensei_override"
    state = flow.invoke(Command(resume="proceed"), thread)                     # explicit override
    assert state["__interrupt__"][0].value["stage"] == "design_countermeasure"
    state = flow.invoke(Command(resume="countermeasure: x\npilot: y"), thread)
    state = flow.invoke(Command(resume="no: recurred immediately"), thread)
    assert state["status"] == "in_progress"     # unverified -> not standardized
    assert state["sensei_override"] is True


# -- Dashboard -------------------------------------------------------------

from kaizen import generate_dashboard  # noqa: E402


def test_dashboard_generation(tmp_path):
    rules = [{"name": "too-big", "condition": "state.get('doubled', 0) > 5",
              "severity": "high", "description": "Doubled value exceeded the limit"}]
    config = make_config(tmp_path, rules=rules)
    runlog = RunLog(str(tmp_path / "log.jsonl"))
    builder = KaizenGraphBuilder(State, config, runlog=runlog)
    builder.add_node("double", double)
    builder.set_entry_point("double")
    builder.set_finish_point("double")
    graph = builder.compile()
    graph.invoke({"value": 10})

    board = LocalKanbanBoard(config.data["kanban"]["board_path"])
    path = generate_dashboard(config, board, runlog,
                              output_path=str(tmp_path / "dash.html"))
    page = (tmp_path / "dash.html").read_text()
    assert path.endswith("dash.html")
    assert "test-process" in page
    assert "too-big" in page                  # pareto row
    assert "on target" in page or "off target" in page
    assert "&" not in page.split("<style>")[0] or "&#" in page  # escaped output


# -- .env loading ----------------------------------------------------------

from kaizen import load_env  # noqa: E402


def test_load_env_walks_up_and_respects_existing(tmp_path, monkeypatch):
    import os
    (tmp_path / ".env").write_text(
        "# comment\nANTHROPIC_API_KEY='sk-test-123'\nexport EXTRA=abc\nALREADY=new\n"
    )
    child = tmp_path / "a" / "b"
    child.mkdir(parents=True)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("EXTRA", raising=False)
    monkeypatch.setenv("ALREADY", "original")

    loaded = load_env(str(child))          # found two levels up
    assert loaded["ANTHROPIC_API_KEY"] == "sk-test-123"
    assert os.environ["EXTRA"] == "abc"    # 'export ' prefix handled
    assert os.environ["ALREADY"] == "original"  # existing env always wins
    assert "ALREADY" not in loaded


def test_sensei_recoach_responds_to_updated_analysis(tmp_path):
    rules = [{"name": "always", "condition": "True", "severity": "high",
              "description": "Something abnormal happened in the run"}]
    config = make_config(tmp_path, rules=rules)
    graph = build(tmp_path, config)
    graph.invoke({"value": 1})
    board = LocalKanbanBoard(config.data["kanban"]["board_path"])
    sensei = SenseiAgent(config)
    sensei.coach_open_exceptions(board)

    # Human fills in a solid analysis inside the ticket description.
    ticket = board.list_tickets(bucket="Exceptions", status="open")[0]
    body = ticket.description.split(SenseiAgent.SECTION_MARKER)[0]
    body = body.replace("**Problem:**", "**Problem:** Run 42 exceeded limit 5 by 15 on 2026-07-19 —")
    for i, why in enumerate([
        "The doubled value exceeded the limit",
        "The input arrived pre-doubled from the upstream feed",
        "The feed changed units last week",
        "No contract test exists between feed and process",
        "Onboarding of the feed predates the contract-test standard",
    ], start=1):
        body = body.replace(f"{i}. Why? — _(to be answered together)_", f"{i}. Why? — {why}")
    body = body.replace("**Root cause:** _(agree during the daily kata)_",
                        "**Root cause:** Feed integration lacks a units contract test")
    body = body.replace("**Countermeasure:** _(agree during the daily kata)_",
                        "**Countermeasure:** Add the contract test; verify over one week of runs")
    board.update_ticket(ticket.id, description=body + "\n\n---\n\n**Sensei questions** old stuff")

    assert sensei.coach_open_exceptions(board) == 0            # without recoach: skipped
    assert sensei.coach_open_exceptions(board, recoach=True) == 1
    updated = [t for t in board.list_tickets() if t.id == ticket.id][0]
    assert updated.description.count("**Sensei") == 1          # old section replaced
    assert "ready to act on" in updated.description            # solid analysis accepted
