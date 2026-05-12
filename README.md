# LLM-Mediated HRC Task Planner

A research prototype for **LLM-mediated human-robot collaboration (HRC)**. A human and a Hello Robot Stretch RE1 collaborate on a physical ingredient-swap task. A large language model (Llama via Groq) mediates the planning conversation — translating natural language preferences and constraints into formal PDDL modifications, which the Fast Downward planner uses to generate an optimal task assignment.

---

## Overview

### The Task

**Ingredient swap** between two rooms (kitchen and pantry):

| Start | Goal |
|---|---|
| Bread and ham in the **pantry** | Bread and ham in the **kitchen** |
| Cheese and lettuce in the **kitchen** | Cheese and lettuce in the **pantry** |

Two agents divide the steps: **p1** (human) and **p2** (robot Stretch).

### Three-Phase Interaction

1. **PRE_TASK** — Human and robot discuss the plan. The human states preferences and limitations in plain language; the LLM extracts structured constraints, and the planner regenerates the plan automatically.
2. **EXECUTION** — The robot executes the plan. A live browser dashboard lets the observer track progress and update world state step by step. On failure or user stop, the system replans from the current physical state.
3. **POST_TASK** — Human and robot debrief. The robot suggests improvements and extracts a session note saved to persistent memory for future runs.

### Dashboard

A live browser interface at **http://localhost:5050** shows:
- **Step Tracker** — interactive plan with Mark Done / Undo buttons per step; progress bar
- **Constraints & Preferences** — the live knowledge base extracted from conversation
- **World State** — live item and agent positions, updated by the observer clicking steps

---

## System Requirements

- Python 3.9+
- macOS or Linux (Windows not tested)
- SSH access to Hello Robot Stretch RE1 (for real robot mode; not needed for `--dummy` mode)

---

## Installation

### 1. Clone with submodules

The project depends on the [Fast Downward](https://www.fast-downward.org/) PDDL planner as a git submodule.

```bash
git clone --recurse-submodules git@github.com:TimothyLien/llm-mediated-cooking.git
cd llm-mediated-cooking
```

If you already cloned without `--recurse-submodules`:

```bash
git submodule update --init --recursive
```

### 2. Create a Python environment

```bash
python3 -m venv env

# macOS / Linux
source env/bin/activate

# Windows (PowerShell)
env\Scripts\Activate.ps1
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure API key

Create a `.env` file in the project root:

```
GROQ_API_KEY=your_groq_api_key_here
```

Get a free API key at [console.groq.com](https://console.groq.com).

### 5. Build the Fast Downward planner

```bash
cd downward
./build.py
cd ..
```

To verify the build:

```bash
cd downward
./fast-downward.py ../pddl/domain_simple.pddl ../pddl/problem_simple.pddl --search "astar(lmcut())"
cd ..
```

### 6. (Real robot only) Configure passwordless SSH

```bash
ssh-keygen -t ed25519 -C "llm-cooking"   # skip if you already have a key
ssh-copy-id hello-robot@10.49.91.168      # enter password once
```

---

## Running the System

### Local simulation (no robot required)

Use `--dummy` mode to test the full three-phase flow locally. The robot's steps are simulated at 3 seconds each with keyboard-interrupt support.

```bash
python3 main.py --simple --dummy
```

Open **http://localhost:5050** in a browser to see the dashboard.

During dummy execution:
- Type `stop` + Enter to simulate a user-initiated stop → triggers REPLAN
- Type `fail` + Enter to simulate a hardware failure → triggers REPLAN

### Real robot mode

```bash
python3 main.py --simple
```

### Flags

| Flag | Description |
|---|---|
| `--simple` | Use `domain_simple.pddl` / `problem_simple.pddl` (required) |
| `--dummy` | Simulate robot execution locally — no SSH or robot needed |
| `--debug` | Verbose logging from LLM, PDDL solver, and planner |
| `--reset` | Wipe all persistent memory (KB, incident log, session notes) and exit |

---

## Typical Session Flow

```
$ python3 main.py --simple --dummy

  Dashboard → http://localhost:5050

  Stretch: Hi! I'm Stretch... [introduces task and initial plan]

You: I can't go to the pantry

  Stretch: Got it — I'll handle all the pantry trips...

  I understood the following:
    • [Your limitations] Human cannot enter the pantry.

  Update the plan with these changes? [y/n]: y
  Replanning... done.

You: /finish

  [Plan printed — confirm execution]
Begin simulated execution? [y/n]: y

  [Robot] Step 1/8:  Robot picks up the lettuce  -> done  (mark in dashboard)
  [Robot] Step 2/8:  Robot moves to the pantry   -> done  (mark in dashboard)
  ...

  [After task completes]

  Stretch: Great work — everything's swapped! How did the collaboration feel?

You: it went well

You: /finish

  Session saved. Goodbye!
```

---

## Persistent Memory

The system remembers preferences and incidents across sessions. Memory is stored in `memory/`:

| File | Contents |
|---|---|
| `kb_persistent.json` | Constraints and preferences extracted from conversation |
| `incident_log.json` | Record of every execution failure and how it was resolved |
| `session_notes.json` | Short notes distilled from post-task debriefs |

On next launch, the KB is applied automatically — the human does not need to restate previous constraints.

To start fresh:

```bash
python3 main.py --reset
```

---

## How Replanning Works

When execution stops (user stop or robot failure):

1. Terminal shows the stop reason and prompts the observer to update the dashboard
2. Observer marks any completed steps as done in the browser, then presses Enter
3. The system generates a new PDDL `:init` block from the current world state
4. Fast Downward solves for the remaining steps from the current physical position
5. The dashboard updates with the new plan
6. A conversation begins:
   - **User stop**: robot asks why the human stopped
   - **Robot failure**: robot explains what went wrong
   - Constraint changes during conversation trigger further replanning in real time
7. Human types `/continue` to resume execution with the updated plan

---

## Project Structure

```
llm-mediated-cooking/
├── main.py                    # Entry point — three-phase orchestration
├── conversation_engine.py     # Unified conversation loop (all phases)
├── execution_manager.py       # Execute → failure → REPLAN loop
├── buffer_window.py           # Pause point after stop, before replanning
│
├── phase_config.py            # PhaseConfig dataclass
├── phase_configs.py           # PRE_TASK, REPLAN, POST_TASK configurations
├── session_context.py         # Shared state across all phases
│
├── knowledge_base.py          # 4-category constraint store
├── world_state.py             # Live physical world model
├── memory_store.py            # JSON persistence (memory/ directory)
│
├── pddl_utils.py              # PDDL solver, modifier, world-state replanner
├── plan_utils.py              # Plan display and PDDL-to-English translation
├── plan_bridge.py             # PDDL → ROS2 JSON + SSH transport (real robot)
├── dummy_bridge.py            # Local plan simulation (--dummy mode)
├── dashboard.py               # Flask dashboard — step tracker, KB, world state
├── llm_engine_groq.py         # All LLM calls (Groq API)
│
├── pddl/
│   ├── domain_simple.pddl     # PDDL domain: move / take / drop
│   └── problem_simple.pddl    # PDDL problem: initial state + goal (master copy)
│
├── downward/                  # Fast Downward planner (git submodule)
├── memory/                    # Persistent memory (JSON files)
├── codebase-analysis-docs/
│   └── CODEBASE_KNOWLEDGE.md  # Full architecture and design reference
├── requirements.txt
└── .env                       # GROQ_API_KEY (not committed)
```

---

## Architecture Notes

- **LLM as constraint extractor, not planner.** The LLM extracts structured facts from natural language; the formal planner (Fast Downward A* + LM-Cut) handles all optimization.
- **Constraints = predicate removal.** The domain grants all permissions by default. A constraint removes the corresponding `can-enter` or `can-take` predicate from the PDDL `:init` block.
- **World state tracked manually.** The observer marks steps done in the dashboard — there is no automatic sensor-based tracking. This tolerates hardware uncertainty and network lag.
- **Single conversation engine.** All three phases share one `run_conversation_phase()` loop, parameterized by `PhaseConfig`.

For a full technical reference see [`codebase-analysis-docs/CODEBASE_KNOWLEDGE.md`](codebase-analysis-docs/CODEBASE_KNOWLEDGE.md).

---

## Two Repositories

| Repo | Host | Role |
|---|---|---|
| `llm-mediated-cooking` (this repo) | Laptop | Planning, conversation, dashboard, SSH bridge |
| `llm_cooking_ws` | Hello Robot (ROS2 Humble) | Action servers: ExecutePlan, Move, Grasp, Place |
