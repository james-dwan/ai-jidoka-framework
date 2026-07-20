"""Kaizen dashboard — a self-contained HTML view of the improvement system.

Generates a single HTML file (no server, no dependencies, no build step) from
the same sources the agents use: SQDIP vs targets, a Pareto of exception
rules, the shared Kanban board, and the latest daily Kaizen report. Open it in
a browser, project it in the daily kata, or attach it to a demo.

Light and dark mode are both supported; colors follow a validated,
accessibility-checked palette (status colors always ship with an icon + word,
never color alone).
"""

from __future__ import annotations

import datetime as _dt
import html
import re
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional

from .config import KaizenConfig
from .kanban_integration import KanbanBoard
from .reflection_agent import ReflectionAgent, SQDIPSnapshot
from .runlog import RunLog


def generate_dashboard(
    config: KaizenConfig,
    board: KanbanBoard,
    runlog: RunLog,
    day: Optional[_dt.date] = None,
    output_path: str = "kaizen_dashboard.html",
    reports_dir: Optional[str] = None,
) -> str:
    """Render the dashboard and return the output path."""
    day = day or _dt.datetime.now(_dt.timezone.utc).date()
    agent = ReflectionAgent(config, runlog, board=board)
    sqdip = agent.compute_sqdip(day)
    pareto = dict(Counter(
        e.get("rule", "unknown") for e in runlog.events() if e.get("type") == "exception"
    ).most_common())

    buckets = list(config.kanban.get("buckets", {}).values()) or ["Problems"]
    tickets_by_bucket: Dict[str, List] = {b: board.list_tickets(bucket=b) for b in buckets}

    latest_report = _latest_report(reports_dir)

    page = _render(config, day, sqdip, pareto, tickets_by_bucket, latest_report)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    return str(out)


# ----------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------

_SEVERITY_RANK = {"urgent": 0, "high": 1, "medium": 2, "low": 3}


def _latest_report(reports_dir: Optional[str]) -> str:
    if not reports_dir:
        return ""
    reports = sorted(Path(reports_dir).glob("kaizen-*.md"))
    return reports[-1].read_text(encoding="utf-8") if reports else ""


def _tile(letter: str, name: str, value: str, target, ok: Optional[bool]) -> str:
    if ok is None:
        status = '<span class="status neutral">— no target</span>'
    elif ok:
        status = '<span class="status good">&#10003; on target</span>'
    else:
        status = '<span class="status serious">&#9650; off target</span>'
    target_text = f"target {target}" if target is not None else "&nbsp;"
    return (
        f'<div class="tile"><div class="tile-head"><span class="letter">{letter}</span>'
        f'<span class="tile-name">{html.escape(name)}</span></div>'
        f'<div class="tile-value">{value}</div>'
        f'<div class="tile-foot">{status}<span class="target">{target_text}</span></div></div>'
    )


def _sqdip_tiles(sqdip: SQDIPSnapshot, targets: Dict) -> str:
    def t(name):
        return targets.get(name, {}).get("target")

    def le(value, target):  # lower-or-equal is good
        return None if value is None or target is None else value <= target

    def ge(value, target):  # greater-or-equal is good
        return None if value is None or target is None else value >= target

    quality = sqdip.quality_exception_rate
    delivery = sqdip.delivery_completion_rate
    inventory = sqdip.inventory_open_tickets
    tiles = [
        _tile("S", "Safety", str(sqdip.safety_incidents), t("safety"),
              le(sqdip.safety_incidents, t("safety"))),
        _tile("Q", "Quality", "n/a" if quality is None else f"{quality}%", t("quality"),
              le(quality, t("quality"))),
        _tile("D", "Delivery", "n/a" if delivery is None else f"{delivery}%", t("delivery"),
              ge(delivery, t("delivery"))),
        _tile("I", "Inventory", "n/a" if inventory is None else str(inventory), t("inventory"),
              le(inventory, t("inventory"))),
        _tile("P", "Productivity", str(sqdip.productivity_runs_completed), t("productivity"),
              ge(sqdip.productivity_runs_completed, t("productivity"))),
    ]
    return "\n".join(tiles)


def _pareto_rows(pareto: Dict[str, int]) -> str:
    if not pareto:
        return '<p class="empty">No exceptions recorded yet.</p>'
    peak = max(pareto.values())
    rows = []
    for rule, count in pareto.items():
        width = max(2.0, 100.0 * count / peak)
        rows.append(
            f'<div class="prow" title="{html.escape(rule)}: {count} exception(s)">'
            f'<span class="plabel">{html.escape(rule)}</span>'
            f'<span class="ptrack"><span class="pbar" style="width:{width:.1f}%"></span></span>'
            f'<span class="pvalue">{count}</span></div>'
        )
    return "\n".join(rows)


def _board_columns(tickets_by_bucket: Dict[str, List]) -> str:
    columns = []
    for bucket, tickets in tickets_by_bucket.items():
        cards = []
        for ticket in sorted(tickets, key=lambda t: (_SEVERITY_RANK.get(t.priority, 4), t.created_at)):
            done = ticket.status == "done"
            cards.append(
                f'<div class="card{" done" if done else ""}">'
                f'<span class="chip {html.escape(ticket.priority)}">{html.escape(ticket.priority)}</span>'
                f'<span class="chip state">{html.escape(ticket.status.replace("_", " "))}</span>'
                f'<div class="card-title">{html.escape(ticket.title)}</div></div>'
            )
        columns.append(
            f'<div class="column"><div class="col-head">{html.escape(bucket)}'
            f'<span class="count">{len(tickets)}</span></div>'
            + ("\n".join(cards) or '<p class="empty">empty</p>') + "</div>"
        )
    return "\n".join(columns)


def _inline_md(text: str) -> str:
    """Escape, then render the inline markdown the reports use: **bold**, `code`."""
    out = html.escape(text)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    return out


def _md_to_html(md: str) -> str:
    """A tiny renderer for the subset of markdown the Kaizen reports and
    tickets contain — headings, pipe tables, bullet/numbered lists, checkbox
    items, bold, code — keeping everything dependency-free. Headings are
    shifted down two levels to nest under the page's own headings. HTML
    comments (agent bookkeeping markers) are hidden."""
    md = re.sub(r"<!--.*?-->", "", md, flags=re.DOTALL)
    blocks: List[str] = []
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not line.strip():
            i += 1
            continue
        if line.startswith("|"):                                   # pipe table
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells):  # skip |---| row
                    rows.append(cells)
                i += 1
            head, body = rows[0], rows[1:]
            table = ["<table><thead><tr>"]
            table += [f"<th>{_inline_md(c)}</th>" for c in head]
            table.append("</tr></thead><tbody>")
            for row in body:
                table.append("<tr>" + "".join(f"<td>{_inline_md(c)}</td>" for c in row) + "</tr>")
            table.append("</tbody></table>")
            blocks.append("".join(table))
            continue
        if re.fullmatch(r"-{3,}", line.strip()):                   # horizontal rule
            blocks.append("<hr>")
            i += 1
            continue
        if m := re.match(r"(#{1,4})\s+(.*)", line):                # heading, shifted +2
            level = min(len(m.group(1)) + 2, 6)
            blocks.append(f"<h{level}>{_inline_md(m.group(2))}</h{level}>")
            i += 1
            continue
        if re.match(r"[-*]\s+", line) or re.match(r"\d+\.\s+", line):   # list
            ordered = bool(re.match(r"\d+\.\s+", line))
            pattern = r"\d+\.\s+" if ordered else r"[-*]\s+"
            items = []
            while i < len(lines) and re.match(pattern, lines[i].strip()):
                text = re.sub(pattern, "", lines[i].strip(), count=1)
                if m := re.match(r"\[( |x|X)\]\s*(.*)", text):   # checklist item
                    checked = " checked" if m.group(1).strip() else ""
                    items.append(f'<li class="task"><input type="checkbox" disabled{checked}> '
                                 f"{_inline_md(m.group(2))}</li>")
                else:
                    items.append(f"<li>{_inline_md(text)}</li>")
                i += 1
            tag = "ol" if ordered else "ul"
            blocks.append(f"<{tag}>{''.join(items)}</{tag}>")
            continue
        paragraph = []                                             # paragraph
        while i < len(lines) and lines[i].strip() and not re.match(r"(\||#{1,4}\s|[-*]\s|\d+\.\s)", lines[i].strip()):
            paragraph.append(_inline_md(lines[i].strip()))
            i += 1
        # Single newlines inside a paragraph are line breaks (Root cause /
        # Countermeasure sit on adjacent lines and must not merge).
        blocks.append(f"<p>{'<br>'.join(paragraph)}</p>")
    return "\n".join(blocks)


def _render(config, day, sqdip, pareto, tickets_by_bucket, latest_report) -> str:
    sandbox = ('<span class="chip sandbox">SANDBOX</span>' if config.sandbox else "")
    report_section = ""
    if latest_report:
        report_section = (
            '<section><h2>Latest daily Kaizen report</h2>'
            f'<div class="report">{_md_to_html(latest_report)}</div>'
            "</section>"
        )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kaizen — {html.escape(config.process_name)}</title>
<style>
  :root {{
    color-scheme: light;
    --page: #f9f9f7; --surface: #fcfcfb;
    --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
    --grid: #e1e0d9; --border: rgba(11,11,11,0.10);
    --bar: #2a78d6;
    --good: #0ca30c; --good-text: #006300;
    --serious: #ec835a; --critical: #d03b3b; --warning: #fab219;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      color-scheme: dark;
      --page: #0d0d0d; --surface: #1a1a19;
      --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
      --grid: #2c2c2a; --border: rgba(255,255,255,0.10);
      --bar: #3987e5;
      --good: #0ca30c; --good-text: #0ca30c;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; padding: 24px; background: var(--page); color: var(--ink);
         font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }}
  main {{ max-width: 1100px; margin: 0 auto; }}
  h1 {{ font-size: 20px; margin: 0 0 2px; }}
  h2 {{ font-size: 15px; margin: 0 0 12px; color: var(--ink); }}
  .sub {{ color: var(--ink-2); margin-bottom: 24px; }}
  section {{ background: var(--surface); border: 1px solid var(--border);
             border-radius: 10px; padding: 18px 20px; margin-bottom: 20px; }}
  .empty {{ color: var(--muted); }}
  /* SQDIP tiles */
  .tiles {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; }}
  .tile {{ background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
           padding: 14px 16px; }}
  .tile-head {{ display: flex; align-items: baseline; gap: 8px; }}
  .letter {{ font-weight: 700; font-size: 16px; }}
  .tile-name {{ color: var(--ink-2); }}
  .tile-value {{ font-size: 30px; font-weight: 650; margin: 6px 0 4px; }}
  .tile-foot {{ display: flex; justify-content: space-between; align-items: baseline; gap: 8px; }}
  .status {{ font-size: 12px; font-weight: 600; }}
  .status.good {{ color: var(--good-text); }}
  .status.serious {{ color: var(--critical); }}
  .status.neutral {{ color: var(--muted); font-weight: 400; }}
  .target {{ color: var(--muted); font-size: 12px; }}
  /* Pareto */
  .prow {{ display: grid; grid-template-columns: 240px 1fr 40px; align-items: center;
           gap: 10px; padding: 3px 0; }}
  .plabel {{ color: var(--ink-2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .ptrack {{ display: block; height: 14px; }}
  .pbar {{ display: block; height: 100%; background: var(--bar);
           border-radius: 0 4px 4px 0; min-width: 3px; }}
  .prow:hover .pbar {{ opacity: 0.85; }}
  .pvalue {{ text-align: right; font-variant-numeric: tabular-nums; color: var(--ink); }}
  /* Board */
  .board {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 14px; }}
  .column {{ background: var(--page); border: 1px solid var(--border); border-radius: 10px;
             padding: 12px; }}
  .col-head {{ font-weight: 650; margin-bottom: 10px; display: flex; justify-content: space-between; }}
  .count {{ color: var(--muted); font-weight: 400; }}
  .card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
           padding: 10px 12px; margin-bottom: 8px; }}
  .card.done {{ opacity: 0.55; }}
  .card-title {{ margin-top: 6px; }}
  .chip {{ display: inline-block; font-size: 11px; font-weight: 600; border-radius: 999px;
           padding: 1px 8px; border: 1px solid var(--border); color: var(--ink-2);
           margin-right: 4px; text-transform: uppercase; letter-spacing: 0.03em; }}
  .chip.urgent, .chip.high {{ border-color: var(--critical); color: var(--critical); }}
  .chip.medium {{ border-color: var(--serious); color: var(--serious); }}
  .chip.sandbox {{ border-color: var(--warning); color: var(--ink-2); margin-left: 8px; }}
  /* Rendered daily report */
  .report {{ background: var(--page); border: 1px solid var(--border);
             border-radius: 8px; padding: 4px 18px 14px; }}
  .report h3 {{ font-size: 15px; margin: 14px 0 6px; }}
  .report h4 {{ font-size: 13px; margin: 14px 0 6px; text-transform: uppercase;
                letter-spacing: 0.04em; color: var(--ink-2); }}
  .report h5, .report h6 {{ font-size: 13px; margin: 12px 0 4px; }}
  .report p {{ margin: 6px 0; color: var(--ink-2); }}
  .report li {{ color: var(--ink-2); margin: 2px 0; }}
  .report table {{ border-collapse: collapse; margin: 8px 0; }}
  .report th, .report td {{ border: 1px solid var(--grid); padding: 4px 10px;
                            text-align: left; }}
  .report th {{ color: var(--ink); background: var(--surface); }}
  .report td {{ color: var(--ink-2); }}
  .report code {{ background: var(--surface); border: 1px solid var(--border);
                  border-radius: 4px; padding: 0 4px; }}
</style>
</head>
<body>
<main>
  <h1>Daily Kaizen — {html.escape(config.process_name)} {sandbox}</h1>
  <div class="sub">{day.isoformat()} · {sqdip.runs_started} run(s) started ·
    {sqdip.exceptions_total} exception(s) today</div>

  <section>
    <h2>SQDIP</h2>
    <div class="tiles">
{_sqdip_tiles(sqdip, config.sqdip_targets)}
    </div>
  </section>

  <section>
    <h2>Exception Pareto (all time) — where should the next investigation go?</h2>
{_pareto_rows(pareto)}
  </section>

  <section>
    <h2>Shared Kanban board</h2>
    <div class="board">
{_board_columns(tickets_by_bucket)}
    </div>
  </section>

{report_section}
  <p class="empty">AI Kaizen Framework — generated {_dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}</p>
</main>
</body>
</html>
"""
