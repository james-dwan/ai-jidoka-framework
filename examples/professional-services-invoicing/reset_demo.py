"""Reset the demo to a clean slate.

Deletes only the git-ignored runtime artifacts this demo writes — the board,
run log, reports, dashboard, proposals, working config, and config history.
The committed sample data, config, and code are never touched.

    python reset_demo.py
"""

from __future__ import annotations

import shutil
from pathlib import Path

HERE = Path(__file__).parent

FILES = [
    "kaizen_board.json",       # the shared Kanban board
    "kaizen_runlog.jsonl",     # the event log (defect counts, SQDIP source)
    "kaizen_dashboard.html",   # generated dashboard
    "kaizen_proposals.json",   # standard-work change proposals
    "kaizen_config.work.yaml", # propose_change.py's working copy of the config
]
DIRS = [
    "kaizen_reports",          # daily Kaizen reports
    "config_history",          # archived config versions from approvals
]


def main() -> None:
    removed = []
    for name in FILES:
        path = HERE / name
        if path.exists():
            path.unlink()
            removed.append(name)
    for name in DIRS:
        path = HERE / name
        if path.exists():
            shutil.rmtree(path)
            removed.append(name + "/")

    if removed:
        print("Removed:")
        for name in removed:
            print(f"  - {name}")
    else:
        print("Already clean — nothing to remove.")
    print("\nFresh slate. Start again with: python invoicing_workflow.py")


if __name__ == "__main__":
    main()
