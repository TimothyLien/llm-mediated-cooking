"""
plan_bridge.py

Translates a Fast Downward sas_plan (simple PDDL) into the JSON format
expected by the ROS2 controller on the Hello Robot, then sends it over SSH.

One-time setup (passwordless auth):
    ssh-keygen -t ed25519 -C "llm-cooking"   # skip if you already have a key
    ssh-copy-id hello-robot@10.49.91.168      # enter password once, never again
"""

import json
import select
import subprocess
import sys
import threading

# ---------------------------------------------------------------------------
# Connection config
# ---------------------------------------------------------------------------
ROBOT_USER    = "hello-robot"
ROBOT_HOST    = "10.49.91.168"
ROS_DISTRO    = "humble"
WORKSPACE     = "~/llm_cooking_ws"
ACTION_SERVER = "/execute_plan"
ACTION_TYPE   = "servers_interfaces/action/ExecutePlan"


# ---------------------------------------------------------------------------
# PDDL → JSON translation
# ---------------------------------------------------------------------------

def parse_sas_plan(plan_text: str) -> list[list[str]]:
    """
    Parse raw sas_plan text into a list of token lists.
    Each line like '(move p2 kitchen pantry)' becomes ['move', 'p2', 'kitchen', 'pantry'].
    Comment lines (starting with ';') and blank lines are skipped.
    """
    actions = []
    for line in plan_text.splitlines():
        line = line.strip()
        if not line or line.startswith(';'):
            continue
        tokens = line.strip('()').split()
        actions.append(tokens)
    return actions


def filter_robot_actions(actions: list[list[str]]) -> list[list[str]]:
    """Keep only actions whose actor token is p2 (the robot)."""
    return [a for a in actions if len(a) > 1 and a[1] == 'p2']


def translate_to_controller_json(actions: list[list[str]]) -> list[dict]:
    """
    Map simple PDDL robot actions to the controller's JSON step format.

    PDDL token layout:
      move  p2 <from> <to>    →  {"type": "move",  "arg": <to>}
      take  p2 <item> <room>  →  {"type": "grasp", "arg": <item>}
      drop  p2 <item> <room>  →  {"type": "place", "arg": <item>}

    Grasp uses the ingredient name so the grasp server can locate the
    correct ArUco tag for that item.
    Place uses the ingredient name so the place server can find the
    item's designated spot on the table (tagged with the ingredient name).
    """
    steps = []
    for tokens in actions:
        action = tokens[0]
        if action == 'move':
            # move p2 <from> <to>  →  navigate to destination room
            steps.append({"type": "move",  "arg": tokens[3]})
        elif action == 'take':
            # take p2 <item> <room>  →  grasp the named ingredient
            steps.append({"type": "grasp", "arg": tokens[2]})
        elif action == 'drop':
            # drop p2 <item> <room>  →  place at the ingredient's tagged spot
            steps.append({"type": "place", "arg": tokens[2]})
        else:
            print(f"[Bridge] Skipping unrecognised action: {tokens}")
    return steps


def sas_plan_to_controller_json(plan_text: str) -> list[dict] | None:
    """
    Full pipeline: raw sas_plan text → filtered, translated JSON step list.
    Returns None if no robot actions were found.
    """
    all_actions   = parse_sas_plan(plan_text)
    robot_actions = filter_robot_actions(all_actions)

    if not robot_actions:
        print("[Bridge] No robot (p2) actions found in plan.")
        return None

    steps = translate_to_controller_json(robot_actions)

    print("\n[Bridge] Translated robot plan:")
    for i, step in enumerate(steps, 1):
        print(f"  {i:>2}. {step['type']:6}  {step['arg']}")

    return steps


# ---------------------------------------------------------------------------
# SSH transport
# ---------------------------------------------------------------------------

def _source_cmds() -> str:
    return (
        f"source /opt/ros/{ROS_DISTRO}/setup.bash && "
        f"source {WORKSPACE}/install/setup.bash"
    )


def _build_remote_cmd(plan_json_str: str) -> str:
    escaped  = plan_json_str.replace('"', '\\"')
    yaml_arg = '"{plan_json: \'' + escaped + '\'}"'
    ros_cmd  = (
        f"ros2 action send_goal --feedback "
        f"{ACTION_SERVER} {ACTION_TYPE} {yaml_arg}"
    )
    return f"{_source_cmds()} && {ros_cmd}"


def _cancel_remote() -> None:
    """
    Open a second SSH connection and SIGINT the remote ros2 action send_goal
    process. SIGINT causes the action client to send a proper cancel request
    to the controller before exiting.
    """
    target   = f"{ROBOT_USER}@{ROBOT_HOST}"
    kill_cmd = f"{_source_cmds()} && pkill -SIGINT -f 'ros2 action send_goal'"
    try:
        subprocess.run(
            ["ssh", target, kill_cmd],
            timeout=5,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass  # best-effort — local terminate will follow immediately


# ---------------------------------------------------------------------------
# Main entry point (called from main.py)
# ---------------------------------------------------------------------------

def execute_plan_on_robot(plan_text: str) -> dict:
    """
    Translate sas_plan, send to the robot over SSH, and keep stdin live so
    the user can type 'stop' at any time to cancel execution.

    Returns a result dict:
      {
        "success":        bool,
        "steps_completed": int,   # approximate (based on output lines)
        "failure_reason": str | None,
        "stopped_by":     "user" | "error" | None
      }
    """
    steps = sas_plan_to_controller_json(plan_text)
    if steps is None:
        return {"success": False, "steps_completed": 0,
                "failure_reason": "No robot actions in plan", "stopped_by": "error"}

    target     = f"{ROBOT_USER}@{ROBOT_HOST}"
    remote_cmd = _build_remote_cmd(json.dumps(steps))

    print(f"\n[SSH] Connecting to {target} ...")
    print("[Task] Robot is executing. Type 'stop' to cancel.\n")

    try:
        proc = subprocess.Popen(
            ["ssh", target, remote_cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except FileNotFoundError:
        return {"success": False, "steps_completed": 0,
                "failure_reason": "'ssh' binary not found", "stopped_by": "error"}

    steps_completed = 0
    output_lines: list[str] = []

    def _stream():
        nonlocal steps_completed
        for line in proc.stdout:
            output_lines.append(line.rstrip())
            print(f"  [Robot] {line}", end="", flush=True)
            # Heuristic: count "Executing step" lines as completed steps
            if "executing step" in line.lower() or "completed step" in line.lower():
                steps_completed += 1

    reader = threading.Thread(target=_stream, daemon=True)
    reader.start()

    stopped = False
    while proc.poll() is None:
        ready, _, _ = select.select([sys.stdin], [], [], 0.2)
        if ready:
            cmd = sys.stdin.readline().strip().lower()
            if cmd == "stop":
                print("\n[Task] Stop requested — cancelling task on robot ...")
                _cancel_remote()
                proc.terminate()
                stopped = True
                break

    reader.join(timeout=2)
    proc.wait()

    if stopped:
        print("[Task] Task cancelled.")
        return {"success": False, "steps_completed": steps_completed,
                "failure_reason": "Stopped by user", "stopped_by": "user"}
    elif proc.returncode == 0:
        print("\n[Task] Task completed successfully.")
        return {"success": True, "steps_completed": steps_completed,
                "failure_reason": None, "stopped_by": None}
    else:
        reason = f"SSH exit code {proc.returncode}"
        print(f"\n[SSH ERROR] {reason}")
        print(f"  If this is an auth error, run: ssh-copy-id {target}")
        return {"success": False, "steps_completed": steps_completed,
                "failure_reason": reason, "stopped_by": "error"}
