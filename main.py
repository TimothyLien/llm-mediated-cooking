"""
main.py

Entry point. Orchestrates three phases:
  1. PRE_TASK  — collaborative planning conversation
  2. EXECUTION — robot executes the plan (with automatic replanning on failure)
  3. POST_TASK — post-completion deliberation and memory update

Usage:
  python3 main.py [--simple] [--debug] [--dummy]

  --simple  use domain_simple.pddl / problem_simple.pddl (required for robot)
  --debug   verbose logging
  --dummy   simulate robot execution locally (no SSH, no robot required)
            type 'stop' during execution to test REPLAN flow
            type 'fail' to simulate a hardware failure
  --reset   wipe all persistent memory (KB, incident log, session notes) and exit
"""

import shutil
import sys
from pathlib import Path

import dashboard
import memory_store
from knowledge_base import KnowledgeBase
from world_state import WorldStateTracker
from session_context import SessionContext
from plan_utils import print_plan, _compute_plan_rows, _compute_plan_actions
from pddl_utils import run_pddl_solver, apply_kb_to_pddl
from llm_engine_groq import get_introduction
from conversation_engine import run_conversation_phase
from execution_manager import execute_with_replanning
from phase_configs import PRE_TASK_CONFIG, REPLAN_CONFIG, POST_TASK_CONFIG

_SIMPLE_MODE = "--simple" in sys.argv
_DEBUG       = "--debug"  in sys.argv
_DUMMY_MODE  = "--dummy"  in sys.argv
_RESET_MODE  = "--reset"  in sys.argv

if _RESET_MODE:
    import memory_store as _ms
    _ms.clear_all()
    print("  Memory cleared: KB, incident log, and session notes reset to empty.")
    sys.exit(0)

DOMAIN_FILE           = Path("pddl/domain_simple.pddl") if _SIMPLE_MODE else Path("pddl/domain.pddl")
ORIGINAL_PROBLEM_FILE = Path("pddl/problem_simple.pddl") if _SIMPLE_MODE else Path("pddl/problem.pddl")
CURRENT_PROBLEM_FILE  = Path("current_problem_simple.pddl") if _SIMPLE_MODE else Path("current_problem.pddl")


def _build_context() -> SessionContext:
    """Create a SessionContext loaded from persistent memory."""
    kb = KnowledgeBase()
    kb.load_from_file(memory_store.KB_PATH)

    world_state    = WorldStateTracker()
    incident_log   = memory_store.load_incident_log()
    session_notes  = memory_store.load_session_notes()
    run_number     = len(incident_log) + 1

    return SessionContext(
        kb=kb,
        world_state=world_state,
        current_plan=None,
        incident_log=incident_log,
        session_notes=session_notes,
        run_number=run_number,
        original_problem_file=ORIGINAL_PROBLEM_FILE,
        current_problem_file=CURRENT_PROBLEM_FILE,
        domain_file=DOMAIN_FILE,
    )


def _save_memory(ctx: SessionContext) -> None:
    ctx.kb.save_to_file(memory_store.KB_PATH)
    memory_store.save_incident_log(ctx.incident_log)
    memory_store.save_session_notes(ctx.session_notes)


def main() -> None:
    print()
    print("  ┌─────────────────────────────────────┐")
    print("  │         Robot Task Planner          │")
    if _DUMMY_MODE:
        print("  │         ★  DUMMY MODE  ★            │")
    print("  └─────────────────────────────────────┘")
    print()

    # Start dashboard
    port = dashboard.start()
    print(f"  Dashboard → http://localhost:{port}  (open this in your browser)")
    print()

    # Verify files exist
    if not DOMAIN_FILE.exists():
        print(f"Error: Domain file not found: {DOMAIN_FILE}")
        sys.exit(1)
    if not ORIGINAL_PROBLEM_FILE.exists():
        print(f"Error: Problem file not found: {ORIGINAL_PROBLEM_FILE}")
        sys.exit(1)

    # Build context from persistent memory
    ctx = _build_context()
    dashboard.set_world_state_tracker(ctx.world_state)

    if ctx.session_notes:
        print(f"  (Loaded {len(ctx.session_notes)} session note(s) from previous runs.)")
    if any(v for v in ctx.kb.get_state().values()):
        print("  (Loaded preferences/constraints from previous session.)")
    print()

    # -----------------------------------------------------------------------
    # Initial plan generation
    # -----------------------------------------------------------------------
    try:
        shutil.copy(ORIGINAL_PROBLEM_FILE, CURRENT_PROBLEM_FILE)
    except Exception as e:
        print(f"Error: Could not initialise problem file: {e}")
        sys.exit(1)

    # Apply any persisted KB constraints before solving
    apply_kb_to_pddl(ctx.kb, ORIGINAL_PROBLEM_FILE, CURRENT_PROBLEM_FILE)

    dashboard.set_status("Generating initial plan…")
    print("  Setting up initial plan...", end="", flush=True)
    ctx.current_plan = run_pddl_solver(DOMAIN_FILE, CURRENT_PROBLEM_FILE)

    if not ctx.current_plan:
        print(" failed.\n")
        print("Error: Could not generate an initial plan. Exiting.")
        sys.exit(1)

    print(" done.\n")
    dashboard.update_world_state(ctx.world_state.to_dict())
    dashboard.update(
        _compute_plan_rows(ctx.current_plan),
        ctx.kb, "Ready",
        plan_actions=_compute_plan_actions(ctx.current_plan),
    )
    dashboard.set_phase("PRE_TASK")

    # Robot introduction
    plan_steps_str = "\n".join(
        f"  {i}. {r['actor']}: {r['description']}"
        for i, r in enumerate(_compute_plan_rows(ctx.current_plan), 1)
    )
    print("  (introducing...)", end="\r", flush=True)
    intro = get_introduction(plan_steps_str)
    print(f"  Stretch: {intro}\n")

    # -----------------------------------------------------------------------
    # Phase 1: PRE_TASK conversation
    # -----------------------------------------------------------------------
    print("  Type your preferences or constraints. Type '/finish' when ready to execute.\n")
    dashboard.set_status("Pre-task planning…")
    run_conversation_phase(ctx, PRE_TASK_CONFIG)

    # -----------------------------------------------------------------------
    # Phase 2: Execution (with automatic replanning on failure)
    # -----------------------------------------------------------------------
    if _SIMPLE_MODE:
        print()
        print("=" * 62)
        print("  FINAL PLAN")
        print("=" * 62)
        print_plan(ctx.current_plan)
        print()

        if _DUMMY_MODE:
            confirm = input("Begin simulated execution? [y/n]: ").strip().lower()
        else:
            confirm = input("Send this plan to the robot and begin execution? [y/n]: ").strip().lower()
        if confirm != "y":
            print("Execution cancelled. Plan saved in downward/sas_plan.")
            _save_memory(ctx)
            return

        # Build executor: real SSH or local dummy
        if _DUMMY_MODE:
            from dummy_bridge import dummy_execute_plan
            executor = lambda plan: dummy_execute_plan(plan, ctx.world_state)
        else:
            from plan_bridge import execute_plan_on_robot
            executor = execute_plan_on_robot

        dashboard.set_phase("REPLAN")
        success = execute_with_replanning(ctx, REPLAN_CONFIG, executor=executor)
    else:
        print("(Non-simple mode — robot execution not available from main.py)")
        print("Run 'python3 app.py' to launch the web simulation.")
        _save_memory(ctx)
        return

    # -----------------------------------------------------------------------
    # Phase 3: POST_TASK conversation
    # -----------------------------------------------------------------------
    if success:
        print("\n  Task completed successfully!")
    else:
        print("\n  Task ended without full completion.")

    dashboard.set_phase("POST_TASK")
    dashboard.set_status("Post-task debrief…")
    print("\n  Let's reflect on how it went. Type '/finish' when done.\n")
    run_conversation_phase(ctx, POST_TASK_CONFIG)

    # Persist KB and memory at the very end
    _save_memory(ctx)
    print("\n  Session saved. Goodbye!")


if __name__ == "__main__":
    main()
