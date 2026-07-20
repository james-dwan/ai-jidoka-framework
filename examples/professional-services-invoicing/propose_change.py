"""Closing the loop: propose → pilot → the process owner approves a change to
standard work.

Shows an **agent** proposing a change to its own standard work (the Jidoka stop
threshold), piloting it against the recorded run log as a what-if, and the
**process owner** approving it — which updates and versions the standard.
Agents can propose and pilot; only the owner can approve.

    python invoicing_workflow.py   # run a few times first, to record defects
    python propose_change.py

To keep the demo repeatable it operates on a *working copy* of the config
(`kaizen_config.work.yaml`, git-ignored) — the committed standard work is never
touched, and rollback is a previous file in `config_history/`.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from kaizen import KaizenConfig, ProposalRegistry, RunLog, create_board

HERE = Path(__file__).parent


def main() -> None:
    runlog = RunLog(str(HERE / "kaizen_runlog.jsonl"))
    if not runlog.events():
        print("No run log yet — run `python invoicing_workflow.py` a few times first.")
        return

    # Work on a copy so the committed standard work stays pristine and the demo
    # is repeatable.
    src = HERE / "config" / "kaizen_config.yaml"
    work = HERE / "kaizen_config.work.yaml"
    shutil.copy2(src, work)
    config = KaizenConfig.load(str(work))
    config.data["kanban"]["board_path"] = str(HERE / "kaizen_board.json")
    board = create_board(config.kanban)
    registry = ProposalRegistry(config, runlog=runlog, board=board,
                                path=str(HERE / "kaizen_proposals.json"))

    print(f"Process owner (only they can approve): {config.process_owner}\n")

    # 1. An AGENT proposes a change to its OWN standard work.
    proposal = registry.propose(
        title="Lower the Jidoka stop threshold to medium",
        path=["jidoka", "stop_on_severity"],
        new_value="medium",
        rationale=("Medium defects (missing reports, bad hours) recur every run. "
                   "Stopping the line on them would surface the problem sooner."),
        proposed_by="agent:teammate",
    )
    print(f"[proposed] {proposal.title}")
    print(f"  by {proposal.proposed_by} · {proposal.register} standard work · "
          f"{proposal.old_value!r} → {proposal.new_value!r}")

    # 2. Pilot it — replay the recorded run log as a what-if.
    proposal = registry.pilot(proposal.id)
    print(f"\n[piloted]\n  {proposal.pilot['summary']}")

    # 3. The AGENT cannot approve — only the process owner can.
    try:
        registry.approve(proposal.id, owner=proposal.proposed_by)
    except PermissionError as exc:
        print(f"\n[gate] agent tried to approve → blocked: {exc}")

    # 4. The process owner reviews the evidence and approves.
    proposal = registry.approve(proposal.id, owner=config.process_owner)
    print(f"\n[approved] by {proposal.approver}")
    print(f"  standard work updated: jidoka.stop_on_severity = "
          f"{config.data['jidoka']['stop_on_severity']!r}")
    print(f"  config versioned to v{proposal.resulting_version} "
          f"(previous archived in config_history/)")

    print(f"\nThe proposal is also a card in the Experiments bucket on the board.")
    print(f"Committed standard work untouched — this ran on {work.name}.")


if __name__ == "__main__":
    main()
