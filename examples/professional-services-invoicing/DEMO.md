# Live demo runbook — AI Kaizen Framework

~10 minutes end to end. Run everything from this folder with the virtualenv
active and `ANTHROPIC_API_KEY` in the repo-root `.env`.

```bash
cd examples/professional-services-invoicing
source ../../.venv/bin/activate        # or your own venv with `pip install -e '..[llm]'`
```

To reset between rehearsals (or after the demo):

```bash
python reset_demo.py
```

---

## Act 1 — Jidoka: the line stops itself (~1 min)

```bash
python invoicing_workflow.py
```

**What happens:** the monthly invoicing run hits deliberately faulty data — a
consultant who never submitted a report, a negative-hours timesheet, an
engagement with no rate card. The two medium defects are **counted** and the
run *continues*; the missing rate card is high severity and **stops the line**
before a bad invoice is raised, raising one immediate card. Run it 2-3 times to
build up the defect counts.

**Say:** "Nothing here asked a human for permission to stop. The process
detected its own abnormality and refused to pass a defect downstream — that's
Jidoka. Notice the medium defects are counted, not turned into tickets: we
don't want a board full of raw defects, we want to *know how many* there were."

Optional beat: `python invoicing_workflow.py --sandbox` — same stops, no
tickets, no side effects. "This is how the team trials rule changes safely."

## Act 2 — The daily kata: AI prepares, humans interpret (~2 min)

```bash
python run_daily_kaizen.py --llm
```

**What happens:** Claude computes SQDIP from the run log, reads the exception
patterns, and writes the daily Kaizen summary — posted to the shared board as
the standup agenda.

**Point at:** the SQDIP table (red across the board — and the narrative
correctly says Delivery failure is the root symptom, not five separate
problems); the closing question to the team; and the **target-miss card** the
review raised — *"On {date}, 3 out of 3 invoicing runs had a missing delivery
report, against the target of <1."* "The AI doesn't decide — it prepares the
conversation, and it writes a card only where a target was actually missed."

## Act 3 — The investigation: an A3 as a flow (~5 min)

```bash
python run_investigation.py --llm
```

Pick the **missing-rate-card** ticket. Every stage pauses for you — the human
gates are non-optional. Suggested inputs (finish each answer with an empty
line):

| Stage | What to type |
|---|---|
| `frame_problem` | `In the 2026-07 run, 88.0 hours on ENG-004 could not be billed because no rate card entry exists; standard is 100% of delivered hours billable at cutoff.` |
| `collect_data` | `Checked the CRM: ENG-004 was created 2026-07-02 by sales; the commercial setup task is still sitting in the finance queue.` |
| `brainstorm_causes` | `ok` (look at Claude's drafted fishbone first — it's grounded in your observation) |
| `five_whys` — **round 1, on purpose:** | `The person who set up the engagement forgot the rate card` ⏎ `Human error` |

**The moment:** the Sensei refuses to accept it and sends you back with
socratic questions — "what in the *process* allowed a normal human action to
become a defect?" **Say:** "The AI just rejected blame as a root cause. It
gates the quality of the thinking, not just the data."

| Stage | What to type |
|---|---|
| `five_whys` — round 2: | `Hours for ENG-004 could not be billed` ⏎ `ENG-004 has no entry in the rate card` ⏎ `The engagement was activated in the CRM before commercial setup finished` ⏎ `CRM activation and rate-card creation are separate manual steps with no dependency` ⏎ `The onboarding process has no gate that blocks activation until the rate card exists` ⏎ `Engagement onboarding lacks a completeness gate before activation` |
| `design_countermeasure` | `countermeasure: add a CRM validation rule - an engagement cannot move to Active until a rate card entry exists` ⏎ `pilot: enable the rule for new engagements only for two weeks in sandbox; measure missing-rate-card exceptions` |
| `verify` | `yes: two weeks piloted, zero missing-rate-card exceptions on new engagements` |

**What happens:** the gate opens (READY), the full A3 is printed and written
back to the ticket, and the ticket closes — but only because the pilot was
verified.

## Optional act — the live board with the AI teammate working it (~3 min)

```bash
python serve_board.py --llm      # opens http://127.0.0.1:8765
```

The board is deliberately Planner-shaped (drag between progress columns, edit
descriptions, add notes, add cards — nothing Planner can't do). The **Kaizen
Teammate runs in the background**, working the board autonomously:

1. Within ~15s, watch it move exception tickets to **In progress** and fill in
   what the evidence supports — problem statements quote real recurrence data
   ("in 3 of 3 runs…"), and unknowable whys are left OPEN.
2. Open a ticket: a rendered analysis ending in **"Needs from the team"** —
   precise questions only a human can answer — with a **Conversation** thread
   below. Put your name in the "You:" box, then **add a note** answering one.
3. Wait for the next pass: the teammate folds your answer into the analysis
   and **replies in the conversation** (🤖 bubble: "Thanks — I've folded your
   notes into the analysis above…") — through to "Proposal ready for team
   review" once the chain is sound (its own analysis is gated by the Sensei
   before it posts).
4. It never closes tickets — **you** drag verified work to Done.
5. Raise your own **improvement idea** with the "Add card" box — the AI raises
   its ideas into the same bucket from the daily reflection.

**Say:** "The AI acts, asks when blocked, and the humans hold the gates.
Locally this is a JSON file; against Microsoft Planner the agents behave
identically — they just read and write tickets."

## Optional act — closing the loop: change the standard work (~2 min)

```bash
python propose_change.py
```

**What happens:** an **agent** proposes a change to its *own* standard work
(lower the Jidoka stop threshold), pilots it as a what-if against the recorded
run log — *"the current standard produced 6 line-stops; the proposed one would
produce 12"* — then tries to approve itself and is **blocked**. Only the
**process owner** can approve, which updates the standard work and versions it.

**Say:** "This is the whole point of Kaizen — the standard itself improves. The
AI can propose changes to its own prompts and rules and show the evidence, but
it can never standardize a change to itself. A named process owner holds that
gate. And every approval is versioned, so any change rolls back."

## Act 4 — The dashboard (~1 min)

```bash
python make_dashboard.py
```

**Point at:** SQDIP tiles vs targets, the exception Pareto ("where should the
next investigation go?"), the board with the investigated ticket now done, and
the daily report. **Close:** "One versioned YAML file holds the rules, the
prompts, the targets, and the humans' standard work. The team improves this
system daily — that loop, not the automation, is the product."

---

## If something goes wrong

- **No/invalid API key:** everything still runs — `--llm` degrades to the
  deterministic summaries with a visible note. The demo cannot crash on auth.
- **Weird LLM output:** re-run the command; or drop `--llm` and narrate the
  deterministic version (it makes the same structural points).
- **Muscle-memory reset:** `python reset_demo.py` restores a clean state in
  one second.
