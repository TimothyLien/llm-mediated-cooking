# Codebase Knowledge: LLM-Mediated HRC Task Planner

> **Self-Contained Reference** — Full architecture, data flow, and step-by-step walkthrough of the complete three-phase human-robot collaboration interaction.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Repository Layout](#2-repository-layout)
3. [Three-Phase Interaction Model](#3-three-phase-interaction-model)
4. [Complete Data Flow: End to End](#4-complete-data-flow-end-to-end)
5. [File-by-File Reference](#5-file-by-file-reference)
6. [PDDL Domain & Problem Reference](#6-pddl-domain--problem-reference)
7. [LLM Engine Reference](#7-llm-engine-reference)
8. [Dashboard Reference](#8-dashboard-reference)
9. [Robot Execution & Dummy Mode](#9-robot-execution--dummy-mode)
10. [Persistent Memory System](#10-persistent-memory-system)
11. [Running the System: Step-by-Step](#11-running-the-system-step-by-step)
12. [Nuances, Gotchas & Design Decisions](#12-nuances-gotchas--design-decisions)
13. [Glossary & Quick Reference](#13-glossary--quick-reference)

---

## 1. Project Overview

### What This System Does

This is a **research prototype** for an **LLM-mediated human-robot collaboration (HRC) task planner**. A human and a Hello Robot Stretch RE1 collaborate on a physical ingredient-swap task. A large language model (via Groq) mediates the planning conversation: it translates natural language preferences and constraints into formal PDDL modifications, which the Fast Downward planner uses to generate an optimal task assignment.

The system runs on a **laptop** and sends plans to the **robot over SSH**. All conversation and planning happens on the laptop; the robot only executes the resulting action sequence. A **dummy mode** (`--dummy`) allows full end-to-end testing without a physical robot.

### The Task

**Ingredient swap** between two rooms (kitchen and pantry):
- **Start:** bread and ham in pantry, cheese and lettuce in kitchen
- **Goal:** bread and ham in kitchen, cheese and lettuce in pantry
- **Agents:** p1 (human), p2 (robot Stretch)
- **Actions:** move between rooms, take an item, drop an item

### Two Repositories

| Repo | Host | Role |
|---|---|---|
| `llm-mediated-cooking` | Laptop | Conversation, planning, SSH bridge, dashboard |
| `llm_cooking_ws` | Hello Robot (ROS2) | Action servers: ExecutePlan, Move, Grasp, Place |

This document covers `llm-mediated-cooking` exclusively.

### Core Design Principles

1. **LLM as natural language → formal logic bridge.** The LLM never writes code or plans — it extracts structured facts (KB updates) and the planner does the optimization.
2. **Constraints enforced by predicate removal.** The PDDL domain starts with all agents having all permissions; a constraint means removing the corresponding `can-enter` or `can-take` predicate from `:init`.
3. **Persistent memory across sessions.** KB, incidents, and session notes survive restarts via JSON files in `memory/`.
4. **Three conversation phases.** PRE_TASK (plan together), REPLAN (recover from failure), POST_TASK (debrief and learn).
5. **Single unified conversation loop.** All three phases share one engine (`conversation_engine.py`), parameterized by `PhaseConfig`.
6. **World state tracked manually via dashboard.** The observer marks steps as done in the browser interface; the system never auto-infers physical state.

---

## 2. Repository Layout

```
llm-mediated-cooking/
│
├── main.py                    # Entry point — orchestrates all three phases
├── conversation_engine.py     # Unified conversation loop (used by all phases)
├── execution_manager.py       # Execute plan → detect failure → REPLAN loop
├── buffer_window.py           # Pause point after stop — waits for Enter before replanning
│
├── phase_config.py            # PhaseConfig dataclass
├── phase_configs.py           # PRE_TASK_CONFIG, REPLAN_CONFIG, POST_TASK_CONFIG
├── session_context.py         # SessionContext dataclass (state across phases)
│
├── knowledge_base.py          # KnowledgeBase: 4-category constraint store
├── world_state.py             # WorldStateTracker: live physical world model
├── memory_store.py            # JSON read/write for memory/ directory
│
├── pddl_utils.py              # PDDL solver, KB modifier, world-state replanner
├── plan_utils.py              # Plan display and PDDL→English translation
├── plan_bridge.py             # PDDL→ROS2 JSON translation + SSH transport
├── dummy_bridge.py            # Local plan simulation (no SSH, no robot)
├── dashboard.py               # Flask dashboard (step tracker, KB, world state)
│
├── llm_engine_groq.py         # All LLM calls (Groq API — three models, one per task type)
│
├── pddl/
│   ├── domain_simple.pddl     # PDDL domain: move/take/drop only (active)
│   └── problem_simple.pddl    # PDDL problem: initial state + goal (master copy)
│
├── current_problem_simple.pddl  # Working copy (modified at runtime)
├── current_problem_replan.pddl  # Generated from world state for mid-task replanning
│
├── downward/                  # Fast Downward solver (git submodule)
│   └── sas_plan               # Solver output (overwritten each run)
│
├── memory/
│   ├── kb_persistent.json     # KB state from previous sessions
│   ├── incident_log.json      # Log of all execution failures + resolutions
│   └── session_notes.json     # Free-text notes extracted from post-task talks
│
├── codebase-analysis-docs/
│   └── CODEBASE_KNOWLEDGE.md  # This document
│
├── templates/                 # (Legacy web simulator — not used in robot mode)
├── app.py                     # (Legacy web simulator — not used in robot mode)
├── env/                       # Python virtualenv
├── requirements.txt
└── .env                       # GROQ_API_KEY (not committed)
```

**Startup commands:**

```bash
python3 main.py --simple           # real robot mode
python3 main.py --simple --dummy   # local simulation, no robot required
python3 main.py --simple --debug   # verbose logging
python3 main.py --reset            # wipe all persistent memory and exit
```

- `--simple`: use `domain_simple.pddl` / `problem_simple.pddl` (required; non-simple mode has no terminal execution path)
- `--dummy`: simulate robot steps locally at 3 s/step; type `stop` or `fail` during execution to test REPLAN flow
- `--debug`: verbose logging from LLM, PDDL, and planner
- `--reset`: wipe `memory/kb_persistent.json`, `incident_log.json`, and `session_notes.json` back to empty defaults, then exit immediately

---

## 3. Three-Phase Interaction Model

The system runs through exactly three phases in sequence. Each phase is a conversation with the robot (LLM). The same engine runs all three; only the configuration changes.

```
┌─────────────────────────────────────────────────────────────────────┐
│  PHASE 1: PRE_TASK                                                  │
│  Human and robot discuss the plan before anything happens.          │
│  - Robot introduces itself and the task                             │
│  - Human states preferences/limitations                             │
│  - LLM extracts KB updates → PDDL modified → plan regenerated      │
│  - Dashboard shows live plan and KB                                 │
│  - Ends when human types /finish                                    │
└─────────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│  PHASE 2: EXECUTION (execution_manager.py)                          │
│  Robot executes the plan (real SSH or dummy simulation).            │
│  - Human types "stop" to interrupt; robot failures auto-detected    │
│  - Dashboard shows interactive step tracker: observer marks each    │
│    completed step (human + robot) as done via "Mark Done" button    │
│  - On stop/failure:                                                 │
│      1. Buffer window — observer updates dashboard, presses Enter   │
│      2. Replan from dashboard world state (PDDL :init regenerated)  │
│      3. REPLAN conversation — robot asks why stopped / explains     │
│         failure; plan adjusts dynamically during conversation       │
│  - Loops until success or user aborts                               │
└─────────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│  PHASE 3: POST_TASK                                                 │
│  Human and robot debrief after the task.                            │
│  - Robot opens with what happened + invites reflection              │
│  - Robot proposes 1-2 concrete improvements for next time           │
│  - Late KB updates allowed (saved persistently)                     │
│  - LLM extracts one session note → saved to memory/                 │
│  - Ends when human types /finish                                    │
└─────────────────────────────────────────────────────────────────────┘
                            │
                            ▼
                   Session saved to memory/
```

### Phase Transitions

| From | To | Trigger |
|---|---|---|
| PRE_TASK | EXECUTION | Human types `/finish` |
| EXECUTION | REPLAN | Executor returns `success=False` |
| REPLAN | EXECUTION | Human types `/continue` or `/resume` |
| EXECUTION | POST_TASK | Executor returns `success=True`, or max replan attempts reached |
| POST_TASK | Exit | Human types `/finish` |

---

## 4. Complete Data Flow: End to End

### 4.1 Startup

```
main.py
  │
  ├─ [--reset check]             → wipe memory/ and exit (if --reset flag)
  ├─ dashboard.start()           → Flask on localhost:5050 (background thread)
  ├─ _build_context()            → load KB, incident_log, session_notes from memory/
  ├─ dashboard.set_world_state_tracker(ctx.world_state)  → share live tracker reference
  ├─ shutil.copy(problem → working) → reset working copy
  ├─ apply_kb_to_pddl()          → remove persisted KB constraints from working copy
  ├─ run_pddl_solver()           → Fast Downward generates initial plan
  ├─ dashboard.update_world_state()  → push initial world state FIRST (bread/ham in pantry)
  ├─ dashboard.update(..., plan_actions=...) → snapshot base world state, push plan + KB
  ├─ dashboard.set_phase("PRE_TASK")
  └─ get_introduction()          → LLM speaks first (warm intro)
```

**Critical ordering:** `update_world_state()` must be called before `update(..., plan_actions=...)`. The `update()` call snapshots the current world state as the undo base; if called before world state is populated, items will disappear on the first "Mark Done" click.

### 4.2 PRE_TASK Conversation Turn

```
Human types a constraint (e.g. "I can't go to the pantry")
  │
  ├─ get_conversation_reply()    → LLM responds naturally (no plan change)
  │    • system_prompt_override = None (uses default)
  │    • returns {"reply": "...", "is_clarifying_question": false}
  │
  ├─ extract_kb_updates()        → LLM extracts structured delta
  │    • returns {"kb_update": [{"type": "human_limitations",
  │                              "fact": "Human cannot enter the pantry.",
  │                              "pddl_removal": "(can-enter p1 pantry)"}]}
  │
  ├─ Human confirms: "Update the plan with these changes? [y/n]"
  │
  ├─ kb.update_kb_from_llm()     → appends to in-memory KB
  │
  ├─ apply_kb_to_pddl()          → removes "(can-enter p1 pantry)" from :init
  │    source: problem_simple.pddl  (master, always rebuilt from scratch)
  │    target: current_problem_simple.pddl
  │
  ├─ run_pddl_solver()           → Fast Downward re-solves
  │    • plan now assigns all pantry trips to robot
  │
  ├─ diff_plans()                → prints what changed
  └─ dashboard.update(..., plan_actions=...)  → browser reflects new plan + KB
                                               snapshots new base world state for undo
```

### 4.3 Execution Flow

```
execute_with_replanning(ctx, REPLAN_CONFIG, executor)
  │
  │  executor = dummy_execute_plan  (--dummy mode)
  │           = execute_plan_on_robot  (real mode)
  │
  ├─ executor(ctx.current_plan)
  │    │
  │    │  [DUMMY MODE]
  │    ├─ Steps through robot-only (p2) actions at 3s/step
  │    │  Terminal: "Step 2/8: Robot moves to the pantry  → done  (mark in dashboard ↗)"
  │    │  Observer clicks "Mark Done" in dashboard for each completed step
  │    │  Type "stop" → user-initiated stop; "fail" → simulated hardware failure
  │    │
  │    │  [REAL MODE]
  │    ├─ Translates PDDL to ROS2 JSON, sends over SSH
  │    │  SSH output streams to terminal
  │    │  Type "stop" → SIGINT sent to robot; type "fail" → not available
  │    │
  │    └─ Returns {"success": bool, "steps_completed": int,
  │                "failure_reason": str|None, "stopped_by": "user"|"error"|None}
  │
  ├─ [IF success] → return True → proceed to POST_TASK
  │
  └─ [IF failure]
       │
       ├─ ctx.start_incident(trigger)     → open incident record with stopped_by info
       │
       ├─ run_buffer_window(reason)  → blocking input() in terminal
       │    Terminal: "Update completed steps in the dashboard (http://localhost:5050)."
       │    "Press Enter when done (or type a note to help with replanning):"
       │    Observer updates dashboard at their own pace, presses Enter to continue
       │    (No countdown — waits indefinitely for Enter)
       │
       ├─ run_pddl_solver_from_world_state(ctx.world_state, ctx.kb, ctx.domain_file)
       │    → generates fresh PDDL :init from dashboard's current world state
       │    → applies KB constraints on top
       │    → runs Fast Downward from current physical position
       │    → ctx.current_plan updated with new plan
       │    → dashboard.update(..., plan_actions=...) → step tracker resets to new plan
       │
       ├─ run_conversation_phase(ctx, REPLAN_CONFIG)
       │    • Opening: context-aware LLM message
       │      [user stop]    "I noticed you stopped — I've updated the plan from where
       │                      we left off. What made you want to stop?"
       │      [robot failure] "I had to stop — [reason]. I've updated the plan from our
       │                       current position. Let's figure out how to continue."
       │    • System prompt: REPLAN-specific (focused on recovery dialogue)
       │    • Human adjusts constraints mid-conversation
       │    • Each KB update triggers run_pddl_solver_from_world_state()
       │    • Dashboard reflects every plan change in real time
       │    • Human types /continue when ready
       │
       ├─ ctx.close_incident("replanned")
       └─ loop back to executor(ctx.current_plan)
```

### 4.4 POST_TASK Conversation Turn

```
run_conversation_phase(ctx, POST_TASK_CONFIG)
  │
  ├─ get_posttask_opening()      → LLM opens: congrats + incident summary
  │
  ├─ [Each human turn]
  │    ├─ get_conversation_reply()   → normal conversation
  │    ├─ get_proactive_proposals()  → robot suggests 1-2 improvements
  │    └─ extract_kb_updates()       → late preferences still captured + saved
  │
  └─ [On /finish] POST_TASK on_phase_end callback:
       ├─ extract_session_note()     → LLM distils one note from debrief
       ├─ session_notes.append(note)
       └─ memory_store.save_session_notes()
```

### 4.5 Session Teardown

```
main.py: _save_memory(ctx)
  ├─ ctx.kb.save_to_file(memory/kb_persistent.json)
  ├─ memory_store.save_incident_log(ctx.incident_log)
  └─ memory_store.save_session_notes(ctx.session_notes)
```

---

## 5. File-by-File Reference

### `main.py` — Entry Point

Three-act structure. No business logic; only orchestration.

**CLI flags:**
- `--simple` — use `domain_simple.pddl` / `problem_simple.pddl`
- `--debug` — verbose logging
- `--dummy` — use `dummy_bridge.dummy_execute_plan` instead of `plan_bridge.execute_plan_on_robot`
- `--reset` — call `memory_store.clear_all()` and `sys.exit(0)` before doing anything else

**Key functions:**

```python
_build_context() → SessionContext
```
Loads persistent memory (KB, incident log, session notes) and constructs `SessionContext`.

```python
_save_memory(ctx: SessionContext) → None
```
Writes KB, incident log, and session notes back to `memory/` after the session ends.

```python
main() → None
```
Full sequence:
1. Handle `--reset` (wipe memory and exit if present)
2. Start dashboard
3. Validate file paths
4. `_build_context()`
5. `dashboard.set_world_state_tracker(ctx.world_state)` — share tracker reference
6. Copy problem file, apply KB constraints, run solver → `ctx.current_plan`
7. `dashboard.update_world_state(ctx.world_state.to_dict())` — populate world state **first**
8. `dashboard.update(..., plan_actions=...)` — snapshot base world state, push plan + KB
9. Get robot introduction from LLM
10. `run_conversation_phase(ctx, PRE_TASK_CONFIG)` → Phase 1
11. Print final plan, confirm execution
12. Build executor (dummy or real), call `execute_with_replanning()` → Phase 2
13. `run_conversation_phase(ctx, POST_TASK_CONFIG)` → Phase 3
14. `_save_memory(ctx)`

---

### `session_context.py` — Shared State

```python
@dataclass
class SessionContext:
    kb: KnowledgeBase              # 4-category constraint store
    world_state: WorldStateTracker # live physical model
    current_plan: str | None       # raw sas_plan text
    incident_log: list[dict]       # all past incidents
    session_notes: list[str]       # free-text memory from post-task talks
    current_incident: dict | None  # open incident (during execution)
    session_id: str                # timestamp-based identifier
    run_number: int                # increments each session
    original_problem_file: Path
    current_problem_file: Path
    domain_file: Path
```

**Methods:**
- `start_incident(trigger)` — opens a new incident dict
- `close_incident(resolution)` — finalises and appends to `incident_log`

The incident dict schema:
```python
{
    "session_id": "20260508_012345",
    "run_number": 3,
    "trigger": "Stopped by user",
    "timestamp": "2026-05-08T01:23:45.000000",
    "steps_completed": 4,
    "failure_reason": "Stopped by user",
    "stopped_by": "user",         # "user" | "error" | None
    "resolution": "replanned (attempt 1)",
    "replan_count": 1
}
```

---

### `conversation_engine.py` — Unified Loop

```python
def run_conversation_phase(ctx: SessionContext, phase_config: PhaseConfig) -> None
```

One function handles all three phases. Logic:

1. Call `phase_config.opening_message_fn(ctx)` if set → print robot's opening message
2. `while True:` read user input
3. Call `get_conversation_reply()` with optional system prompt override
4. If `phase_config.enable_proposals`: call `get_proactive_proposals()` after each human turn
5. If `is_clarifying_question`: store `pending_user_input`, skip KB extraction this turn
6. Call `extract_kb_updates()` → show extracted facts → ask confirmation
7. On confirm: `kb.update_kb_from_llm()` → replan
8. On `/finish` or any `phase_config.finish_commands`: call `phase_config.on_phase_end(ctx)` → break

**REPLAN replanning (phase_name == "REPLAN"):**
```python
run_pddl_solver_from_world_state(ctx.world_state, ctx.kb, ctx.domain_file)
# Generates :init from WorldStateTracker (current physical state)
# Goal is always fixed (bread+ham in kitchen, cheese+lettuce in pantry)
```

**PRE_TASK/POST_TASK replanning:**
```python
apply_kb_to_pddl(ctx.kb, ctx.original_problem_file, ctx.current_problem_file)
run_pddl_solver(ctx.domain_file, ctx.current_problem_file)
# Rebuilds from master problem file — no stale state accumulation
```

After any successful replan, `dashboard.update(..., plan_actions=_compute_plan_actions(new_plan))` is called so the step tracker resets and the base world state is re-snapshotted.

---

### `phase_config.py` — Phase Parameterization

```python
@dataclass
class PhaseConfig:
    phase_name: str                     # "PRE_TASK" | "REPLAN" | "POST_TASK"
    system_prompt_override: str | None  # None = use default LLM prompt
    finish_commands: tuple[str, ...]    # commands that end the phase
    enable_kb_updates: bool             # whether to extract/apply KB changes
    offer_execution: bool               # (informational flag)
    input_prompt: str                   # terminal prompt shown to user
    show_plan_diff: bool                # print plan diff after replan
    enable_proposals: bool              # robot suggests improvements (POST_TASK)
    opening_message_fn: Callable | None # (ctx) → str
    on_phase_end: Callable | None       # (ctx) → None
```

---

### `phase_configs.py` — Config Instances

**PRE_TASK_CONFIG:**
- `system_prompt_override = None` (uses default `get_conversation_reply` prompt)
- `finish_commands = ("/finish", "/done")`
- `input_prompt = "You (or '/finish' to start execution): "`
- `enable_kb_updates = True`
- `opening_message_fn = None` (intro handled directly in `main.py`)

**REPLAN_CONFIG:**
- `system_prompt_override = _REPLAN_SYSTEM_PROMPT` — focused on recovery dialogue:
  - Robot is told the plan has already been updated from world state
  - Instructs robot to ask why stopped (if user) or explain failure (if error)
  - Tells robot plan updates automatically when constraints change
  - Prohibits describing the plan directly
- `finish_commands = ("/continue", "/resume", "/finish")`
- `input_prompt = "You (or '/continue' to resume execution): "`
- `opening_message_fn = _replan_opening`:
  - Reads `incident.get("stopped_by")` to determine whether stop was user or error
  - Calls `get_replanning_opening(..., stopped_by_user=True/False)`
  - Opening already reflects the pre-updated plan (replanning happened before conversation)

**POST_TASK_CONFIG:**
- `finish_commands = ("/finish", "/done", "/exit")`
- `enable_proposals = True` (robot suggests improvements each turn)
- `opening_message_fn = _posttask_opening` → calls `get_posttask_opening()`
- `on_phase_end = _posttask_end` → calls `extract_session_note()` + saves

---

### `execution_manager.py` — Execution Loop

```python
def execute_with_replanning(ctx: SessionContext, replan_config: PhaseConfig,
                             executor: Callable | None = None) -> bool
```

`executor` defaults to `plan_bridge.execute_plan_on_robot`. Pass `dummy_bridge.dummy_execute_plan` for local testing.

Loop (up to `MAX_REPLAN_ATTEMPTS = 5`):
1. Call `executor(ctx.current_plan)` — returns result dict
2. On success: return `True`
3. On failure:
   a. `ctx.start_incident(trigger)` — stores `failure_reason`, `steps_completed`, `stopped_by`
   b. `run_buffer_window(reason)` — blocks until Enter; observer updates dashboard first
   c. `run_pddl_solver_from_world_state(...)` — regenerate plan from dashboard world state
   d. `dashboard.update(..., plan_actions=...)` — step tracker resets to new plan
   e. `run_conversation_phase(ctx, replan_config)` — REPLAN conversation
   f. `ctx.close_incident("replanned (attempt N)")`
   g. Loop back with updated `ctx.current_plan`

There is **no** "Continue with a revised plan? [y/n]" prompt for user stops. The REPLAN conversation's opening message handles the dialogue about why the human stopped.

---

### `buffer_window.py` — Observer Pause Point

```python
def run_buffer_window(reason: str, **_kwargs) -> str | None
```

Displays the stop reason and blocks on a simple `input()` until the observer presses Enter. Terminal output:
```
  Execution stopped: Action server timeout: grasp did not complete within 30s
  Update completed steps in the dashboard (http://localhost:5050).
  Press Enter when done (or type a note to help with replanning):
```

There is no countdown or timeout — the observer takes as long as they need to update the dashboard before pressing Enter. Any text typed before Enter is returned as an optional note (currently not used to update world state directly, but available for future LLM context). The `**_kwargs` absorbs any legacy `seconds=` parameter at call sites.

---

### `dummy_bridge.py` — Local Plan Simulation

```python
def dummy_execute_plan(plan_text: str, world_state=None) -> dict
```

Simulates robot execution locally without SSH. Filters to robot-only (`p2`) actions and steps through them at `_STEP_DELAY = 3.0` seconds each.

Terminal output per step:
```
  [Robot] Step 2/8:  Robot moves to the pantry  → done  (mark in dashboard ↗)
```

The observer is expected to click "Mark Done" in the dashboard for completed steps (both human and robot). Dummy mode does **not** automatically update world state — this matches real mode behavior.

**Interruption commands** (type then press Enter during a step's 3-second delay):
- `stop` — simulates user-initiated stop (`stopped_by: "user"`)
- `fail` — simulates hardware failure; picks a random message from `_FAILURE_REASONS` (`stopped_by: "error"`)

**Differences from real robot mode:**

| | Dummy | Real robot |
|---|---|---|
| Transport | Local Python loop | SSH to `10.49.91.168` |
| Step timing | Fixed 3s delay | Actual robot hardware time |
| Failure injection | Type `fail` | Real hardware/ROS2 errors |
| Step counting | Exact (loop iteration) | Approximate (SSH output heuristic) |
| Stop mechanism | Loop break | `pkill -SIGINT` over second SSH connection |
| Human (p1) steps | Filtered out — observer marks in dashboard | Same |

---

### `world_state.py` — Live Physical Model

```python
class WorldStateTracker:
    item_room: dict[str, str | None]      # item → room (None if held)
    agent_holding: dict[str, str | None]  # agent → item they hold (None if empty)
    agent_room: dict[str, str]            # agent → current room
```

**Initial state (mirrors `problem_simple.pddl`):**
```
bread → pantry, ham → pantry, cheese → kitchen, lettuce → kitchen
p1 → kitchen (empty hand), p2 → kitchen (empty hand)
```

**Mutation methods:**
- `agent_moved(agent, room)` — updates `agent_room`
- `agent_took(agent, item)` — clears `item_room[item]`, sets `agent_holding[agent]`
- `agent_dropped(agent, item, room)` — sets `item_room[item] = room`, clears `agent_holding`
- `reset()` — restores initial state
- `from_dict(data)` — restores from a `to_dict()` snapshot (used by dashboard undo)

**Serialization:**
- `to_pddl_init() → str` — emits the `:init` block body for replanning (includes agents, items, connectivity, all permissions)
- `to_dict() → dict` — snapshot for dashboard (`{"item_room": {...}, "agent_holding": {...}, "agent_room": {...}}`)
- `summary() → str` — one-sentence human-readable state for LLM prompts

**PDDL init emitted example (mid-task state):**
```pddl
    (at p1 kitchen)
    (at p2 pantry)
    (empty-hand p1)
    (holding p2 cheese)
    (in-room bread pantry)
    (in-room ham pantry)
    (in-room lettuce kitchen)
    (connected kitchen pantry)
    (connected pantry kitchen)
    (can-enter p1 kitchen)
    (can-enter p1 pantry)
    ... (all permissions — KB removals are applied on top by apply_kb_to_pddl)
```

---

### `knowledge_base.py` — Constraint Store

```python
class KnowledgeBase:
    state: dict = {
        "human_preference":    [],
        "human_limitations":   [],
        "robot_limitations":   [],
        "environmental_factors": []
    }
```

Each entry in a list is a dict:
```python
{"fact": "Human cannot enter the pantry.", "pddl_removal": "(can-enter p1 pantry)"}
```
The `pddl_removal` key is optional (environmental facts often lack it).

**Key methods:**

| Method | Behavior |
|---|---|
| `update_kb_from_llm(updates_data)` | **APPENDS** new facts (delta, not reset). Accepts list or dict format. |
| `get_pddl_removals() → list[str]` | Returns deduplicated list of predicates to remove from `:init` |
| `get_state_as_string() → str` | Returns JSON of `{"category": ["fact text", ...]}` (hides pddl_removal) |
| `load_from_file(path)` | Replaces state from JSON file (no-op if file missing) |
| `save_to_file(path)` | Writes state to JSON file, creating parent dirs |

**Critical:** `update_kb_from_llm` is ADDITIVE. The LLM is prompted to return only NEW facts per message. Accumulation across turns is handled by this function, not the LLM.

---

### `memory_store.py` — Persistence Layer

Simple read/write wrappers. No business logic.

```python
# File paths
KB_PATH       = Path("memory/kb_persistent.json")
INCIDENT_PATH = Path("memory/incident_log.json")
NOTES_PATH    = Path("memory/session_notes.json")

# Functions
load_kb() → dict
save_kb(state: dict) → None
load_incident_log() → list[dict]
save_incident_log(log: list[dict]) → None
load_session_notes() → list[str]
save_session_notes(notes: list[str]) → None
append_incident(incident: dict) → None   # load → append → save
append_session_note(note: str) → None
clear_all() → None                       # reset all three files to empty defaults
```

All load functions return empty defaults on missing/corrupt file. `clear_all()` is called by `main.py --reset`.

---

### `pddl_utils.py` — PDDL Operations

```python
def validate_pddl(content: str) -> tuple[bool, str | None]
```
Checks `(define` prefix and balanced parentheses.

```python
def run_pddl_solver(domain_file: Path, problem_path: Path) -> str | None
```
Runs Fast Downward from `cwd=downward/` with `astar(lmcut())`. Returns content of `downward/sas_plan` or None.

```python
def apply_kb_to_pddl(kb, source_path: Path, target_path: Path) -> bool
```
Reads `source_path`, removes each string in `kb.get_pddl_removals()` using regex, validates, writes to `target_path`. Always rebuilds from source (never accumulates edits). When `source_path == target_path` and there are no removals, the copy is skipped (avoids `SameFileError`).

```python
def run_pddl_solver_from_world_state(world_state, kb, domain_file: Path) -> str | None
```
Generates a complete PDDL problem from `world_state.to_pddl_init()`, writes to `current_problem_replan.pddl`, applies KB removals on top (in-place), runs the solver. Goal is always fixed:
```pddl
(:goal (and
  (in-room bread kitchen) (in-room ham kitchen)
  (in-room cheese pantry) (in-room lettuce pantry)
))
```
This is the key enabler for mid-task replanning: the planner finds the shortest path from the *current* physical state to the fixed goal.

---

### `plan_utils.py` — Display Utilities

```python
AGENT_LABELS = {"p1": "You", "p2": "Robot"}
ROOM_LABELS  = {"kitchen": "the kitchen", "pantry": "the pantry"}
ITEM_LABELS  = {"bread": "the bread", ...}
KB_CATEGORY_LABELS = {"human_preference": "Your preferences", ...}

def _humanize_action(tokens) → str
# Uses bare infinitive for p1 ("You"), third-person singular for p2 ("Robot")
# ['move', 'p2', 'kitchen', 'pantry'] → "Robot moves to the pantry"
# ['move', 'p1', 'kitchen', 'pantry'] → "You move to the pantry"
# ['take', 'p1', 'bread', 'pantry']   → "You pick up the bread"
# ['drop', 'p1', 'bread', 'kitchen']  → "You put down the bread in the kitchen"

def _compute_plan_rows(raw_plan) → list[dict]
# [{"actor": "Robot", "description": "Robot moves to the pantry"}]

def _compute_plan_actions(raw_plan) → list[list[str]]
# [['move', 'p2', 'kitchen', 'pantry'], ['take', 'p2', 'cheese', 'kitchen'], ...]
# Token lists for every action in the plan (all actors, not just robot)
# Used by dashboard to update WorldStateTracker when steps are marked done

def print_plan(raw_plan) → list[dict]   # prints formatted table to terminal
def diff_plans(old, new) → None         # prints ✕ removed / ✓ added steps
```

`_compute_plan_actions` is the bridge between the plan text and the dashboard's world state tracking. The dashboard stores these token lists and calls `_apply_tokens_to_tracker()` when steps are marked done.

---

### `plan_bridge.py` — Robot Communication

**Translation table:**

| PDDL action | Tokens | ROS2 JSON |
|---|---|---|
| `(move p2 kitchen pantry)` | `['move','p2','kitchen','pantry']` | `{"type": "move", "arg": "pantry"}` |
| `(take p2 cheese kitchen)` | `['take','p2','cheese','kitchen']` | `{"type": "grasp", "arg": "cheese"}` |
| `(drop p2 cheese pantry)` | `['drop','p2','cheese','pantry']` | `{"type": "place", "arg": "cheese"}` |

For `grasp` and `place`, the arg is the **ingredient name** (not the room), so the robot's ArUco-tag-based grasp server can locate the specific item.

```python
def execute_plan_on_robot(plan_text: str) -> dict
```
Returns:
```python
{
    "success": bool,
    "steps_completed": int,         # count of "executing step" lines in SSH output (heuristic)
    "failure_reason": str | None,
    "stopped_by": "user" | "error" | None
}
```

Stop mechanism: polls stdin every 200ms with `select()`. If "stop" is typed, `_cancel_remote()` sends SIGINT to the remote `ros2 action send_goal` process via a second SSH connection, then terminates the local SSH process.

---

## 6. PDDL Domain & Problem Reference

### Domain: `pddl/domain_simple.pddl`

**Domain name:** `fetch-domain`  **Requirements:** `:strips :typing`

**Types:** `agent`, `room`, `item`

**Predicates:**

| Predicate | Meaning |
|---|---|
| `(at ?a ?r)` | Agent is in room |
| `(connected ?r1 ?r2)` | Rooms are adjacent |
| `(in-room ?i ?r)` | Item is in room (not held) |
| `(holding ?a ?i)` | Agent holds this item |
| `(empty-hand ?a)` | Agent's hand is free |
| `(can-enter ?a ?r)` | **HRC constraint:** agent may enter room |
| `(can-take ?a ?i)` | **HRC constraint:** agent may pick up item |

**Actions:**

```
move(?a, ?from, ?to):
  pre: at(?a, ?from), connected(?from, ?to), can-enter(?a, ?to)
  eff: ¬at(?a, ?from), at(?a, ?to)

take(?a, ?i, ?r):
  pre: at(?a, ?r), in-room(?i, ?r), empty-hand(?a), can-take(?a, ?i)
  eff: ¬in-room(?i, ?r), ¬empty-hand(?a), holding(?a, ?i)

drop(?a, ?i, ?r):
  pre: at(?a, ?r), holding(?a, ?i)
  eff: ¬holding(?a, ?i), in-room(?i, ?r), empty-hand(?a)
```

### Problem: `pddl/problem_simple.pddl`

**Objects:** `p1 p2 - agent`, `kitchen pantry - room`, `bread ham cheese lettuce - item`

**Initial state:**
```pddl
(at p1 kitchen)    (at p2 kitchen)
(empty-hand p1)    (empty-hand p2)
(connected kitchen pantry)  (connected pantry kitchen)
(in-room cheese kitchen)    (in-room lettuce kitchen)
(in-room bread pantry)      (in-room ham pantry)
(can-enter p1 kitchen)  (can-enter p1 pantry)
(can-enter p2 kitchen)  (can-enter p2 pantry)
(can-take p1 bread)  (can-take p1 ham)  (can-take p1 cheese)  (can-take p1 lettuce)
(can-take p2 bread)  (can-take p2 ham)  (can-take p2 cheese)  (can-take p2 lettuce)
```

**Goal:**
```pddl
(and (in-room bread kitchen) (in-room ham kitchen)
     (in-room cheese pantry) (in-room lettuce pantry))
```

**Default unconstrained plan** (12 steps):
```
1. Robot picks up the lettuce
2. Robot moves to the pantry
3. You picks up the cheese
4. Robot puts down the lettuce in the pantry
5. You moves to the pantry
6. Robot picks up the bread
7. Robot moves to the kitchen
8. Robot puts down the bread in the kitchen
9. You puts down the cheese in the pantry
10. You picks up the ham
11. You moves to the kitchen
12. You puts down the ham in the kitchen
```

---

## 7. LLM Engine Reference

### Models

Three models are used via the Groq API, each chosen for its task:

| Constant | Model | Task | Rationale |
|---|---|---|---|
| `MODEL_CHAT` | `openai/gpt-oss-120b` | Conversation, openings, proposals | Strongest available — best natural language quality |
| `MODEL_EXTRACT` | `qwen/qwen3-32b` | KB extraction, session notes | Excellent structured JSON output and instruction following |
| `MODEL_SUMMARIZE` | `llama-3.1-8b-instant` | History compression | Fastest model; summarization is a simple task |

| Purpose | Temp | Format |
|---|---|---|
| Conversation (chat) | 0.7–0.8 | JSON object |
| Extraction (structured) | 0.01 | JSON object |
| Summarization | 0.3 | Plain text |

All extraction calls use `response_format={"type": "json_object"}`, which suppresses any model-specific "thinking" tokens and enforces clean JSON output.

### Functions

| Function | Called By | Purpose |
|---|---|---|
| `get_introduction(plan_steps)` | `main.py` | Robot's warm intro at startup |
| `get_conversation_reply(history, kb, plan, override)` | `conversation_engine.py` | Natural language reply per turn |
| `extract_kb_updates(message)` | `conversation_engine.py` | Structured fact extraction (delta only) |
| `summarize_chat_history(history)` | `conversation_engine.py` | Rolling window compression |
| `get_replanning_opening(world_state, failure, steps, plan, stopped_by_user)` | `phase_configs.py` | REPLAN phase opening |
| `get_posttask_opening(world_state, incidents, notes)` | `phase_configs.py` | POST_TASK phase opening |
| `get_proactive_proposals(history, kb, incidents)` | `conversation_engine.py` | Robot's improvement suggestions |
| `extract_session_note(history, kb)` | `phase_configs.py` | Post-task memory distillation |

**`get_replanning_opening` — context-aware opening:**
- `stopped_by_user=True`: asks warmly why the human stopped, confirms plan has been updated
- `stopped_by_user=False`: explains the failure in plain language, confirms plan has been updated

**`extract_kb_updates` output format:**
```json
{"kb_update": [
    {"type": "human_limitations", "fact": "Human cannot enter the pantry.", "pddl_removal": "(can-enter p1 pantry)"},
    {"type": "robot_limitations", "fact": "Robot cannot take bread.", "pddl_removal": "(can-take p2 bread)"}
]}
```

**Constraint predicate guide** (injected into KB extraction prompt):
```
(can-enter p1 kitchen)  (can-enter p1 pantry)
(can-enter p2 kitchen)  (can-enter p2 pantry)
(can-take p1 bread)  (can-take p1 ham)  (can-take p1 cheese)  (can-take p1 lettuce)
(can-take p2 bread)  (can-take p2 ham)  (can-take p2 cheese)  (can-take p2 lettuce)
```

### KB Extraction — What Gets Extracted

| Human says | KB category | pddl_removal |
|---|---|---|
| "I can't go to the pantry" | `human_limitations` | `(can-enter p1 pantry)` |
| "I want to carry the bread myself" | `human_preference` | `(can-take p2 bread)` |
| "Robot's arm is broken" | `robot_limitations` | depends on context |
| "There's a spill by the sink" | `environmental_factors` | none |
| "Sounds good" | (empty list) | — |

### Clarifying Questions

When `get_conversation_reply()` returns `"is_clarifying_question": true`, the engine:
1. Stores `user_input` as `pending_user_input`
2. Does NOT call `extract_kb_updates` this turn
3. On the NEXT human message, passes both messages to `extract_kb_updates` as context

### Rolling History Compression

When `chat_history` exceeds 18 messages:
- Oldest messages (beyond last 10) are passed to `summarize_chat_history()`
- Summary replaces them as a single `[CONVERSATION SUMMARY]: ...` entry
- Last 10 messages remain verbatim

---

## 8. Dashboard Reference

**URL:** http://localhost:5050 (auto-started in background thread)

### Panels

1. **Step Tracker** — interactive plan table; each step has actor badge (blue=You, green=Robot) and Mark Done / Undo buttons. A progress bar shows overall task completion.
2. **Constraints & Preferences** — four KB categories, each as a bullet list
3. **World State** — live item locations and agent positions (two columns: items / agents)

### Header Elements

- Live green pulsing dot
- Phase badge: blue=PRE_TASK, orange=REPLAN, green=POST_TASK
- Status pill: short status text

### Step Tracker Behavior

Each step row shows:
- ✓ / ○ status icon, step number, actor badge, action description
- **Mark Done** button (green) — marks step as done, recomputes world state
- **Undo** button (gray) — only active on the **last done step**; earlier done steps show a disabled, grayed-out Undo button with the tooltip "Undo later steps first"

**Logical undo ordering:** Only the most recently completed step (highest index among all "done" steps) may be undone. This prevents leaving the world in a logically inconsistent state — e.g. you cannot undo picking up lettuce while the drop-lettuce step is still marked done. The `/undo-step` endpoint enforces this server-side; the frontend also disables earlier Undo buttons visually.

When any step is marked done or undone, `_recompute_world_state_inplace()` runs:
1. Restore `WorldStateTracker` from a **deep copy** of `base_world_state` (snapshot taken when plan was last set)
2. Replay all "done" steps in order using `_apply_tokens_to_tracker()`

The deep copy is critical: without it, replaying done steps would mutate the snapshot's nested dicts, corrupting the base and making undo ineffective. Both the snapshot creation (`copy.deepcopy`) and the restore (`copy.deepcopy(base)` passed to `from_dict`) use deep copies.

Both human (p1) and robot (p2) steps are tracked this way. Observers manually click steps as they observe them completed.

### World State Update Architecture

```
Observer clicks "Mark Done" on Step N
  └─ POST /complete-step {index: N}
       └─ _state["step_statuses"][N] = "done"
       └─ _recompute_world_state_inplace()
            ├─ _world_state_tracker.from_dict(base_world_state)   # reset to base
            └─ for each "done" step:                               # replay in order
                 _apply_tokens_to_tracker(tokens, _world_state_tracker)
            └─ _state["world_state"] = _world_state_tracker.to_dict()
```

`_world_state_tracker` is the same Python object as `ctx.world_state` in the main thread (shared reference set via `set_world_state_tracker()`). So after any Mark Done click, `ctx.world_state` is already updated — `run_pddl_solver_from_world_state(ctx.world_state, ...)` reads the correct state without any extra synchronization.

### `_apply_tokens_to_tracker` — Token → World State

```python
action, agent = tokens[0], tokens[1]
# move p2 kitchen pantry  → tracker.agent_moved(agent, tokens[3])
# take p2 cheese kitchen  → tracker.agent_took(agent, tokens[2])
# drop p2 cheese pantry   → tracker.agent_dropped(agent, tokens[2], tokens[3])
```

### Flask Routes

| Route | Method | Purpose |
|---|---|---|
| `/` | GET | Serve dashboard HTML |
| `/state` | GET | Return full JSON state snapshot |
| `/complete-step` | POST | Mark step done, recompute world state |
| `/undo-step` | POST | Revert step to pending, recompute world state |
| `/stop` | POST | Set stop flag (available but not yet polled during execution) |

### Public Python API

```python
dashboard.start(port=5050) → int
dashboard.set_world_state_tracker(tracker)        # share live WorldStateTracker reference
dashboard.update(plan_rows, kb, status, plan_actions=None)
    # plan_actions: if provided, resets step_statuses and snapshots base_world_state
    # must call update_world_state() BEFORE update() so snapshot has correct item positions
dashboard.update_world_state(world_dict)          # push world state dict to _state
dashboard.set_status(text)
dashboard.set_phase("PRE_TASK" | "REPLAN" | "POST_TASK")
dashboard.consume_stop_request() → bool           # clears flag after reading
```

**Auto-poll:** Browser polls `/state` every 1.5 seconds. Re-renders panels only when a hash of their content changes.

---

## 9. Robot Execution & Dummy Mode

### Prerequisites for Real Robot (one-time setup)

```bash
ssh-keygen -t ed25519 -C "llm-cooking"  # skip if key exists
ssh-copy-id hello-robot@10.49.91.168    # enter password once
```

### Connection Parameters

```python
ROBOT_USER    = "hello-robot"
ROBOT_HOST    = "10.49.91.168"
ROS_DISTRO    = "humble"
WORKSPACE     = "~/llm_cooking_ws"
ACTION_SERVER = "/execute_plan"
ACTION_TYPE   = "servers_interfaces/action/ExecutePlan"
```

### Plan Format Sent to Robot

```json
[
  {"type": "move",  "arg": "pantry"},
  {"type": "grasp", "arg": "lettuce"},
  {"type": "move",  "arg": "kitchen"},
  {"type": "place", "arg": "lettuce"}
]
```

### ROS2 Action Servers (on robot)

| Action | Arg | Behavior |
|---|---|---|
| `move` | destination room name | Navigate to room |
| `grasp` | ingredient name | Locate ArUco tag for that ingredient and grasp |
| `place` | ingredient name | Place at the ingredient's designated tagged spot |

### Dummy Mode

Run with `--dummy` to test locally without SSH or robot hardware. The dummy executor:
- Steps through only `p2` actions (same filter as real mode)
- Prints each step with a 3-second delay
- Tells the observer to mark steps in the dashboard
- Responds to `stop` / `fail` typed in the terminal

Everything downstream (buffer window, world-state replan, REPLAN conversation, POST_TASK) behaves identically in dummy and real modes.

---

## 10. Persistent Memory System

### What Persists

| File | Contents | Loaded at | Saved at |
|---|---|---|---|
| `memory/kb_persistent.json` | KB state (all 4 categories) | Startup | End of session |
| `memory/incident_log.json` | List of all incident records | Startup | End of session |
| `memory/session_notes.json` | List of free-text notes | Startup | POST_TASK end + end of session |

### Resetting Memory

```bash
python3 main.py --reset
```

Calls `memory_store.clear_all()`: writes empty defaults to all three files and exits. Safe to run at any time; the files are recreated if missing on the next launch.

### KB Persistence Behavior

The KB loaded at startup is applied **before** the initial plan is generated. Preferences from a previous session immediately restrict the plan without needing to be re-stated.

### Incident Log

Each incident captures what triggered the stop, how many steps completed, the failure reason, the resolution (aborted vs. replanned), and how many replan attempts were needed. Shown to the LLM during POST_TASK to generate informed suggestions.

### Session Notes

After each POST_TASK conversation, `extract_session_note()` distils one short sentence from the debrief. These notes are passed to `get_posttask_opening()` in future sessions so the robot can reference prior experiences.

---

## 11. Running the System: Step-by-Step

### Prerequisites

```bash
cd llm-mediated-cooking
source env/bin/activate
# ensure .env contains: GROQ_API_KEY=gsk_...
# ensure Fast Downward is built: cd downward && ./build.py
# for real robot: passwordless SSH configured (see Section 9)
```

### (Optional) Reset Memory from a Previous Session

```bash
python3 main.py --reset
# Output: Memory cleared: KB, incident log, and session notes reset to empty.
```

### Full Walkthrough (Dummy Mode)

**Step 1: Launch**

```bash
python3 main.py --simple --dummy
```

Terminal shows:
```
  ┌─────────────────────────────────────┐
  │         Robot Task Planner          │
  │         ★  DUMMY MODE  ★            │
  └─────────────────────────────────────┘

  Dashboard → http://localhost:5050  (open this in your browser)

  Setting up initial plan... done.

  Stretch: Hi! I'm Stretch...
```

**Step 2: Open the Dashboard**

Navigate to http://localhost:5050. You'll see:
- The 12-step initial plan with Mark Done / Undo buttons per step and a progress bar
- All four KB categories (empty)
- World state showing initial positions (bread/ham in pantry, cheese/lettuce in kitchen)
- Phase badge: PRE_TASK (blue)

**Step 3: PRE_TASK Conversation**

```
You (or '/finish' to start execution): I can't go to the pantry

  (thinking...)
  Stretch: Got it — I'll take care of all the pantry trips. Just to clarify,
  do you mean you can't enter the pantry at all, or just prefer not to?

You (or '/finish' to start execution): I can't enter the pantry at all

  I understood the following:
    • [Your limitations] Human cannot enter the pantry.

  Update the plan with these changes? [y/n]: y

  Replanning... done.

  What changed in the plan:
  ✕  You move to the pantry
  ✕  You put down cheese in the pantry
  ...
  ✓  Robot picks up ham
  ✓  Robot moves to the pantry
  ...
```

Dashboard updates automatically. Continue until satisfied, then:
```
You (or '/finish' to start execution): /finish
```

**Step 4: Execution**

```
  FINAL PLAN
  ══════════════════════════════════════
  1  Robot  Robot picks up the lettuce
  2  Robot  Robot moves to the pantry
  ...

Begin simulated execution? [y/n]: y

  Starting execution (attempt 1)…

  [Dummy] Simulating 8 robot step(s).
  Type 'stop' to interrupt, or 'fail' to simulate a hardware failure.

  [Robot] Step 1/8:  Robot picks up the lettuce  → done  (mark in dashboard ↗)
  [Robot] Step 2/8:  Robot moves to the pantry   → done  (mark in dashboard ↗)
```

As each step is announced, click **Mark Done** in the dashboard for that step (and for any human steps that complete concurrently). The world state panel updates in real time.

**Step 5: Testing a Stop (Dummy Mode)**

During a step's 3-second delay, type `stop` and press Enter:
```
  [Robot] Step 3/8:  Robot picks up the bread  STOPPED

  Execution stopped: Stopped by user
  Update completed steps in the dashboard (http://localhost:5050).
  Press Enter when done (or type a note to help with replanning):
```

Mark any remaining completed steps in the dashboard, then press Enter when ready.

```
  Updating plan from current world state… done.

  Entering replanning conversation…

  Stretch: I noticed you stopped the task. I've updated the plan to reflect
  our progress so far — what made you want to stop, and how would you like
  to proceed?

You (or '/continue' to resume execution): I need to take a short break

  Stretch: Of course! Take all the time you need. The plan is all set from
  where we left off whenever you're ready to continue.

You (or '/continue' to resume execution): /continue

  Starting execution (attempt 2)…
```

**Step 6: Testing a Hardware Failure (Dummy Mode)**

Type `fail` during any step's 3-second delay:
```
  [Robot] Step 2/8:  Robot moves to the pantry  FAILED
  [Dummy] Simulated failure: ArUco tag not detected — item may have moved

  ⚠  Execution stopped: ArUco tag not detected — item may have moved
  Update completed steps in the dashboard...

  Updating plan from current world state… done.

  Stretch: I had to stop — it looks like the ArUco tag wasn't visible, so
  I couldn't locate the item. I've updated the plan from where things stand
  now. Would you like to reposition the item, or adjust who handles this step?
```

**Step 7: POST_TASK Conversation**

After the task succeeds:
```
  Task completed successfully!

  Stretch: Great work — we got everything swapped! We hit one hiccup but
  recovered well. How did you feel the collaboration went overall?

You (or '/finish' to exit): it was smooth, the dashboard was helpful

  Stretch (suggestion): Next time I could...

You (or '/finish' to exit): /finish

  (Remembered for next time: Human found the dashboard step tracker useful for coordination.)

  Session saved. Goodbye!
```

---

## 12. Nuances, Gotchas & Design Decisions

### N1: KB Updates Are Additive (Not Reset)

`update_kb_from_llm()` **appends** new facts. The LLM is prompted to return only the delta from the current message. If you change the prompt to return the full KB state, you must also reset before applying.

### N2: PDDL Modification Always Rebuilds from Master

`apply_kb_to_pddl()` always reads from `ORIGINAL_PROBLEM_FILE` and writes to `CURRENT_PROBLEM_FILE`. This prevents stale edits from accumulating. The same applies to world-state replanning: the PDDL is generated fresh each time from `WorldStateTracker`.

### N3: Constraint = Predicate Removal

The domain gives all agents all permissions by default. A constraint is enforced by **removing** the corresponding predicate from `:init`. Fast Downward cannot include actions that require an absent predicate.

### N4: World State Is Updated Exclusively via the Dashboard

The observer marks steps as done by clicking **Mark Done** in the browser. There is no automatic step tracking, no text-based state input, and no `/state` command. When a step is marked done, `_recompute_world_state_inplace()` replays all done steps from a base snapshot — this means Undo is always correct (just un-mark and replay). Both human and robot steps are tracked this way in both dummy and real modes.

### N5: Dashboard Update Ordering Matters

`dashboard.update_world_state()` **must** be called before `dashboard.update(..., plan_actions=...)`. The `update()` call snapshots `_state["world_state"]` as `base_world_state` for undo. If called with an empty world state, items not yet touched by marked-done steps will disappear from the world state panel. The defensive fix in `dashboard.update()` also reads directly from the live tracker when available.

### N6: Replanning Always Targets the Same Goal

Whether replanning from a file (PRE_TASK) or from world state (REPLAN), the goal is always:
```
bread+ham in kitchen, cheese+lettuce in pantry
```
There is no mechanism to change the goal mid-session.

### N7: Pre-Conversation Replan in REPLAN Phase

When execution stops, `execution_manager.py` runs `run_pddl_solver_from_world_state()` **before** entering the REPLAN conversation. The robot's opening message already describes the updated plan. Further constraint changes during the conversation trigger additional replanning via `conversation_engine.py`.

### N8: Python 3.9 Compatibility

All files use `from __future__ import annotations` at the top to enable `str | None` and `dict[str, str]` type hint syntax on Python 3.9. Without this, a runtime `TypeError` occurs when Python evaluates type annotations.

### N9: Grasp/Place Arguments Are Item Names, Not Rooms

`plan_bridge.py` extracts `tokens[2]` (the item name) for grasp/place args — not `tokens[3]` (the room). The robot's ArUco-tag grasp server identifies items by name.

### N10: Dashboard Stop Button Not Yet Polled

The `/stop` endpoint sets a flag readable via `dashboard.consume_stop_request()`. However, `execution_manager.py` does not currently poll this flag — it relies on terminal stdin `stop` command. The endpoint is wired and ready for future integration.

### N11: sas_plan Is Overwritten Each Solver Run

Fast Downward always writes to `downward/sas_plan`. If you need to compare plans across replans, capture the string before re-running the solver.

### N12: Human Steps Are Not Simulated in Dummy Mode

`dummy_bridge.py` filters to robot-only (`p2`) actions via `filter_robot_actions()`. Human steps are not stepped through — the observer is expected to do them physically (or imaginarily) and mark them done in the dashboard. This matches real-mode behavior exactly.

### N13: apply_kb_to_pddl SameFileError Guard

When `source_path == target_path` (e.g. during in-place KB application on `current_problem_replan.pddl`) and there are no KB removals, the copy is skipped entirely to avoid Python's `SameFileError`.

---

## 13. Glossary & Quick Reference

### Glossary

| Term | Definition |
|---|---|
| **PDDL** | Planning Domain Definition Language. Formal AI planning language. |
| **Domain** | PDDL file: types, predicates, actions. Never changes at runtime. |
| **Problem** | PDDL file: objects, initial state (`:init`), goal. Modified per session. |
| **KB** | Knowledge Base. 4-category store of conversation-extracted constraints. |
| **HRC** | Human-Robot Collaboration. |
| **p1** | Human agent (identifier in PDDL). |
| **p2** | Robot agent (Stretch RE1). |
| **Fast Downward** | Open-source AI planner using A* + LM-Cut heuristic. |
| **sas_plan** | Solver output: newline-delimited PDDL action sequence. |
| **SessionContext** | Python dataclass holding all state that flows between phases. |
| **PhaseConfig** | Python dataclass parameterizing the unified conversation loop. |
| **WorldStateTracker** | Live model of item/agent positions, serializable to PDDL `:init`. |
| **buffer window** | Pause after a stop/failure; observer updates dashboard then presses Enter to proceed. |
| **can-enter / can-take** | HRC permission predicates: presence = allowed, absence = forbidden. |
| **delta extraction** | LLM extracts only NEW facts from the current message (not full KB). |
| **base_world_state** | Snapshot of world state taken when plan was last set; used as replay base for undo. |
| **dummy mode** | Local plan simulation (`--dummy`) with no SSH or robot hardware. |

### Key Function Quick Reference

| Function | File | Purpose |
|---|---|---|
| `main()` | `main.py` | Orchestrates all three phases |
| `_build_context()` | `main.py` | Load memory → build SessionContext |
| `run_conversation_phase(ctx, config)` | `conversation_engine.py` | Unified conversation loop |
| `execute_with_replanning(ctx, config, executor)` | `execution_manager.py` | Execute → fail → REPLAN → retry loop |
| `run_buffer_window(reason)` | `buffer_window.py` | Blocks on Enter; observer updates dashboard first |
| `dummy_execute_plan(plan_text)` | `dummy_bridge.py` | Local simulation of robot execution |
| `execute_plan_on_robot(plan_text)` | `plan_bridge.py` | Translate PDDL → JSON → SSH to robot |
| `run_pddl_solver(domain, problem)` | `pddl_utils.py` | Run Fast Downward, return plan text |
| `apply_kb_to_pddl(kb, src, dst)` | `pddl_utils.py` | Remove KB predicates from problem file |
| `run_pddl_solver_from_world_state(ws, kb, domain)` | `pddl_utils.py` | Replan from current physical state |
| `_compute_plan_actions(raw_plan)` | `plan_utils.py` | Extract token lists for dashboard tracking |
| `get_introduction(plan_steps)` | `llm_engine_groq.py` | Robot warm intro at startup |
| `get_conversation_reply(history, kb, plan, override)` | `llm_engine_groq.py` | LLM response per turn |
| `extract_kb_updates(message)` | `llm_engine_groq.py` | Structured constraint extraction |
| `get_replanning_opening(..., stopped_by_user)` | `llm_engine_groq.py` | Context-aware REPLAN opening |
| `get_posttask_opening(...)` | `llm_engine_groq.py` | POST_TASK phase opening |
| `get_proactive_proposals(...)` | `llm_engine_groq.py` | Robot's improvement suggestions |
| `extract_session_note(history, kb)` | `llm_engine_groq.py` | Distil one memory note from debrief |
| `WorldStateTracker.to_pddl_init()` | `world_state.py` | Serialize world state to PDDL `:init` |
| `KnowledgeBase.get_pddl_removals()` | `knowledge_base.py` | List of predicates to remove from `:init` |
| `memory_store.clear_all()` | `memory_store.py` | Wipe all persistent memory to empty defaults |
| `dashboard.set_world_state_tracker(t)` | `dashboard.py` | Share live tracker reference with Flask |
| `dashboard.update(rows, kb, status, plan_actions)` | `dashboard.py` | Push plan + KB; snapshot base world state |
| `dashboard.update_world_state(dict)` | `dashboard.py` | Push world state dict (call before update()) |

### Environment Variables

| Variable | File | Required |
|---|---|---|
| `GROQ_API_KEY` | `.env` | Yes — missing causes `sys.exit(1)` at startup |

### Runtime Files

| File | Written By | When | Contents |
|---|---|---|---|
| `current_problem_simple.pddl` | `shutil.copy` + `apply_kb_to_pddl` | Startup and each PRE_TASK/POST_TASK replan | Modified PDDL problem |
| `current_problem_replan.pddl` | `run_pddl_solver_from_world_state` | Each mid-task replan | World-state-generated PDDL problem |
| `downward/sas_plan` | Fast Downward | Each solver run | PDDL action sequence |
| `memory/kb_persistent.json` | `_save_memory` / `clear_all` | End of session / `--reset` | KB state |
| `memory/incident_log.json` | `_save_memory` / `clear_all` | End of session / `--reset` | All incident records |
| `memory/session_notes.json` | POST_TASK end + `_save_memory` / `clear_all` | During and end of session / `--reset` | Free-text session notes |
