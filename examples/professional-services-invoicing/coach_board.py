"""Have the Sensei coach the open exception tickets on the board.

The collaboration loop, ticket-mediated:

1. ``python coach_board.py``            — sensei appends socratic questions to
                                          each open exception ticket
2. You answer them IN the ticket        — edit the 5 Whys / root cause /
                                          countermeasure lines in the ticket
                                          description (kaizen_board.json
                                          locally; the task description in
                                          Planner/Lists in production)
3. ``python coach_board.py --recoach``  — sensei re-reads your updated
                                          analysis and responds: more
                                          questions, or "ready to act on"

Add ``--llm`` for Claude-written questions on top of the heuristics.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from kaizen import KaizenConfig, SenseiAgent, build_default_llm, create_board, load_env

HERE = Path(__file__).parent
load_env(str(HERE))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--llm", action="store_true", help="Claude-written questions too.")
    parser.add_argument("--recoach", action="store_true",
                        help="Re-review tickets whose analysis you have updated.")
    args = parser.parse_args()

    config = KaizenConfig.load(str(HERE / "config" / "kaizen_config.yaml"))
    config.data["kanban"]["board_path"] = str(HERE / "kaizen_board.json")
    board = create_board(config.kanban)

    llm = None
    if args.llm:
        try:
            llm = build_default_llm()
        except Exception as exc:
            print(f"[!] Claude questions unavailable ({exc}); heuristic questions only.\n")

    sensei = SenseiAgent(config, llm=llm)
    coached = sensei.coach_open_exceptions(board, recoach=args.recoach)
    print(f"Sensei coached {coached} open ticket(s).")

    for ticket in board.list_tickets(bucket="Exceptions", status="open"):
        marker = SenseiAgent.SECTION_MARKER
        if marker in ticket.description:
            print(f"\n### {ticket.title}")
            print("**Sensei" + ticket.description.split(marker, 1)[1].split("\n\n---\n\n")[0])
    print("\nAnswer the questions by editing the ticket descriptions "
          "(kaizen_board.json), then run:  python coach_board.py --recoach")


if __name__ == "__main__":
    main()
