"""
execution_manager.py

Manages the full execution loop: execute plan → detect failure → buffer window →
REPLAN conversation → re-execute. Loops until success or user aborts.
"""

from __future__ import annotations

import sys
from typing import Callable, Optional

import dashboard
from buffer_window import run_buffer_window

_DEBUG = "--debug" in sys.argv

MAX_REPLAN_ATTEMPTS = 5


def execute_with_replanning(ctx, replan_config,
                            executor: Optional[Callable] = None) -> bool:
    """
    Execute ctx.current_plan on the robot, handling failures by entering
    the REPLAN conversation phase when needed.

    Args:
        ctx           — SessionContext (mutated in place)
        replan_config — PhaseConfig for the REPLAN phase
        executor      — callable(plan_text) → result dict.
                        Defaults to plan_bridge.execute_plan_on_robot.
                        Pass dummy_bridge.dummy_execute_plan for local testing.

    Returns:
        True if the overall task succeeded, False if user aborted.
    """
    if executor is None:
        from plan_bridge import execute_plan_on_robot
        executor = execute_plan_on_robot

    from conversation_engine import run_conversation_phase

    attempt = 0

    while attempt < MAX_REPLAN_ATTEMPTS:
        attempt += 1
        dashboard.set_status(f"Executing (attempt {attempt})…")
        print(f"\n  Starting execution (attempt {attempt})…")

        result = executor(ctx.current_plan)

        if result["success"]:
            dashboard.set_status("Task complete ✓")
            return True

        # --- Failure path ---
        failure_reason   = result.get("failure_reason", "unknown issue")
        steps_completed  = result.get("steps_completed", 0)
        stopped_by_user  = result.get("stopped_by") == "user"

        if _DEBUG:
            print(f"[Exec] Failure: {failure_reason}, steps_completed={steps_completed}")

        # Open an incident record
        ctx.start_incident(trigger=failure_reason if not stopped_by_user else "user stop")
        ctx.current_incident["steps_completed"] = steps_completed
        ctx.current_incident["failure_reason"]  = failure_reason
        ctx.current_incident["stopped_by"]      = result.get("stopped_by")

        # Buffer window — let observer update world state via dashboard before replanning
        run_buffer_window(reason=failure_reason)

        # Replan from the current world state (as updated by observer in dashboard)
        from pddl_utils import run_pddl_solver_from_world_state
        from plan_utils import _compute_plan_rows as _cpr, _compute_plan_actions

        dashboard.set_status("Updating plan from world state…")
        print("\n  Updating plan from current world state…", end="", flush=True)
        new_plan = run_pddl_solver_from_world_state(
            ctx.world_state, ctx.kb, ctx.domain_file
        )
        if new_plan:
            ctx.current_plan = new_plan
            dashboard.update(
                _cpr(new_plan), ctx.kb, "Plan updated from world state",
                plan_actions=_compute_plan_actions(new_plan),
            )
            print(" done.")
        else:
            print(" could not replan from current world state — keeping previous plan.")

        # Enter REPLAN conversation (robot will ask why stopped / explain failure)
        print("\n  Entering replanning conversation…")
        dashboard.set_status("Replanning…")
        run_conversation_phase(ctx, replan_config)

        if ctx.current_incident:
            ctx.current_incident["replan_count"] = \
                ctx.current_incident.get("replan_count", 0) + 1

        # If the user typed /finish without providing a new plan, abort
        if not ctx.current_plan:
            ctx.close_incident("aborted — no plan after replan conversation")
            return False

        ctx.close_incident(f"replanned (attempt {attempt})")

    print(f"\n  Reached maximum replan attempts ({MAX_REPLAN_ATTEMPTS}). Aborting.")
    dashboard.set_status("Max replan attempts reached.")
    return False
