"""Professional services invoicing with the AI Kaizen Framework.

Pipeline: load delivery reports -> validate -> aggregate hours -> calculate
invoice -> raise invoice. Every step is watched by the abnormality rules in
config/kaizen_config.yaml; problems become Kanban tickets and, at high
severity, stop the line (Jidoka) instead of raising a bad invoice.

Run it:

    python invoicing_workflow.py                # normal run
    python invoicing_workflow.py --sandbox      # no tickets, no invoice raised
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, TypedDict

from kaizen import KaizenConfig, KaizenGraphBuilder, RunLog
from kaizen.kaizen_graph import KaizenState

HERE = Path(__file__).parent


class InvoicingState(KaizenState, TypedDict, total=False):
    period: str
    expected_consultants: List[str]
    rate_card: Dict[str, Dict[str, Any]]
    reports: List[Dict[str, Any]]
    valid_reports: List[Dict[str, Any]]
    invalid_reports: List[Dict[str, Any]]
    missing_reports: List[str]
    hours_by_engagement: Dict[str, float]
    total_hours: float
    unbilled_engagements: List[str]
    invoice_lines: List[Dict[str, Any]]
    invoice_total: float
    invoice_reference: str


# --------------------------------------------------------------------------
# Nodes — plain functions over state; the framework adds the Jidoka layer.
# --------------------------------------------------------------------------

def load_delivery_reports(state: InvoicingState) -> Dict[str, Any]:
    with open(HERE / "sample_data" / "delivery_reports.json", encoding="utf-8") as fh:
        data = json.load(fh)
    return {
        "period": data["period"],
        "expected_consultants": data["expected_consultants"],
        "rate_card": data["rate_card"],
        "reports": data["reports"],
    }


def validate_reports(state: InvoicingState) -> Dict[str, Any]:
    valid, invalid = [], []
    for report in state["reports"]:
        (valid if report["hours"] > 0 else invalid).append(report)
    submitted = {r["consultant"] for r in state["reports"]}
    missing = [c for c in state["expected_consultants"] if c not in submitted]
    return {"valid_reports": valid, "invalid_reports": invalid, "missing_reports": missing}


def aggregate_hours(state: InvoicingState) -> Dict[str, Any]:
    hours: Dict[str, float] = {}
    for report in state["valid_reports"]:
        hours[report["engagement"]] = hours.get(report["engagement"], 0.0) + report["hours"]
    return {"hours_by_engagement": hours, "total_hours": round(sum(hours.values()), 2)}


def calculate_invoice(state: InvoicingState) -> Dict[str, Any]:
    lines, unbilled = [], []
    for engagement, hours in state["hours_by_engagement"].items():
        entry = state["rate_card"].get(engagement)
        if entry is None:
            unbilled.append(engagement)
            continue
        lines.append({
            "engagement": engagement,
            "client": entry["client"],
            "hours": hours,
            "rate": entry["rate"],
            "amount": round(hours * entry["rate"], 2),
        })
    return {
        "invoice_lines": lines,
        "invoice_total": round(sum(line["amount"] for line in lines), 2),
        "unbilled_engagements": unbilled,
    }


def raise_invoice(state: InvoicingState) -> Dict[str, Any]:
    # In production this would call the finance system's API. Here we just
    # mint a reference so the flow is observable end to end.
    reference = f"INV-{state['period']}-{state['kaizen_run_id'][:6].upper()}"
    return {"invoice_reference": reference}


# --------------------------------------------------------------------------
# Wire it together
# --------------------------------------------------------------------------

def build_graph(config: KaizenConfig):
    builder = KaizenGraphBuilder(
        InvoicingState,
        config,
        runlog=RunLog(str(HERE / "kaizen_runlog.jsonl")),
    )
    builder.add_node("load_reports", load_delivery_reports)
    builder.add_node("validate_reports", validate_reports)
    builder.add_node("aggregate_hours", aggregate_hours)
    builder.add_node("calculate_invoice", calculate_invoice)
    builder.add_node("raise_invoice", raise_invoice)

    builder.set_entry_point("load_reports")
    builder.add_edge("load_reports", "validate_reports")
    builder.add_edge("validate_reports", "aggregate_hours")
    builder.add_edge("aggregate_hours", "calculate_invoice")
    builder.add_edge("calculate_invoice", "raise_invoice")
    builder.set_finish_point("raise_invoice")
    return builder.compile()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sandbox", action="store_true",
                        help="Run in sandbox mode (no tickets, no invoice raised).")
    args = parser.parse_args()

    config = KaizenConfig.load(str(HERE / "config" / "kaizen_config.yaml"))
    if args.sandbox:
        config.data["sandbox"] = True
    # Keep the board file next to this example regardless of CWD.
    config.data["kanban"]["board_path"] = str(HERE / "kaizen_board.json")

    graph = build_graph(config)
    result = graph.invoke({})

    print(f"\n=== Invoicing run — period {result.get('period')} "
          f"{'(SANDBOX)' if config.sandbox else ''} ===")
    for exc in result.get("kaizen_exceptions", []):
        ticket = f" -> ticket {exc['ticket_id']}" if exc.get("ticket_id") else ""
        print(f"  [{exc['severity'].upper():8s}] {exc['node']}: {exc['rule']} — {exc['summary']}{ticket}")

    if result.get("kaizen_stopped"):
        print(f"\nJIDOKA STOP: {result['kaizen_stop_reason']}")
        print("The line stopped before raising the invoice. Review the Problems "
              "column on the board, complete the 5 Whys together, then re-run.")
    else:
        print(f"\nInvoice {result['invoice_reference']} raised: "
              f"{result['invoice_total']:.2f} across {len(result['invoice_lines'])} engagements.")

    print(f"\nBoard:   {config.data['kanban']['board_path']}")
    print(f"Run log: {HERE / 'kaizen_runlog.jsonl'}")
    print("Next: python run_daily_kaizen.py  (generate the daily Kaizen summary)")


if __name__ == "__main__":
    main()
