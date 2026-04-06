import textwrap
import sys
import re
import subprocess
import shutil
from pathlib import Path
from knowledge_base import KnowledgeBase
from llm_engine_groq import get_conversation_reply, extract_kb_updates, summarize_chat_history

DOMAIN_FILE           = Path("pddl/domain.pddl")
ORIGINAL_PROBLEM_FILE = Path("pddl/problem.pddl")
CURRENT_PROBLEM_FILE  = Path("current_problem.pddl")

# Rolling history settings
MAX_HISTORY_TURNS    = 10   # Keep this many recent messages verbatim
SUMMARIZE_THRESHOLD  = 18   # Trigger compression once history exceeds this

CURRENT_PROBLEM_FILE.parent.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# PDDL Solver
# ---------------------------------------------------------------------------

def run_pddl_solver(problem_path=CURRENT_PROBLEM_FILE):
    solver_dir    = Path("downward")
    solver_script = "./fast-downward.py"
    problem_rel   = Path("..") / problem_path
    domain_rel    = Path("..") / DOMAIN_FILE

    command = [str(solver_script), str(domain_rel), str(problem_rel),
               "--search", "astar(lmcut())"]
    try:
        result = subprocess.run(command, cwd=solver_dir,
                                capture_output=True, text=True, check=True)
        plan_path = solver_dir / "sas_plan"
        if plan_path.exists():
            return plan_path.read_text()
        print("[Solver ERROR] Solver ran but 'sas_plan' was not created.")
        print("Solver Output:\n", result.stdout)
        return None
    except subprocess.CalledProcessError as e:
        print(f"[Solver ERROR] Fast Downward failed (exit {e.returncode}).")
        print("STDOUT:", e.stdout)
        print("STDERR:", e.stderr)
        return None
    except FileNotFoundError:
        print("[Solver ERROR] Could not find the Fast Downward executable.")
        return None


# ---------------------------------------------------------------------------
# PDDL Modification (programmatic — no LLM)
# ---------------------------------------------------------------------------

def apply_kb_to_pddl(kb, source_path, target_path):
    """
    Reads the original PDDL problem, removes all predicates listed in the KB,
    validates the result, and writes it to target_path.

    Constraint enforcement is purely string-based — no LLM involved.
    Always rebuilds from source_path so stale edits never accumulate.
    """
    try:
        content = source_path.read_text()
    except FileNotFoundError:
        print(f"[PDDL Error] Source file not found: {source_path}")
        return False

    removals = kb.get_pddl_removals()

    if not removals:
        shutil.copy(source_path, target_path)
        print("[PDDL] No constraints to apply — using original problem.")
        return True

    for predicate in removals:
        # Remove the predicate and the newline that preceded it
        escaped = re.escape(predicate)
        content = re.sub(r'\n[ \t]*' + escaped + r'[ \t]*', '', content)

    # Tidy up runs of blank lines left behind
    content = re.sub(r'\n{3,}', '\n\n', content)

    valid, err = validate_pddl(content)
    if not valid:
        print(f"[PDDL Validation Error] {err}")
        return False

    target_path.write_text(content)
    print(f"[PDDL] Modified problem saved to: {target_path}")
    print(f"[PDDL] Removed {len(removals)} predicate(s): {removals}")
    return True


def validate_pddl(content: str):
    """
    Basic sanity checks before writing PDDL to disk.
    Catches the most common LLM output errors early.
    """
    stripped = content.strip()
    if not stripped.startswith("(define"):
        return False, "Does not start with '(define'"
    open_count  = stripped.count('(')
    close_count = stripped.count(')')
    if open_count != close_count:
        return False, f"Unbalanced parentheses ({open_count} open, {close_count} close)"
    return True, None


# ---------------------------------------------------------------------------
# Plan Display & Diff
# ---------------------------------------------------------------------------

def _parse_actions(raw_plan_str):
    if not raw_plan_str:
        return []
    return [l.strip() for l in raw_plan_str.split('\n')
            if l.strip() and not l.strip().startswith(';')]


def print_plan(raw_plan_string):
    plan_data = {"plan": []}
    if not raw_plan_string:
        print("  (No plan steps yet)")
        return plan_data

    actions = _parse_actions(raw_plan_string)
    for i, action in enumerate(actions):
        assigned_to = "N/A"
        if " p1" in action:
            assigned_to = "Human"
        elif " p2" in action:
            assigned_to = "Robot"
        plan_data["plan"].append({
            "step": i + 1,
            "task": action.strip('()'),
            "assigned_to": assigned_to
        })

    print("  " + "-" * 60)
    print(f"  {'Step':<5} | {'Assigned To':<11} | {'Task':<35}")
    print("  " + "-" * 60)
    for item in plan_data["plan"]:
        print(f"  {item['step']:<5} | {item['assigned_to']:<11} | {str(item['task']):<35}")
    print("  " + "-" * 60)
    return plan_data


def diff_plans(old_plan_str, new_plan_str):
    """Prints a human-readable diff between two raw plan strings."""
    old = _parse_actions(old_plan_str)
    new = _parse_actions(new_plan_str)
    old_set = set(old)
    new_set = set(new)

    removed = [a for a in old if a not in new_set]
    added   = [a for a in new if a not in old_set]
    kept    = [a for a in new if a in old_set]

    print("\n  PLAN DIFF:")
    print("  " + "-" * 50)
    if not removed and not added:
        print("  (No changes to plan actions)")
    for a in removed:
        print(f"  - REMOVED : {a.strip('()')}")
    for a in added:
        print(f"  + ADDED   : {a.strip('()')}")
    print(f"  = UNCHANGED: {len(kept)} step(s)")
    print("  " + "-" * 50)


def print_ui(kb, plan):
    print("\n" + "=" * 80)
    print("PANEL 1: SHARED PLAN")
    print("-" * 38)
    print_plan(plan)
    print("\nPANEL 2: KNOWLEDGE BASE")
    print("-" * 38)
    print(textwrap.indent(kb.get_state_as_string(), "  "))
    print("=" * 80 + "\n")


# ---------------------------------------------------------------------------
# File Loading
# ---------------------------------------------------------------------------

def load_domain_pddl():
    try:
        return DOMAIN_FILE.read_text()
    except FileNotFoundError:
        print(f"[ERROR] Domain file not found: {DOMAIN_FILE}")
        sys.exit(1)

def load_problem_pddl():
    try:
        return ORIGINAL_PROBLEM_FILE.read_text()
    except FileNotFoundError:
        print(f"[ERROR] Problem file not found: {ORIGINAL_PROBLEM_FILE}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Chat History Management
# ---------------------------------------------------------------------------

def manage_history(chat_history):
    """
    If history exceeds SUMMARIZE_THRESHOLD, compress older messages into a
    single summary entry and prepend it to the recent window.
    """
    if len(chat_history) <= SUMMARIZE_THRESHOLD:
        return chat_history

    print("[History] Compressing old conversation history...")
    to_summarize = chat_history[:-MAX_HISTORY_TURNS]
    recent       = chat_history[-MAX_HISTORY_TURNS:]
    summary      = summarize_chat_history(to_summarize)
    return [f"[CONVERSATION SUMMARY]: {summary}"] + recent


# ---------------------------------------------------------------------------
# Main Loop
# ---------------------------------------------------------------------------

def main():
    kb            = KnowledgeBase()
    chat_history  = []
    latest_raw_plan = None

    print("--- Collaborative HRI Prototype ---")

    load_domain_pddl()   # validate file exists early; content not needed in prompts
    problem_pddl = load_problem_pddl()  # kept for reference/display only

    try:
        shutil.copy(ORIGINAL_PROBLEM_FILE, CURRENT_PROBLEM_FILE)
        print(f"Initial problem copied to {CURRENT_PROBLEM_FILE}")
    except Exception as e:
        print(f"[ERROR] Could not copy initial problem file: {e}")
        sys.exit(1)

    print("\nRunning PDDL Solver for initial plan...")
    latest_raw_plan = run_pddl_solver(CURRENT_PROBLEM_FILE)

    if not latest_raw_plan:
        print("[ERROR] Initial plan generation failed. Exiting.")
        sys.exit(1)

    print("\n" + "=" * 80)
    print("INITIAL PLAN GENERATED")
    print("=" * 80)
    print("Assistant: I've generated an initial plan. Share any preferences or")
    print("           constraints and I'll update the plan accordingly.")

    # Holds the original user message when a clarifying question is pending.
    # On the next turn the answer is combined with this for extraction context.
    pending_user_input = None

    while True:
        # A. Display current state
        print_ui(kb, latest_raw_plan)

        # B. Get human input
        user_input = input("You (or '/finish' to finalize): ").strip()

        if user_input.lower() == "/quit":
            print("Exiting. Goodbye!")
            break
        if user_input.lower() == "/finish":
            print("\nUser finished planning.")
            break

        chat_history.append(f"You: {user_input}")

        # C. Conversational reply — lean call, no domain PDDL
        reply_data = get_conversation_reply(
            chat_history=chat_history,
            kb_string=kb.get_state_as_string(),
            plan_data=latest_raw_plan
        )
        reply = reply_data.get("reply", "I'm not sure what to say.")
        is_clarifying = reply_data.get("is_clarifying_question", False)
        print(f"\nAssistant: {reply}")
        chat_history.append(f"Assistant: {reply}")

        # If the assistant asked a clarifying question, save the original
        # message and wait for the user's answer before extracting anything.
        if is_clarifying:
            pending_user_input = user_input
            continue

        # D. Structured extraction — low-temp, delta only (new facts this turn).
        # If this turn answers a clarifying question, build a combined context so
        # the extractor understands what the one-word answer refers to.
        if pending_user_input is not None:
            # Reconstruct the clarifying exchange for the extractor
            clarifying_question = chat_history[-2].replace("Assistant: ", "")
            extraction_context = (
                f"User originally said: \"{pending_user_input}\"\n"
                f"Assistant asked: \"{clarifying_question}\"\n"
                f"User confirmed: \"{user_input}\""
            )
            pending_user_input = None
        else:
            extraction_context = user_input

        extract_data = extract_kb_updates(user_message=extraction_context)
        kb_updates = extract_data.get("kb_update", [])

        if kb_updates:
            # Show proposed changes and ask for confirmation
            print("\nProposed KB updates:")
            for u in kb_updates:
                if isinstance(u, dict):
                    print(f"  [{u.get('type', '?')}] {u.get('fact', '')}")
                    if u.get('pddl_removal'):
                        print(f"    -> PDDL: remove {u['pddl_removal']}")

            confirm = input("\nApply these changes? [y/n]: ").strip().lower()
            if confirm == 'y':
                # E. Programmatic regeneration detection (KB diff, not LLM-decided)
                kb_snapshot = kb.get_state_as_string()
                kb.update_kb_from_llm(kb_updates)
                regenerate_plan = (kb.get_state_as_string() != kb_snapshot)

                if regenerate_plan:
                    # F. Programmatic PDDL modification — no LLM needed
                    if apply_kb_to_pddl(kb, ORIGINAL_PROBLEM_FILE, CURRENT_PROBLEM_FILE):
                        print("\nRunning PDDL Solver on modified problem...")
                        new_raw_plan = run_pddl_solver(CURRENT_PROBLEM_FILE)

                        if new_raw_plan:
                            diff_plans(latest_raw_plan, new_raw_plan)
                            latest_raw_plan = new_raw_plan
                            print("New plan ready.")
                        else:
                            print("[Solver Error] Could not generate new plan. Keeping previous.")
                    else:
                        print("[PDDL Error] Modification failed. Keeping previous plan.")
                else:
                    print("[KB] No effective change detected — plan unchanged.")
            else:
                print("Changes discarded.")

        # G. Rolling history management
        chat_history = manage_history(chat_history)

    # Final output
    print("\n" + "=" * 80)
    print("FINAL PLAN FOR EXECUTION")
    print("=" * 80)
    print_plan(latest_raw_plan)
    print(f"\n[System] Final problem state: {CURRENT_PROBLEM_FILE}")
    print("[System] Run 'python3 app.py' to launch the web simulation.")


if __name__ == "__main__":
    main()
