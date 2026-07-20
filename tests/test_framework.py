"""Smoke tests for the AI Kaizen Framework core loop."""

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


# -- Interactive board server ----------------------------------------------

import json as _json  # noqa: E402
import threading  # noqa: E402
import urllib.request  # noqa: E402

from kaizen.board_server import make_server  # noqa: E402


def _req(port, path, payload=None):
    data = _json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data,
                                 method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req) as resp:
        return _json.loads(resp.read())


def test_board_server_api(tmp_path):
    rules = [{"name": "always", "condition": "True", "severity": "high",
              "description": "Something abnormal happened"}]
    config = make_config(tmp_path, rules=rules)
    graph = build(tmp_path, config)
    graph.invoke({"value": 1})
    board = LocalKanbanBoard(config.data["kanban"]["board_path"])

    server = make_server(config, board, port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        state = _req(port, "/api/state")
        assert state["process"] == "test-process"
        ticket = state["tickets"][0]

        # drag to in_progress
        updated = _req(port, f"/api/tickets/{ticket['id']}", {"status": "in_progress"})
        assert updated["status"] == "in_progress"

        # add a note
        updated = _req(port, f"/api/tickets/{ticket['id']}/note", {"text": "went and saw the export job"})
        assert "went and saw the export job" in updated["description"]

        # edits + notes survive on disk
        on_disk = [t for t in board.list_tickets() if t.id == ticket["id"]][0]
        assert on_disk.status == "in_progress"
    finally:
        server.shutdown()
        server.server_close()


# -- Kaizen Teammate (autonomous board work) -------------------------------

from kaizen import KaizenTeammate  # noqa: E402


class StubLLM:
    """Returns scripted responses in order; records prompts."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        class R:  # noqa: N801
            content = self.responses.pop(0) if self.responses else "PROBLEM: OPEN"
        return R()


RESPONSE_WITH_QUESTION = """PROBLEM: Run exceeded the doubled-value limit of 5 (actual 20) in the double node
WHY1: The doubled value exceeded the configured limit
WHY2: The input value arrived larger than expected
WHY3: OPEN
ROOT: OPEN
COUNTERMEASURE: OPEN
PILOT: OPEN
QUESTION: Where does the input value originate — is there an upstream feed that could pre-scale it?
"""

RESPONSE_COMPLETE = """PROBLEM: Run exceeded the doubled-value limit of 5 (actual 20) in the double node
WHY1: The doubled value exceeded the configured limit
WHY2: The input value arrived pre-doubled from the upstream feed
WHY3: The feed changed units last week without notice
WHY4: No contract test exists between the feed and this process
WHY5: The integration predates the team's contract-testing standard
ROOT: Feed integration lacks a units contract test
COUNTERMEASURE: Add a contract test on the upstream feed units before ingestion
PILOT: Run the feed with the contract test in sandbox mode for one week and compare exceptions
"""


def _board_with_ticket(tmp_path):
    rules = [{"name": "too-big", "condition": "state.get('doubled', 0) > 5",
              "severity": "high", "description": "Doubled value exceeded the limit"}]
    config = make_config(tmp_path, rules=rules)
    graph = build(tmp_path, config)
    graph.invoke({"value": 10})
    return config, LocalKanbanBoard(config.data["kanban"]["board_path"])


def test_teammate_works_ticket_and_asks_the_team(tmp_path):
    config, board = _board_with_ticket(tmp_path)
    llm = StubLLM([RESPONSE_WITH_QUESTION])
    teammate = KaizenTeammate(config, board, runlog=RunLog(str(tmp_path / "log.jsonl")), llm=llm)

    assert teammate.work_board() == 1
    ticket = board.list_tickets(bucket="Exceptions")[0]
    assert ticket.status == "in_progress"          # visibly picked up by the agent
    assert "Needs from the team" in ticket.description
    assert "upstream feed" in ticket.description
    # Second pass with no human input: nothing to do.
    assert teammate.work_board() == 0


def test_teammate_continues_after_team_note_and_proposes(tmp_path):
    config, board = _board_with_ticket(tmp_path)
    llm = StubLLM([RESPONSE_WITH_QUESTION, RESPONSE_COMPLETE])
    runlog = RunLog(str(tmp_path / "log.jsonl"))
    teammate = KaizenTeammate(config, board, runlog=runlog, llm=llm)
    teammate.work_board()

    # Human answers on the ticket (as a Planner comment / local note would).
    ticket = board.list_tickets(bucket="Exceptions")[0]
    board.update_ticket(ticket.id, description=ticket.description +
                        "\n\n**Note (2026-07-19 10:12):** Yes — the nightly feed was "
                        "switched to pre-scaled units last week.")

    assert teammate.work_board() == 1              # change detected, work resumes
    ticket = board.list_tickets(bucket="Exceptions")[0]
    assert "Proposal ready for team review" in ticket.description
    assert "contract test" in ticket.description
    assert "**Note (2026-07-19 10:12):**" in ticket.description  # human note preserved
    assert ticket.status != "done"                 # closing stays a human act
    # The note the human wrote reached the investigator prompt on the second pass.
    assert any("pre-scaled units" in p for p in llm.prompts)
    events = runlog.events()
    assert any(e["type"] == "teammate_update" and e.get("proposal_ready") for e in events)


# -- Aggregation & improvement ideas ---------------------------------------


def test_non_stop_defects_are_counted_not_carded(tmp_path):
    # A low/medium defect below the stop threshold is recorded but never carded.
    rules = [{"name": "small-defect", "condition": "state.get('doubled', 0) > 5",
              "severity": "medium", "description": "A minor defect"}]
    config = make_config(tmp_path, rules=rules, stop_on="high")
    graph = build(tmp_path, config)
    for value in (10, 11, 12):
        graph.invoke({"value": value})

    board = LocalKanbanBoard(config.data["kanban"]["board_path"])
    assert board.list_tickets(bucket="Exceptions") == []   # no cards for defects
    # But the defects ARE counted in the run log (source of SQDIP / Pareto).
    exceptions = [e for e in RunLog(str(tmp_path / "log.jsonl")).events()
                  if e["type"] == "exception" and e["rule"] == "small-defect"]
    assert len(exceptions) == 3


def test_stop_recurrences_aggregate_onto_one_card(tmp_path):
    rules = [{"name": "too-big", "condition": "state.get('doubled', 0) > 5",
              "severity": "high", "description": "Doubled value exceeded the limit"}]
    config = make_config(tmp_path, rules=rules, stop_on="high")
    graph = build(tmp_path, config)
    for value in (10, 11, 12):          # three line-stops of the same pattern
        graph.invoke({"value": value})

    board = LocalKanbanBoard(config.data["kanban"]["board_path"])
    tickets = board.list_tickets(bucket="Exceptions")
    assert len(tickets) == 1            # ONE card for the stop pattern
    assert tickets[0].description.count("**Occurrence (") == 2


def test_reflection_raises_deduped_improvement_ideas(tmp_path):
    rules = [{"name": "always", "condition": "True", "severity": "low"}]
    config = make_config(tmp_path, rules=rules)
    runlog = RunLog(str(tmp_path / "log.jsonl"))
    builder = KaizenGraphBuilder(State, config, runlog=runlog)
    builder.add_node("double", double)
    builder.set_entry_point("double")
    builder.set_finish_point("double")
    graph = builder.compile()
    graph.invoke({"value": 1})
    graph.invoke({"value": 2})          # 'always' fires twice -> idea-worthy pattern

    board = LocalKanbanBoard(config.data["kanban"]["board_path"])
    agent = ReflectionAgent(config, runlog, board=board, reports_dir=str(tmp_path / "reports"))
    agent.daily_reflection()
    ideas = board.list_tickets(bucket="Improvement Ideas")
    assert len(ideas) == 1
    assert "always" in ideas[0].title
    assert "ai-raised" in ideas[0].labels
    agent.daily_reflection()            # second reflection: no duplicate idea
    assert len(board.list_tickets(bucket="Improvement Ideas")) == 1


def test_board_server_add_card(tmp_path):
    config = make_config(tmp_path)
    board = LocalKanbanBoard(config.data["kanban"]["board_path"])
    server = make_server(config, board, port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        created = _req(port, "/api/tickets",
                       {"title": "Idea: remind consultants two days before cutoff",
                        "bucket": "Improvement Ideas"})
        assert created["bucket"] == "Improvement Ideas"
        assert "human-raised" in created["labels"]
        state = _req(port, "/api/state")
        assert "Improvement Ideas" in state["buckets"]
        assert any(t["id"] == created["id"] for t in state["tickets"])
    finally:
        server.shutdown()
        server.server_close()


# -- Targets: a missed target raises a card ---------------------------------

from kaizen import MeasureTarget  # noqa: E402
import datetime as _dt  # noqa: E402


def test_target_miss_problem_statement_matches_practice():
    # Reproduces the standard practice verbatim: one card, framed as the gap.
    t = MeasureTarget(
        name="customer-complaints",
        description="customer complaints",
        rule="customer-complaint",
        target=20,
        direction="below",
        volume_from="runs",
        volume_unit="calls to the Acme helpdesk",
    )
    events = ([{"type": "run_started"} for _ in range(1000)]
              + [{"type": "exception", "rule": "customer-complaint"} for _ in range(30)])
    result = t.evaluate(events, "20 July", "acme-helpdesk")
    assert result.missed
    assert result.problem_statement() == (
        "On 20 July, 30 out of 1000 calls to the Acme helpdesk had customer "
        "complaints, against the target of <20."
    )
    # A day within target raises no problem.
    ok = ([{"type": "run_started"} for _ in range(1000)]
          + [{"type": "exception", "rule": "customer-complaint"} for _ in range(12)])
    assert t.evaluate(ok, "21 July", "acme-helpdesk").missed is False


def test_reflection_raises_one_card_per_missed_target(tmp_path):
    rules = [{"name": "missing-report", "condition": "True", "severity": "low",
              "description": "A delivery report is missing"}]
    config = make_config(tmp_path, rules=rules)
    config.data["targets"] = [{
        "name": "report-completeness", "description": "a missing report",
        "rule": "missing-report", "volume_from": "runs",
        "volume_unit": "runs", "target": 1, "direction": "below",
    }]
    runlog = RunLog(str(tmp_path / "log.jsonl"))
    builder = KaizenGraphBuilder(State, config, runlog=runlog)
    builder.add_node("double", double)
    builder.set_entry_point("double")
    builder.set_finish_point("double")
    graph = builder.compile()
    graph.invoke({"value": 1})
    graph.invoke({"value": 2})          # 2 missing-report defects across 2 runs

    board = LocalKanbanBoard(config.data["kanban"]["board_path"])
    assert board.list_tickets(bucket="Exceptions") == []   # defects not carded

    agent = ReflectionAgent(config, runlog, board=board, reports_dir=str(tmp_path / "reports"))
    day = _dt.datetime.now(_dt.timezone.utc).date()
    agent.daily_reflection(day=day)

    cards = [t for t in board.list_tickets(bucket="Exceptions") if "Target missed" in t.title]
    assert len(cards) == 1
    assert "out of 2 runs had a missing report, against the target of <1" in cards[0].description
    assert "target-miss" in cards[0].labels
    # Re-running the review does not duplicate the card.
    agent.daily_reflection(day=day)
    cards = [t for t in board.list_tickets(bucket="Exceptions") if "Target missed" in t.title]
    assert len(cards) == 1
