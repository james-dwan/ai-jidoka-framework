# Contributing to the AI Kaizen Framework

Thank you for wanting to improve this project — continuous improvement is
rather the point.

## Ways to contribute

- **Kanban providers** — adapters for Trello, Jira, GitHub Projects, Azure
  Boards, etc. Implement the `KanbanBoard` ABC in
  `src/kaizen/kanban_integration.py`; three methods is all it takes.
- **Examples** — real processes (order-to-cash, claims handling, content
  pipelines) under `examples/`, each with its own config and README.
- **Reflection improvements** — better SQDIP computations, trend analysis
  across days, richer FMEA integration.
- **Docs** — clearer explanations of the daily kata, case studies, diagrams
  for `docs/architecture.md`.
- **Bug reports** — a minimal reproduction and your config (redacted) helps
  enormously.

## Development setup

```bash
git clone https://github.com/james-dwan/ai-kaizen-framework.git
cd ai-kaizen-framework
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pytest
```

## Guidelines

1. **Keep the core dependency-light.** `langgraph` and `pyyaml` only; anything
   provider-specific (Graph API, LLM clients) goes behind an optional extra
   and a lazy import.
2. **Config over code.** If a behavior could reasonably be tuned by a business
   user, expose it in `kaizen_config.yaml` rather than a constructor argument.
3. **Everything observable.** New behaviors should write to the `RunLog` so
   the Reflection Agent can see them.
4. **Degrade gracefully.** The improvement loop must keep working without an
   LLM, without Microsoft 365, and in sandbox mode.
5. **Style** — `ruff` clean, type hints on public APIs, docstrings that explain
   the *Lean intent* of a component, not just its mechanics.

## Pull requests

- One improvement per PR (small countermeasures, verified — you know the drill).
- Include or update tests for behavior changes.
- Update the relevant README/docs section in the same PR.

## Conduct

Be respectful, assume good intent, critique ideas rather than people. Root
cause analysis applies to process problems, never to blaming individuals.
