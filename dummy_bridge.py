"""
dummy_bridge.py

Simulates robot plan execution locally — no SSH, no robot required.
Steps through each robot action with a configurable delay.

During execution the user can type (then press Enter):
  stop  — simulate a user-initiated stop (triggers buffer window + REPLAN)
  fail  — simulate a hardware/software failure at the current step

World state is NOT updated automatically. After each step the terminal
announces what the robot "did"; the observer marks it done in the dashboard.

Usage:
  python3 main.py --simple --dummy
"""

from __future__ import annotations

import random
import select
import sys

from plan_bridge import parse_sas_plan, filter_robot_actions
from plan_utils import _humanize_action

_STEP_DELAY = 3.0   # seconds between robot steps

# Realistic-sounding failure messages (sampled randomly on 'fail')
_FAILURE_REASONS = [
    "Action server timeout: grasp did not complete within 30s",
    "ArUco tag not detected — item may have moved",
    "Robot arm reached joint limit during approach",
    "Navigation: collision avoidance triggered, path blocked",
    "Gripper force sensor reading out of range",
    "Place action failed: target surface not detected",
]


def _poll_stdin(timeout: float) -> str | None:
    """
    Wait up to `timeout` seconds for a line on stdin.
    Returns the stripped lowercase line, or None on timeout.
    """
    ready, _, _ = select.select([sys.stdin], [], [], timeout)
    if ready:
        return sys.stdin.readline().strip().lower()
    return None


def dummy_execute_plan(plan_text: str, world_state=None) -> dict:  # world_state param kept for API compat
    """
    Walk through the robot-only actions in the plan one by one, pausing
    between each step. Returns the same result dict as execute_plan_on_robot().

    Args:
        plan_text:   raw sas_plan string
        world_state: unused (world state is now updated by the observer via the dashboard)

    Returns:
        {"success": bool, "steps_completed": int,
         "failure_reason": str | None, "stopped_by": "user" | "error" | None}
    """
    all_actions   = parse_sas_plan(plan_text)
    robot_actions = filter_robot_actions(all_actions)

    if not robot_actions:
        print("[Dummy] No robot actions in plan — nothing to simulate.")
        return {"success": True, "steps_completed": 0,
                "failure_reason": None, "stopped_by": None}

    total = len(robot_actions)
    print(f"\n  [Dummy] Simulating {total} robot step(s).")
    print("  Type 'stop' to interrupt, or 'fail' to simulate a hardware failure.\n")

    for i, tokens in enumerate(robot_actions, 1):
        desc = _humanize_action(tokens)
        print(f"  [Robot] Step {i}/{total}:  {desc}", end="", flush=True)

        # Sleep _STEP_DELAY seconds, polling stdin every 0.2 s
        elapsed = 0.0
        cmd = None
        while elapsed < _STEP_DELAY:
            tick = min(0.2, _STEP_DELAY - elapsed)
            cmd  = _poll_stdin(tick)
            elapsed += tick
            if cmd in ("stop", "fail"):
                break

        if cmd == "stop":
            print("  STOPPED")
            print()
            return {
                "success":        False,
                "steps_completed": i - 1,
                "failure_reason": "Stopped by user",
                "stopped_by":     "user",
            }

        if cmd == "fail":
            reason = random.choice(_FAILURE_REASONS)
            print("  FAILED")
            print(f"  [Dummy] Simulated failure: {reason}\n")
            return {
                "success":        False,
                "steps_completed": i - 1,
                "failure_reason": reason,
                "stopped_by":     "error",
            }

        # Step completed — announce; observer marks it done in the dashboard
        print("  → done  (mark in dashboard ↗)")

    print(f"\n  [Robot] All {total} step(s) completed successfully.\n")
    return {
        "success":        True,
        "steps_completed": total,
        "failure_reason": None,
        "stopped_by":     None,
    }
