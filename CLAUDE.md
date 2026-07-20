# AI Kaizen Framework - Build Prompt for Claude Code

You are an expert LangGraph developer and Lean practitioner with deep knowledge of human-AI collaboration.

Build the complete open-source **AI Kaizen Framework** — a framework for embedding daily human-AI collaborative Kaizen into LangGraph agentic systems.

**Philosophy** (use this exactly and emphasize it):
AI Kaizen is the practice of intelligent human-machine partnership — where AI and humans work together with shared awareness, perform daily improvement katas, conduct root cause analysis, and continuously evolve both the automated process and human standard work. The focus is on true collaboration, not automation with occasional human oversight.

**Requirements**:
- Integrate Jidoka (stop on abnormality, make problems visible), Daily Kaizen katas, SQDIP metrics (Safety, Quality, Delivery, Inventory, Productivity), 5 Whys root cause analysis, and FMEA thinking.
- Support shared Kanban (Microsoft Planner or Lists preferred) for collaborative problem-solving.
- Allow business users to edit rules, prompts, and human standard work without code changes (use config layer).
- Include a Reflection Agent that generates daily Kaizen summaries with SQDIP analysis and improvement suggestions.
- Automatic exception detection → structured Kanban ticket creation.
- Safe experimentation (versioning, sandbox mode).

**Deliverables** (generate full file contents with clear paths):
1. `README.md` — professional, with attribution to James Dwan, Catalyst Consulting.
2. `docs/AI-Kaizen-Framework-White-Paper.md` — use the full polished white paper content.
3. Core code in `src/kaizen/`:
   - `__init__.py`
   - `kaizen_graph.py`
   - `reflection_agent.py`
   - `exception_handler.py`
   - `kanban_integration.py`
4. `examples/professional-services-invoicing/` — a complete working example.
5. `LICENSE` (MIT), `CONTRIBUTING.md`, `.gitignore`, `pyproject.toml`.

Make everything production-ready, well-documented, modular, and easy to extend. Prioritize Microsoft 365 integration for low friction, but keep the core framework usable with other Kanban systems.

Output in structured format with clear file paths (e.g., ```markdown
# File: README.md
content here
```).

Start building the framework now.
