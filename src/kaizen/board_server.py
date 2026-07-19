"""Interactive local Kanban board — the collaboration surface for development.

A zero-dependency web app over :class:`LocalKanbanBoard`, deliberately limited
to interactions that also exist in Microsoft Planner/Teams:

- drag tickets between Open / In progress / Done lanes (Planner: group by
  progress)
- open a ticket to read and edit its description in place (Planner: task notes)
- add timestamped notes (Planner: comments)

There is intentionally no "invoke the AI" button — that doesn't exist in
Planner either. The agents (Kaizen Teammate, Sensei, Reflection) interact with
the board autonomously by reading and writing tickets; your channel to them is
the ticket itself. The page auto-refreshes, so you see the agents working.

Usage::

    from kaizen.board_server import serve_board
    serve_board(config, board)
"""

from __future__ import annotations

import datetime as _dt
import html as _html
import json
import re
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .config import KaizenConfig
from .kanban_integration import KanbanBoard

STATUSES = ["open", "in_progress", "done"]


def serve_board(
    config: KaizenConfig,
    board: KanbanBoard,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    """Serve the interactive board (blocks until Ctrl-C)."""
    server = make_server(config, board, host, port)
    url = f"http://{host}:{server.server_address[1]}/"
    print(f"Kaizen board: {url}  (Ctrl-C to stop)")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def make_server(config, board, host="127.0.0.1", port=8765) -> ThreadingHTTPServer:

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep the demo console quiet
            pass

        # -- helpers -----------------------------------------------------
        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload: Any, code: int = 200) -> None:
            self._send(code, json.dumps(payload).encode(), "application/json")

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(length) or b"{}")

        def _ticket(self, ticket_id: str):
            for t in board.list_tickets():
                if t.id == ticket_id:
                    return t
            return None

        # -- routes ------------------------------------------------------
        def do_GET(self):
            if self.path == "/":
                self._send(200, _page(config).encode(), "text/html; charset=utf-8")
            elif self.path == "/api/state":
                self._json({
                    "process": config.process_name,
                    "sandbox": config.sandbox,
                    "statuses": STATUSES,
                    "buckets": list(config.kanban.get("buckets", {}).values()) or ["Exceptions"],
                    "tickets": [t.to_dict() for t in board.list_tickets()],
                })
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self):
            if self.path == "/api/tickets":  # add a card (Planner: "Add task")
                body = self._body()
                title = str(body.get("title", "")).strip()
                if not title:
                    self._json({"error": "title required"}, 400)
                    return
                from .kanban_integration import KanbanTicket

                ticket = board.create_ticket(KanbanTicket(
                    title=title[:250],
                    description=str(body.get("description", "")).strip(),
                    bucket=str(body.get("bucket", "Improvement Ideas")),
                    labels=["human-raised"],
                    priority="low",
                ))
                self._json(ticket.to_dict())
                return
            match = re.fullmatch(r"/api/tickets/([\w-]+)(/note)?", self.path)
            if not match:
                self._json({"error": "not found"}, 404)
                return
            ticket_id, action = match.group(1), match.group(2)
            ticket = self._ticket(ticket_id)
            if ticket is None:
                self._json({"error": "no such ticket"}, 404)
                return
            try:
                if action == "/note":
                    text = str(self._body().get("text", "")).strip()
                    if text:
                        stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
                        board.update_ticket(
                            ticket_id,
                            description=ticket.description + f"\n\n**Note ({stamp}):** {text}",
                        )
                else:  # field updates: status / bucket / description
                    changes = {k: v for k, v in self._body().items()
                               if k in ("status", "bucket", "description")}
                    if changes.get("status") not in (None, *STATUSES):
                        self._json({"error": "bad status"}, 400)
                        return
                    if changes:
                        board.update_ticket(ticket_id, **changes)
                self._json(self._ticket(ticket_id).to_dict())
            except Exception as exc:  # surface errors to the UI, don't die
                self._json({"error": str(exc)}, 500)

    return ThreadingHTTPServer((host, port), Handler)


# ----------------------------------------------------------------------
# The single-page app
# ----------------------------------------------------------------------

def _page(config: KaizenConfig) -> str:
    title = _html.escape(config.process_name)
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kaizen board — """ + title + """</title>
<style>
  :root {
    color-scheme: light;
    --page:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b; --ink-2:#52514e;
    --muted:#898781; --border:rgba(11,11,11,0.10); --accent:#2a78d6;
    --critical:#d03b3b; --serious:#ec835a; --good:#006300;
  }
  @media (prefers-color-scheme: dark) {
    :root { color-scheme: dark;
      --page:#0d0d0d; --surface:#1a1a19; --ink:#fff; --ink-2:#c3c2b7;
      --border:rgba(255,255,255,0.10); --accent:#3987e5; --good:#0ca30c; }
  }
  * { box-sizing:border-box; }
  body { margin:0; padding:20px; background:var(--page); color:var(--ink);
         font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; }
  h1 { font-size:18px; margin:0 0 14px; }
  .lanes { display:grid; grid-template-columns:repeat(3,1fr); gap:14px; }
  .lane { background:var(--surface); border:1px solid var(--border); border-radius:10px;
          padding:12px; min-height:300px; }
  .lane.drag { outline:2px dashed var(--accent); }
  .lane h2 { font-size:13px; margin:0 0 10px; color:var(--ink-2);
             text-transform:uppercase; letter-spacing:.05em; }
  .card { background:var(--page); border:1px solid var(--border); border-radius:8px;
          padding:10px 12px; margin-bottom:8px; cursor:grab; }
  .card:hover { border-color:var(--accent); }
  .chip { display:inline-block; font-size:10px; font-weight:600; border-radius:999px;
          padding:1px 7px; border:1px solid var(--border); color:var(--ink-2);
          margin-right:4px; text-transform:uppercase; }
  .chip.high,.chip.urgent { border-color:var(--critical); color:var(--critical); }
  .chip.medium { border-color:var(--serious); color:var(--serious); }
  .card-title { margin-top:5px; font-size:13px; }
  /* modal */
  #overlay { display:none; position:fixed; inset:0; background:rgba(0,0,0,.45);
             align-items:center; justify-content:center; padding:20px; }
  #overlay.show { display:flex; }
  #modal { background:var(--surface); border-radius:12px; width:min(760px,100%);
           max-height:88vh; display:flex; flex-direction:column; padding:18px 20px; }
  #modal h3 { margin:0 0 10px; font-size:15px; padding-right:30px; }
  #desc { flex:1; min-height:300px; width:100%; resize:vertical; font:12px/1.5 ui-monospace,Menlo,monospace;
          background:var(--page); color:var(--ink); border:1px solid var(--border);
          border-radius:8px; padding:10px; }
  .row { display:flex; gap:8px; margin-top:10px; flex-wrap:wrap; align-items:center; }
  button { font:600 13px system-ui; border-radius:8px; border:1px solid var(--border);
           background:var(--page); color:var(--ink); padding:7px 14px; cursor:pointer; }
  button.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
  button:disabled { opacity:.5; cursor:wait; }
  #note { flex:1; min-width:200px; font:13px system-ui; padding:7px 10px;
          border:1px solid var(--border); border-radius:8px;
          background:var(--page); color:var(--ink); }
  #close { position:absolute; margin-left:auto; }
  #modal .top { display:flex; justify-content:space-between; align-items:baseline; }
  #status-msg { color:var(--good); font-size:12px; }
  .empty { color:var(--muted); font-size:13px; }
</style>
</head>
<body>
<h1>Kaizen board — """ + title + """</h1>
<div class="row" style="margin-bottom:14px">
  <input id="new-title" placeholder="Raise an idea or observation…"
         style="flex:1;min-width:260px;font:13px system-ui;padding:7px 10px;
                border:1px solid var(--border);border-radius:8px;
                background:var(--surface);color:var(--ink)">
  <select id="new-bucket" style="font:13px system-ui;padding:7px;border-radius:8px;
                border:1px solid var(--border);background:var(--surface);color:var(--ink)"></select>
  <button id="add-card">Add card</button>
</div>
<div class="lanes" id="lanes"></div>

<div id="overlay">
  <div id="modal">
    <div class="top"><h3 id="m-title"></h3><button id="close">✕</button></div>
    <textarea id="desc" spellcheck="false"></textarea>
    <div class="row">
      <button class="primary" id="save">Save analysis</button>
      <span id="status-msg"></span>
    </div>
    <div class="row">
      <input id="note" placeholder="Add a thought / observation…">
      <button id="add-note">Add note</button>
    </div>
  </div>
</div>

<script>
const LANES = {open:"Open", in_progress:"In progress", done:"Done"};
let tickets = [], current = null;

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error((await r.json()).error || r.status);
  return r.json();
}

async function refresh() {
  const s = await api("/api/state");
  tickets = s.tickets;
  const sel = document.getElementById("new-bucket");
  if (!sel.options.length) {
    for (const b of s.buckets) {
      const o = document.createElement("option");
      o.value = o.textContent = b;
      if (b === "Improvement Ideas") o.selected = true;
      sel.appendChild(o);
    }
  }
  render();
  if (current) {
    const t = tickets.find(t => t.id === current.id);
    if (t) { current = t; fill(t); }
  }
}

function render() {
  const root = document.getElementById("lanes");
  root.innerHTML = "";
  for (const [status, label] of Object.entries(LANES)) {
    const lane = document.createElement("div");
    lane.className = "lane"; lane.dataset.status = status;
    lane.innerHTML = `<h2>${label} <span class="empty">${tickets.filter(t=>t.status===status).length}</span></h2>`;
    lane.addEventListener("dragover", e => { e.preventDefault(); lane.classList.add("drag"); });
    lane.addEventListener("dragleave", () => lane.classList.remove("drag"));
    lane.addEventListener("drop", async e => {
      e.preventDefault(); lane.classList.remove("drag");
      const id = e.dataTransfer.getData("text/plain");
      await api(`/api/tickets/${id}`, {method:"POST", body:JSON.stringify({status})});
      refresh();
    });
    for (const t of tickets.filter(t => t.status === status)) {
      const card = document.createElement("div");
      card.className = "card"; card.draggable = true;
      card.innerHTML = `<span class="chip ${t.priority}">${t.priority}</span>` +
                       `<span class="chip">${t.bucket}</span>` +
                       `<div class="card-title"></div>`;
      card.querySelector(".card-title").textContent = t.title;
      card.addEventListener("dragstart", e => e.dataTransfer.setData("text/plain", t.id));
      card.addEventListener("click", () => open(t));
      lane.appendChild(card);
    }
    if (!tickets.some(t => t.status === status))
      lane.insertAdjacentHTML("beforeend", '<p class="empty">empty</p>');
    root.appendChild(lane);
  }
}

function fill(t) {
  document.getElementById("m-title").textContent = t.title;
  document.getElementById("desc").value = t.description;
}
function open(t) { current = t; fill(t); document.getElementById("overlay").classList.add("show"); }
function msg(text) {
  const el = document.getElementById("status-msg");
  el.textContent = text; setTimeout(() => el.textContent = "", 2500);
}

document.getElementById("close").onclick = () => document.getElementById("overlay").classList.remove("show");
document.getElementById("overlay").addEventListener("click", e => {
  if (e.target.id === "overlay") document.getElementById("overlay").classList.remove("show");
});
document.getElementById("save").onclick = async () => {
  await api(`/api/tickets/${current.id}`, {method:"POST",
    body: JSON.stringify({description: document.getElementById("desc").value})});
  msg("Saved."); refresh();
};
document.getElementById("add-card").onclick = async () => {
  const input = document.getElementById("new-title");
  if (!input.value.trim()) return;
  await api("/api/tickets", {method:"POST", body: JSON.stringify({
    title: input.value, bucket: document.getElementById("new-bucket").value})});
  input.value = ""; refresh();
};
document.getElementById("add-note").onclick = async () => {
  const note = document.getElementById("note");
  if (!note.value.trim()) return;
  await api(`/api/tickets/${current.id}/note`, {method:"POST",
    body: JSON.stringify({text: note.value})});
  note.value = ""; msg("Note added."); refresh();
};

refresh();
setInterval(refresh, 4000);   // pick up agent-side changes while the page is open
</script>
</body>
</html>
"""
