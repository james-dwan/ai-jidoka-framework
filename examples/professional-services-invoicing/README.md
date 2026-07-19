# Example: Professional Services Invoicing

A complete, runnable example of the AI Jidoka Framework applied to a monthly
invoicing process for a professional services firm.

## The process

```
load_reports → validate_reports → aggregate_hours → calculate_invoice → raise_invoice
```

Each node is a plain Python function. The framework wraps every node with the
abnormality rules defined in [`config/kaizen_config.yaml`](config/kaizen_config.yaml):

| Rule | Severity | What happens |
|---|---|---|
| `missing-delivery-reports` | medium | Ticket created, run continues |
| `negative-or-zero-hours` | medium | Invalid reports excluded + ticketed, run continues |
| `missing-rate-card` | high | **Jidoka stop** — hours would go unbilled |
| `invoice-over-approval-threshold` | high | **Jidoka stop** — human approval required |
| `low-utilisation-warning` | low | Logged for the daily reflection |

The bundled sample data deliberately contains problems: one consultant hasn't
submitted a report, one report has negative hours, and one engagement has no
rate card. Run it and watch the line stop before a bad invoice goes out.

## Run it

From the repository root:

```bash
pip install -e .
cd examples/professional-services-invoicing

# 1. Run the invoicing workflow — exceptions become tickets, the line stops
python invoicing_workflow.py

# 2. Generate the daily Kaizen summary (SQDIP + reflection + kata agenda)
python run_daily_kaizen.py

# 3. Run a root cause investigation on one of the exception tickets —
#    an interactive A3-as-a-flow with the Sensei gating your 5 Whys
python run_investigation.py

# 4. Generate the visual dashboard (SQDIP vs targets, exception Pareto,
#    the Kanban board, and the latest daily report) — opens in your browser
python make_dashboard.py

# 5. Serve the live board with the autonomous Kaizen Teammate working it:
#    it advances every exception card as far as the evidence allows, asks
#    the team precise questions in the ticket, and picks up your notes on
#    its next pass. You drag verified work to Done — it never closes tickets.
python serve_board.py --llm

# Optional: have Claude write the reflection narrative and draft fishbones
pip install '.[llm]'
cp ../../.env.example ../../.env    # put your ANTHROPIC_API_KEY in .env (git-ignored)
python run_daily_kaizen.py --llm
python run_investigation.py --llm

# Optional: trial changes safely — no tickets, no invoice raised
python invoicing_workflow.py --sandbox
```

Artifacts land next to the scripts:

- `kaizen_board.json` — the shared Kanban board (local provider; swap in
  Microsoft Planner or Lists via the config)
- `kaizen_runlog.jsonl` — the event log the SQDIP metrics are computed from
- `kaizen_reports/kaizen-YYYY-MM-DD.md` — daily Kaizen summaries

## The investigation flow

`run_investigation.py` turns an exception ticket into a structured
investigation — a living A3 that walks:

```
frame_problem → collect_data (Pareto) → brainstorm_causes (fishbone)
    → five_whys → sensei_gate → design_countermeasure → verify → standardize
           ^__________(needs work)_________|
```

Every stage pauses for your input — the human gates are non-optional. Try
answering the 5 Whys with "the consultant was careless": the Sensei will send
you back with socratic questions about the *process*. After three rounds you
may explicitly override the Sensei, but the override is recorded in the run
log. On completion the full A3 is written back to the ticket and the ticket is
closed (only if the countermeasure was verified).

## The daily kata

1. AI prepares: `run_daily_kaizen.py` posts the summary to the board.
2. Humans and AI review SQDIP together; every stop is inspected.
3. One exception pattern gets a 5 Whys (the scaffold is already in the ticket).
4. One small countermeasure is agreed — often just an edit to
   `kaizen_config.yaml`: a rule threshold, a prompt wording, a kata step.
5. Changes are trialed with `--sandbox`, then standardized. `KaizenConfig.save()`
   versions the file automatically, so every experiment is reversible.

That loop — not the automation — is the point.
