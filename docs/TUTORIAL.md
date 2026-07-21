# Tutorial: set up and run the AI Kaizen Framework demo

This is a from-scratch, step-by-step guide. In about 30 minutes you will set up
the framework, break an invoicing process on purpose, watch the line stop
itself, review the day with an AI-written Kaizen summary, collaborate with an
autonomous AI teammate on a shared Kanban board, and approve a change to the
process's standard work as its owner.

No Microsoft 365 tenant is needed — everything runs locally. A Claude API key
is optional but strongly recommended: without one every step still works with
deterministic output; with one, the agents write real analysis.

> Presenting this to an audience instead of learning it yourself? Use the
> presenter's runbook with talking points:
> [`examples/professional-services-invoicing/DEMO.md`](../examples/professional-services-invoicing/DEMO.md).

---

## Part 0 — Setup

### 0.1 Prerequisites

- **Python 3.10 or newer** (3.11+ recommended). Check with `python3 --version`.
  On macOS, `brew install python@3.11` if needed.
- **git**
- A terminal and about 10 minutes

### 0.2 Clone and install

```bash
git clone https://github.com/james-dwan/ai-kaizen-framework.git
cd ai-kaizen-framework

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -e '.[llm,dev]'
```

The `llm` extra installs `langchain-anthropic` (for Claude-written analysis);
`dev` installs `pytest` so you can verify the install:

```bash
pytest -q
```

You should see all tests pass (`33 passed` at the time of writing). If they do,
the framework is working — everything from here on is about seeing it in action.

### 0.3 Get a Claude API key (recommended)

The agents degrade gracefully without a key, but the demo is far more
compelling with one — the daily reflections, root cause questions, and
fishbone drafts are then written live by Claude.

1. Go to **[platform.claude.com](https://platform.claude.com)** and sign in
   (or create an account).
2. Open **API Keys** (under Settings) and click **Create Key**. Give it a name
   like `ai-kaizen-demo` and copy the key — it's shown only once and starts
   with `sk-ant-`.
3. Make sure the account has credit: **Settings → Billing**. A full run-through
   of this tutorial uses Claude Opus and costs on the order of tens of cents.

### 0.4 Store the key in a `.env` file

Never export the key in your shell profile or paste it into code. The repo
uses a git-ignored `.env` file at the repository root, loaded automatically by
the example scripts:

```bash
cp .env.example .env
```

Edit `.env` and paste your key after the `=`:

```
ANTHROPIC_API_KEY=sk-ant-...your key...
```

`.env` is listed in `.gitignore`, so it can never be committed.

### 0.5 Verify the key

```bash
python -c "
from kaizen import load_env, build_default_llm
load_env('.')
print(build_default_llm().invoke('Reply with exactly: OK').content)"
```

Expected output: `OK`. If you get a `401 authentication_error`, the key was
pasted wrong; if "Could not resolve authentication method", the `.env` file
isn't in the repository root or the line is malformed.

**No key? No problem.** Skip 0.3–0.5 and drop the `--llm` flag from every
command below. Each step prints a clear note and uses deterministic output —
the demo never crashes on a missing key.

### 0.6 Go to the example

All remaining commands run from the example directory:

```bash
cd examples/professional-services-invoicing
```

The process you'll be running is monthly invoicing for a consulting firm:
collect delivery reports → validate → aggregate hours → calculate the invoice
→ raise it. The sample data is deliberately faulty: one consultant never
submitted a report, one report has negative hours, and one engagement has no
rate card.

---

## Part 1 — Jidoka: the line stops itself

Run the invoicing workflow **three times** (the repetition matters — you'll see
why in Part 2):

```bash
python invoicing_workflow.py
python invoicing_workflow.py
python invoicing_workflow.py
```

Each run prints something like:

```
=== Invoicing run — period 2026-07  ===
  [MEDIUM  ] validate_reports: missing-delivery-reports — One or more consultants have not submitted a delivery report
  [MEDIUM  ] validate_reports: negative-or-zero-hours — A delivery report contains zero or negative billable hours
  [HIGH    ] calculate_invoice: missing-rate-card — An engagement has no rate card entry, so hours cannot be billed -> ticket 3066e295dba5
  [HIGH    ] calculate_invoice: invoice-over-approval-threshold — Invoice total exceeds the auto-approval threshold -> ticket ff417d8f3971

JIDOKA STOP: calculate_invoice: missing-rate-card — An engagement has no rate card entry, so hours cannot be billed
The line stopped before raising the invoice. Review the Problems column on the board...
```

**What just happened:**

- Every node of the LangGraph workflow is watched by *abnormality rules* —
  plain YAML in `config/kaizen_config.yaml`, editable by business users.
- The two **medium** defects were **counted, not carded** — they're recorded in
  the run log (`kaizen_runlog.jsonl`) but create no tickets. A call centre
  with 30 defects across 1000 calls wants to *know* that, not drown its board
  in 30 tickets.
- The two **high** defects **stopped the line** (Jidoka) before a bad invoice
  went out, and each raised one card. Notice the second and third runs didn't
  create duplicate cards — recurrences of the same problem aggregate onto the
  existing card.

Look at the raw artifacts if you're curious: `kaizen_board.json` (the board)
and `kaizen_runlog.jsonl` (the event log — the single source of truth for all
metrics).

**Optional:** `python invoicing_workflow.py --sandbox` — same stops, but no
tickets and no side effects. This is how the team trials rule changes safely.

---

## Part 2 — The daily review: SQDIP, and a missed target becomes a card

```bash
python run_daily_kaizen.py --llm
```

This takes 30–60 seconds (Claude is writing the reflection). You'll get:

- An **SQDIP table** — Safety, Quality, Delivery, Inventory, Productivity,
  computed from the run log and scored against the targets in the config.
- A **Reflection** written by Claude. A good one will point out that the
  Delivery failure is the root symptom (the stops), pick the most *upstream*
  problem for a 5 Whys, and end with a question for the team.
- A **target-miss card**. The config defines a target — fewer than 1 missing
  delivery report — and your three runs missed it, so the review raised
  exactly one problem card:

  > On 20 July, 3 out of 3 invoicing runs had a missing delivery report,
  > against the target of <1.

  This is the Lean model: *defects are counted; a card is written when a
  target is missed*, with the gap as the problem statement.
- Possibly one or two **Improvement Ideas** cards raised by the AI (deduped —
  re-running never spams the board).

The full report is saved to `kaizen_reports/` and posted to the board's
"Daily Kaizen" bucket as the standup agenda.

---

## Part 3 — The board, with an AI teammate working it

```bash
python serve_board.py --llm
```

Your browser opens `http://127.0.0.1:8765` — a Kanban board with Open /
In progress / Done lanes. **Keep the terminal visible too**: the Kaizen
Teammate runs in the background and reports every pass:

```
[teammate] watching the board every 15s (Ctrl-C to stop)
[teammate 10:14:03] updated 3 ticket(s)
[teammate 10:14:35] pass complete — no new input from the team
```

Now collaborate with it:

1. **Put your name in the "You:" box** (top right) — your notes will be posted
   under it, so it's always clear who said what.
2. **Watch the first pass** (it takes a minute — real investigations). The
   problem cards move to *In progress* and fill with analysis. Open one: the
   analysis is a rendered document — problem statement quoting real evidence
   ("In 3 of 3 invoicing runs…"), whys the evidence can't support honestly
   marked OPEN, and **"Needs from the team"** — precise questions only a human
   can answer. (Editing the raw text is behind the *Edit analysis* button.)
3. **Answer one in the Conversation.** Type a note like
   `none of us ever gets reminders` and click *Add note* — it appears as a
   🧑 bubble under your name.
4. **Wait ~30 seconds.** The teammate detects your note, folds it into the
   analysis, and **replies in the conversation** as a 🤖 bubble — "Thanks —
   I've folded your notes into the analysis above…" — continuing until
   *"Proposal ready for team review."*
5. **You close it, not the AI.** When a countermeasure is verified, *you* drag
   the card to Done. The teammate never closes work itself.
6. **Raise your own idea** with the "Add card" box at the top.

The board deliberately offers only interactions Microsoft Planner also has
(drag between progress columns, edit notes, add comments/cards) — there's no
"ask the AI" button, because the agents work the board autonomously, exactly
as they would against a real Planner plan.

`Ctrl-C` in the terminal stops the board and the teammate.

---

## Part 4 — A guided root cause investigation (A3 as a flow)

```bash
python run_investigation.py --llm
```

Pick the **missing-rate-card** ticket. This walks a full structured
investigation — problem framing → Pareto → fishbone → 5 Whys → countermeasure
→ pilot → verify — pausing at *every* stage for your input (finish each answer
with an empty line).

**Do this on purpose:** when it asks for the 5 Whys, answer:

```
The person who set up the engagement forgot the rate card
Human error
```

The **Sensei** will refuse to accept it and send you back with socratic
questions — *"what in the process allowed a normal human action to become a
defect?"* Blame is not a root cause. Then give it a real causal chain (the
[DEMO runbook](../examples/professional-services-invoicing/DEMO.md#act-3--the-investigation-an-a3-as-a-flow-5-min)
has a worked set of answers for every stage). On completion the finished A3 is
written back to the ticket.

---

## Part 5 — Closing the loop: change the standard work, with governance

```bash
python propose_change.py
```

Watch the output carefully — this is the framework's governance model in
30 seconds:

```
Process owner (only they can approve): priya.nair

[proposed] Lower the Jidoka stop threshold to medium
  by agent:teammate · agent standard work · 'high' → 'medium'

[piloted]
  Over 2026-07-20, the current standard produced **6** line-stop(s); the
  proposed standard would produce **12** — 6 more.

[gate] agent tried to approve → blocked: Only a human process owner may approve
a change to standard work.

[approved] by priya.nair
  standard work updated: jidoka.stop_on_severity = 'medium'
  config versioned to v2 (previous archived in config_history/)
```

**What just happened:** an *agent* proposed a change to its *own* standard
work, piloted it as a what-if replay of the recorded run log (real evidence,
no re-running the process), tried to approve itself and was **hard-blocked** —
then the named process owner approved, which updated the standard and
versioned it (rollback is a previous file in `config_history/`). Humans and
agents can both propose changes to either the agents' standard work (prompts,
rules, thresholds) or the humans' (the daily kata); only the owner
standardizes.

---

## Part 6 — The dashboard

```bash
python make_dashboard.py
```

A self-contained HTML page opens: SQDIP tiles scored against targets, the
exception Pareto ("where should the next investigation go?"), the Kanban board
with everything you just did, and the latest daily report. No server, no
dependencies — attach it to an email if you like.

---

## Resetting your data

Everything the demo writes — the board, run log, reports, dashboard, proposals
— is git-ignored working state, kept in the example folder. To wipe it and
start fresh (end of a session, between rehearsals, or after experimenting):

```bash
python reset_demo.py
```

It prints exactly what it removed and never touches the committed sample data,
config, or code. (If the board server or an investigation is still running,
`Ctrl-C` it first.)

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `--llm` prints "unavailable… deterministic summary below" | No/invalid API key. Check `.env` is at the repo root and re-run the Part 0.5 verification. The demo continues either way. |
| `401 invalid x-api-key` | The key was pasted incorrectly or revoked — create a new one in the console. |
| Board page won't open / port in use | `python serve_board.py --llm --port 8899` |
| Teammate prints nothing | You started without `--llm`. The heartbeat lines only appear when the teammate is enabled. |
| Teammate says "pass complete — no new input" after your note | Notes only trigger re-work on cards in the **Problems** bucket; also give it up to one full interval (15s). |
| Tests fail on install | Check `python3 --version` ≥ 3.10 and that the venv is activated. |

## Where to go next

- **Hook it up to your real process** — the **[adoption guide](ADOPTION.md)**
  covers connecting your workflow (LangGraph or not), writing real rules and
  targets, the Microsoft Planner/tenant setup, running the agents as services,
  governance, and a Lean-style rollout plan.
- **Read the [white paper](AI-Kaizen-Framework-White-Paper.md)** for the
  philosophy, and [`architecture.md`](architecture.md) for how the pieces fit.
