# AI Kaizen Framework

**Human-AI Collaborative Kaizen for Agentic Systems**

**Author:** James Dwan, Catalyst Consulting · MIT License

Embed daily improvement katas, Jidoka exception management, SQDIP metrics,
5 Whys root cause analysis, and FMEA thinking into
[LangGraph](https://github.com/langchain-ai/langgraph)-based agentic workflows —
with a shared Kanban board as the workspace where humans and AI solve problems
together.

## Philosophy

AI Kaizen is the practice of intelligent human-machine partnership — where AI
and humans work together with shared awareness, perform daily improvement
katas, conduct root cause analysis, and continuously evolve both the automated
process and human standard work. The focus is on true collaboration, not
automation with occasional human oversight.

**Jidoka** — stopping the line and making a problem visible the moment an
abnormality occurs — is one pillar of that practice (it's where the partnership
begins), alongside SQDIP metrics, the daily kata, 5 Whys, FMEA, and standard
work. Kaizen is the whole: the continuous, daily improvement loop those pillars
serve.

Read the full [white paper](docs/AI-Kaizen-Framework-White-Paper.md).

## What you get

| Lean concept | In the framework |
|---|---|
| **Jidoka** (stop on abnormality) | Business-editable rules watch every node; at high severity the graph routes to an `andon` node, stops before defects flow downstream, and raises one immediate card |
| **Defects are counted, problems are chosen** | Every abnormality is recorded and counted in the run log (feeding SQDIP and the Pareto) but is **not** carded one-by-one — a call centre logs its 20-30 daily defects without 20-30 tickets |
| **Targets generate the work** | At the daily review, a **missed target** raises one problem card with the gap as its problem statement: *"On 20 July, 30 out of 1000 calls had customer complaints, against the target of <20."* Targets are config, so the metrics the team already reviews are what create the cards |
| **SQDIP** | Safety, Quality, Delivery, Inventory, Productivity computed daily from the run log, scored against your targets |
| **Daily Kaizen kata** | A Reflection Agent prepares the daily summary and improvement suggestions; humans and AI review it together |
| **FMEA** | A registry of anticipated failure modes, ranked by RPN, folded into the daily reflection |
| **Kaizen Teammate** | An autonomous agent that works the problem cards on the shared board: fills in what the evidence supports, asks the team precise questions in the ticket when blocked, and picks up their answers on its next pass. It never closes tickets — humans hold the gates |
| **Sensei coaching** | A Sensei Agent reviews 5 Whys analyses socratically — questioning vague problem statements, blame-the-person causes, and weak countermeasures. It gates the AI teammate's own proposals before they're posted, not just the humans' |
| **Improvement ideas** | A shared Ideas bucket: humans add cards from the board; the AI raises its own (deduped) suggestion cards from the daily reflection when it spots patterns |
| **Investigations as flows** | Each exception ticket can spawn its own checkpointed LangGraph: problem framing → Pareto → fishbone → 5 Whys → Sensei gate → countermeasure → pilot → verify → standardize. Human gates at every stage are non-optional; a weak analysis stops the flow the way bad data stops production |
| **Standard work as a living agreement** | One versioned YAML file holds rules, prompts, targets, *and* human standard work — editable without code changes, archived on every save |
| **Change proposals with owner approval** | Humans *and* agents propose changes to either register (agent standard work — prompts, rules, thresholds; or human standard work — the kata). A change is piloted as a what-if against the recorded run log, then **only the process owner can approve** and standardize it. Agents propose and pilot; they can never self-modify without a human owner's sign-off |
| **Safe experimentation** | Sandbox mode logs everything but creates no tickets and takes no external actions; config versioning makes every change reversible |

## Quick start

**New here? Follow the step-by-step [tutorial](docs/TUTORIAL.md)** — it covers
setup from scratch (including getting a Claude API key) and walks every part
of the demo with expected output. The short version:

```bash
git clone https://github.com/james-dwan/ai-kaizen-framework.git
cd ai-kaizen-framework
pip install -e .

cd examples/professional-services-invoicing
python invoicing_workflow.py     # watch the line stop on bad data
python run_daily_kaizen.py       # generate the daily Kaizen summary
python run_investigation.py      # interactive A3 investigation with the Sensei
python make_dashboard.py         # visual dashboard: SQDIP, Pareto, board
```

## Minimal usage

```python
from typing import TypedDict
from kaizen import KaizenConfig, KaizenGraphBuilder
from kaizen.kaizen_graph import KaizenState

class MyState(KaizenState, TypedDict, total=False):
    orders: list
    total: float

def collect(state): ...
def process(state): ...

config = KaizenConfig.load("config/kaizen_config.yaml")

builder = KaizenGraphBuilder(MyState, config)
builder.add_node("collect", collect)
builder.add_node("process", process)
builder.set_entry_point("collect")
builder.add_edge("collect", "process")
builder.set_finish_point("process")

graph = builder.compile()
result = graph.invoke({})

if result["kaizen_stopped"]:
    print("Andon:", result["kaizen_stop_reason"])
```

Abnormality rules live in the config, not the code:

```yaml
rules:
  - name: order-total-out-of-range
    description: Order total exceeds the auto-approval threshold
    condition: "state.get('total', 0) > 25000"
    severity: high          # high => Jidoka stop
    sqdip_category: safety
    nodes: [process]
```

The daily reflection:

```python
from kaizen import KaizenConfig, ReflectionAgent, RunLog, create_board, build_default_llm

config = KaizenConfig.load("config/kaizen_config.yaml")
agent = ReflectionAgent(
    config=config,
    runlog=RunLog(),
    board=create_board(config.kanban),
    llm=build_default_llm(),   # optional: Claude writes the narrative
)
summary = agent.daily_reflection()   # SQDIP + exceptions + suggestions -> board
```

## Kanban providers

The board is the shared workspace, so it should live where your team already
works. Microsoft 365 is first-class; the core works with anything.

- **`local`** (default) — a JSON file; zero configuration, ideal for
  development and sandbox experiments
- **`planner`** — Microsoft Planner via Microsoft Graph
  (`pip install 'ai-kaizen-framework[m365]'`). Full round-trip: statuses map to
  Planner's progress columns (dragging a task IS the status change), analyses
  live in task notes, checklists and priorities carry over, every write is
  etag-guarded against concurrent human edits, and proposal cards are
  *assigned* to the process owner so approvals sit in their own Teams view.
  The autonomous teammate runs unchanged against it.
- **`lists`** — Microsoft Lists (SharePoint) via Microsoft Graph
- Anything else — implement the three-method `KanbanBoard` ABC

Authentication stays in your hands: pass any `token_provider` callable (device
code, client secret, managed identity — whatever your tenant requires).

## Optional extras

```bash
pip install 'ai-kaizen-framework[m365]'   # Microsoft Planner / Lists boards
pip install 'ai-kaizen-framework[llm]'    # Claude-written daily reflections
pip install 'ai-kaizen-framework[dev]'    # tests + lint
```

## Repository layout

```
src/kaizen/
  kaizen_graph.py        # KaizenGraphBuilder — LangGraph + Jidoka
  exception_handler.py   # rules, ExceptionRecord, 5 Whys, FMEA
  reflection_agent.py    # SQDIP + daily Kaizen summaries
  kanban_integration.py  # local / Planner / Lists boards
  config.py              # versioned YAML config layer
  runlog.py              # append-only event log
docs/                    # white paper + architecture
examples/professional-services-invoicing/
```

## Sensei coaching

```python
from kaizen import KaizenConfig, SenseiAgent, create_board

config = KaizenConfig.load("config/kaizen_config.yaml")
sensei = SenseiAgent(config)                    # add llm=... for richer questions
sensei.coach_open_exceptions(create_board(config.kanban))
# Every open exception ticket now carries socratic questions about its 5 Whys.
```

## Closing the loop: proposing changes to standard work

The config is the single **standard-work register** — agent standard work
(prompts, rules, thresholds, targets) and human standard work (the daily kata)
in one versioned file. Either humans or agents can propose changes; a change is
piloted, then a **process owner** approves it. Agents propose and pilot; only
the owner standardizes.

```python
from kaizen import KaizenConfig, ProposalRegistry, RunLog, create_board

config = KaizenConfig.load("config/kaizen_config.yaml")
registry = ProposalRegistry(config, runlog=RunLog(), board=create_board(config.kanban))

# An agent proposes a change to its OWN standard work
p = registry.propose(
    title="Lower the stop threshold to medium",
    path=["jidoka", "stop_on_severity"], new_value="medium",
    rationale="Medium defects recur; catch them at the line.",
    proposed_by="agent:teammate",
)
registry.pilot(p.id)                       # what-if replay of the recorded run log
# registry.approve(p.id, owner="agent:teammate")   # -> PermissionError: agents can't approve
registry.approve(p.id, owner=config.process_owner)  # owner standardizes; config versioned
```

See it run: `examples/professional-services-invoicing/propose_change.py`.

## Investigations as flows

```python
from langgraph.types import Command
from kaizen import InvestigationGraphBuilder, KaizenConfig, RunLog, create_board

config = KaizenConfig.load("config/kaizen_config.yaml")
board = create_board(config.kanban)

builder = InvestigationGraphBuilder(config, board, runlog=RunLog())
flow = builder.build()                      # pass a persistent checkpointer in production
thread = {"configurable": {"thread_id": ticket_id}}   # the ticket IS the investigation

state = flow.invoke(builder.start_input(ticket_id), thread)
while "__interrupt__" in state:             # every stage waits for a human
    answer = ask_the_team(state["__interrupt__"][0].value)
    state = flow.invoke(Command(resume=answer), thread)
# On completion, the full A3 is written back to the ticket.
```

See it interactively: `examples/professional-services-invoicing/run_investigation.py`.

## Roadmap

- **Specialist kata agents.** A roster of agents good at different jobs —
  problem-statement writing, Pareto analysis, fishbone facilitation, cause
  brainstorming, pilot design, team communications — orchestrated within the
  investigation flow.
- More Kanban providers (Trello, Jira, GitHub Projects) and SQDIP trend
  analysis across days.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Kanban adapters and new examples are
especially welcome.

## License

MIT — see [LICENSE](LICENSE).

---

© 2026 James Dwan
