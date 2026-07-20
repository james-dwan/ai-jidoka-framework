"""Interactive root cause investigation — an A3 as a flow.

Pick an open exception ticket and walk the full investigation kata with the
AI: problem framing -> Pareto -> fishbone -> 5 Whys -> Sensei gate ->
countermeasure -> pilot -> verify -> standardize. Every stage pauses for your
input (the human gates are non-optional), and the Sensei sends weak analyses
back with socratic questions.

    python invoicing_workflow.py      # first, so there are exception tickets
    python run_investigation.py
    python run_investigation.py --llm # Claude drafts statements & fishbones

Multi-line answers: finish with an empty line. This demo uses an in-memory
checkpointer (one sitting); production would use a persistent one so an
investigation can span days.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from langgraph.types import Command

from kaizen import (
    InvestigationGraphBuilder,
    KaizenConfig,
    RunLog,
    a3_markdown,
    build_default_llm,
    create_board,
    load_env,
)

HERE = Path(__file__).parent
load_env(str(HERE))  # picks up ANTHROPIC_API_KEY from a git-ignored .env


def read_answer(payload: dict) -> str:
    print("\n" + "=" * 72)
    print(f"STAGE: {payload.get('stage', '?')}")
    print("=" * 72)
    for key, value in payload.items():
        if key in ("stage", "instruction"):
            continue
        print(f"\n{key}:")
        if isinstance(value, dict):
            for k, v in value.items():
                print(f"  - {k}: {v}")
        elif isinstance(value, list):
            for item in value:
                print(f"  - {item}")
        else:
            print(f"  {value}")
    print(f"\n>>> {payload.get('instruction', 'Your answer:')}")
    print("(finish with an empty line)")
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line == "":
            break
        lines.append(line)
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--llm", action="store_true", help="Let Claude draft statements and fishbones.")
    args = parser.parse_args()

    config = KaizenConfig.load(str(HERE / "config" / "kaizen_config.yaml"))
    config.data["kanban"]["board_path"] = str(HERE / "kaizen_board.json")
    board = create_board(config.kanban)

    open_exceptions = board.list_tickets(bucket="Problems", status="open")
    if not open_exceptions:
        print("No open exception tickets. Run invoicing_workflow.py first.")
        return

    print("Open exception tickets:")
    for i, ticket in enumerate(open_exceptions):
        print(f"  [{i}] {ticket.title}")
    choice = input("Investigate which one? [0] ").strip() or "0"
    ticket = open_exceptions[int(choice)]

    llm = None
    if args.llm:
        try:
            llm = build_default_llm()
        except Exception as exc:
            print(f"[!] Claude drafting unavailable ({exc}). "
                  "Continuing with heuristic drafts.\n")

    builder = InvestigationGraphBuilder(
        config,
        board,
        runlog=RunLog(str(HERE / "kaizen_runlog.jsonl")),
        llm=llm,
    )
    graph = builder.build()
    thread = {"configurable": {"thread_id": ticket.id}}

    state = graph.invoke(builder.start_input(ticket.id), thread)
    while "__interrupt__" in state:
        answer = read_answer(state["__interrupt__"][0].value)
        state = graph.invoke(Command(resume=answer), thread)

    print("\n" + "=" * 72)
    print("INVESTIGATION COMPLETE" if state.get("verified") else
          "INVESTIGATION PAUSED — countermeasure not yet verified")
    print("=" * 72 + "\n")
    print(a3_markdown(state))
    print(f"\nThe A3 has been written back to ticket {ticket.id} on the board.")


if __name__ == "__main__":
    main()
