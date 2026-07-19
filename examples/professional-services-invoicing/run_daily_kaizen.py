"""Generate today's daily Kaizen summary for the invoicing process.

Run after (one or more) invoicing runs:

    python run_daily_kaizen.py            # deterministic summary
    python run_daily_kaizen.py --llm      # narrative written by Claude
                                          # (pip install 'ai-jidoka-framework[llm]'
                                          #  and set ANTHROPIC_API_KEY)

The summary is written to kaizen_reports/ and posted to the shared Kanban
board's "Daily Kaizen" bucket — the agenda for the joint human-AI standup.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from kaizen import KaizenConfig, ReflectionAgent, RunLog, build_default_llm, create_board

HERE = Path(__file__).parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--llm", action="store_true",
                        help="Use Claude to write the reflection narrative.")
    args = parser.parse_args()

    config = KaizenConfig.load(str(HERE / "config" / "kaizen_config.yaml"))
    config.data["kanban"]["board_path"] = str(HERE / "kaizen_board.json")

    llm = None
    if args.llm:
        try:
            llm = build_default_llm()
        except Exception as exc:
            print(f"[!] Claude narration unavailable ({exc}).\n"
                  "    Install the extra and set your key:\n"
                  "      pip install 'ai-jidoka-framework[llm]'\n"
                  "      export ANTHROPIC_API_KEY=...\n"
                  "    Continuing with the deterministic summary.\n")

    agent = ReflectionAgent(
        config=config,
        runlog=RunLog(str(HERE / "kaizen_runlog.jsonl")),
        board=create_board(config.kanban),
        llm=llm,
        reports_dir=str(HERE / "kaizen_reports"),
    )
    summary = agent.daily_reflection()

    print(summary.markdown)
    print(f"\n--- Report saved to {summary.report_path}")
    if summary.ticket_id:
        print(f"--- Posted to the board as ticket {summary.ticket_id} (Daily Kaizen bucket)")


if __name__ == "__main__":
    main()
