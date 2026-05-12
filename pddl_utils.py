from __future__ import annotations
"""
pddl_utils.py

PDDL solver invocation, problem modification, and world-state-based
problem generation. All functions take explicit path parameters so
they can be called from any module without importing main.py.
"""

import re
import subprocess
import shutil
from pathlib import Path


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_pddl(content: str) -> tuple[bool, str | None]:
    stripped = content.strip()
    if not stripped.startswith("(define"):
        return False, "Does not start with '(define'"
    if stripped.count("(") != stripped.count(")"):
        return False, "Unbalanced parentheses"
    return True, None


# ---------------------------------------------------------------------------
# Solver invocation
# ---------------------------------------------------------------------------

def run_pddl_solver(domain_file: Path, problem_path: Path) -> str | None:
    """
    Run Fast Downward on domain_file + problem_path.
    Returns the raw sas_plan text, or None on failure.
    """
    solver_dir    = Path("downward")
    solver_script = "./fast-downward.py"
    domain_rel    = Path("..") / domain_file
    problem_rel   = Path("..") / problem_path

    command = [str(solver_script), str(domain_rel), str(problem_rel),
               "--search", "astar(lmcut())"]
    try:
        result = subprocess.run(
            command, cwd=solver_dir,
            capture_output=True, text=True, check=True
        )
        plan_path = solver_dir / "sas_plan"
        if plan_path.exists():
            return plan_path.read_text()
        if "--debug" in __import__("sys").argv:
            print("[Solver] Ran but sas_plan was not created.")
            print(result.stdout)
        return None
    except subprocess.CalledProcessError as e:
        if "--debug" in __import__("sys").argv:
            print(f"[Solver ERROR] exit {e.returncode}")
            print("STDOUT:", e.stdout)
            print("STDERR:", e.stderr)
        else:
            print("  (Could not generate a valid plan with these constraints.)")
        return None
    except FileNotFoundError:
        print("  Error: Fast Downward planner not found.")
        return None


# ---------------------------------------------------------------------------
# Problem modification (apply KB constraint removals)
# ---------------------------------------------------------------------------

def apply_kb_to_pddl(kb, source_path: Path, target_path: Path) -> bool:
    """
    Read source_path, remove KB constraint predicates, write to target_path.
    Always rebuilds from source so stale edits never accumulate.
    """
    try:
        content = source_path.read_text()
    except FileNotFoundError:
        print(f"  Error: Problem file not found: {source_path}")
        return False

    removals = kb.get_pddl_removals()

    if not removals:
        if source_path != target_path:
            shutil.copy(source_path, target_path)
        return True

    for predicate in removals:
        escaped = re.escape(predicate)
        content = re.sub(r"\n[ \t]*" + escaped + r"[ \t]*", "", content)

    content = re.sub(r"\n{3,}", "\n\n", content)

    valid, err = validate_pddl(content)
    if not valid:
        if "--debug" in __import__("sys").argv:
            print(f"[PDDL Validation Error] {err}")
        return False

    target_path.write_text(content)
    if "--debug" in __import__("sys").argv:
        print(f"[PDDL] Removed {len(removals)} predicate(s): {removals}")
    return True


# ---------------------------------------------------------------------------
# World-state-based problem generation (for replanning)
# ---------------------------------------------------------------------------

_REPLAN_PROBLEM_PATH = Path("current_problem_replan.pddl")

def run_pddl_solver_from_world_state(world_state, kb, domain_file: Path) -> str | None:
    """
    Generate a fresh PDDL problem from the current world state, apply KB
    constraint removals on top, run the solver, and return the plan text.
    The goal is always the full four-ingredient swap — the planner finds
    only the remaining steps needed from the current state.
    """
    problem_content = _generate_problem_pddl(world_state)

    valid, err = validate_pddl(problem_content)
    if not valid:
        print(f"  Error generating replan problem: {err}")
        return None

    _REPLAN_PROBLEM_PATH.write_text(problem_content)

    # Apply KB removals on top of the generated problem
    if not apply_kb_to_pddl(kb, _REPLAN_PROBLEM_PATH, _REPLAN_PROBLEM_PATH):
        return None

    return run_pddl_solver(domain_file, _REPLAN_PROBLEM_PATH)


def _generate_problem_pddl(world_state) -> str:
    """
    Build a complete PDDL problem string from the WorldStateTracker.
    The :objects and :goal sections are fixed; only :init varies.
    """
    init_block = world_state.to_pddl_init()
    return f"""(define (problem fetch-problem)
  (:domain fetch-domain)

  (:objects
    p1 p2 - agent
    kitchen pantry - room
    bread ham cheese lettuce - item
  )

  (:init
{init_block}
  )

  (:goal
    (and
      (in-room bread kitchen)
      (in-room ham kitchen)
      (in-room cheese pantry)
      (in-room lettuce pantry)
    )
  )
)
"""
