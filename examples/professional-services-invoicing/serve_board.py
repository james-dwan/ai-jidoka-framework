"""Serve the shared Kanban board with the Kaizen Teammate working it live.

    python serve_board.py --llm            # board + autonomous agent (default 15s passes)
    python serve_board.py                  # board only (agents off without an LLM)
    python serve_board.py --llm --interval 30

Open the board, watch the teammate move tickets to "In progress" and fill in
what the evidence supports, answer its "Needs from the team" questions by
adding a note — and see it continue on its next pass. You drag the ticket to
Done; it never closes work itself.

The board deliberately offers only interactions Microsoft Planner also has
(drag between progress columns, edit the description, add notes). The agents
talk to the board the same way they would talk to Planner: reading and
writing tickets.
"""

from __future__ import annotations

import argparse
import threading
from pathlib import Path

from kaizen import KaizenConfig, RunLog, build_default_llm, create_board, load_env
from kaizen.board_server import serve_board
from kaizen.teammate import KaizenTeammate

HERE = Path(__file__).parent
load_env(str(HERE))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--llm", action="store_true",
                        help="Enable the autonomous Kaizen Teammate (needs ANTHROPIC_API_KEY).")
    parser.add_argument("--interval", type=float, default=15.0,
                        help="Seconds between teammate passes over the board.")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    config = KaizenConfig.load(str(HERE / "config" / "kaizen_config.yaml"))
    config.data["kanban"]["board_path"] = str(HERE / "kaizen_board.json")
    board = create_board(config.kanban)

    if args.llm:
        try:
            llm = build_default_llm()
        except Exception as exc:
            print(f"[!] Teammate unavailable ({exc}); serving the board without agents.")
        else:
            teammate = KaizenTeammate(
                config, board, runlog=RunLog(str(HERE / "kaizen_runlog.jsonl")), llm=llm
            )
            threading.Thread(
                target=teammate.watch, kwargs={"interval": args.interval}, daemon=True
            ).start()

    serve_board(config, board, port=args.port)


if __name__ == "__main__":
    main()
