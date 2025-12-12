import textwrap
import sys
import json
import subprocess
from pathlib import Path
import shutil # Added for file copy
from knowledge_base import KnowledgeBase
from llm_engine_gemini import modify_problem_pddl, get_collaborative_response

DOMAIN_FILE = Path("pddl/domain.pddl")
ORIGINAL_PROBLEM_FILE = Path("pddl/problem.pddl")
CURRENT_PROBLEM_FILE = Path("current_problem.pddl") 

CURRENT_PROBLEM_FILE.parent.mkdir(parents=True, exist_ok=True) 

def run_pddl_solver(problem_path=CURRENT_PROBLEM_FILE):
    solver_dir = Path("downward")
    solver_script = "./fast-downward.py"
    domain_pddl = DOMAIN_FILE
    
    problem_relative_path = Path("..") / problem_path
    domain_relative_path = Path("..") / domain_pddl
    
    command = [
        str(solver_script),
        str(domain_relative_path),
        str(problem_relative_path),
        "--search",
        "astar(lmcut())"
    ]
    
    try:
        result = subprocess.run(
            command,
            cwd=solver_dir,
            capture_output=True,
            text=True,
            check=True
        )
        
        plan_path = solver_dir / "sas_plan"
        if plan_path.exists():
            with open(plan_path, 'r') as f:
                sas_plan_content = f.read()
            return sas_plan_content
        else:
            print("[Solver ERROR] Solver ran successfully, but 'sas_plan' file was not created.")
            print("Solver Output:\n", result.stdout)
            return None

    except subprocess.CalledProcessError as e:
        print(f"[Solver ERROR] Fast Downward failed with exit code {e.returncode}.")
        print("STDOUT:", e.stdout)
        print("STDERR:", e.stderr)
        return None
    except FileNotFoundError:
        print("[Solver ERROR] Could not find the Fast Downward executable.")
        return None

def print_plan(raw_plan_string):
    plan_data = {"plan": []}
    if not raw_plan_string:
        print("  (No plan steps yet)")
        return plan_data

    actions = [line.strip() for line in raw_plan_string.split('\n') 
               if line.strip() and not line.strip().startswith(';')]

    for i, action in enumerate(actions):
        
        assigned_to = "N/A"
        
        # --- FIXED LOGIC: Determine assignment by checking for agent marker in the string ---
        # The agent marker (p1 or p2) is not consistently the last word, so checking its presence
        # is the most robust way to assign the task.
        if " p1" in action:
            assigned_to = "Human"
        elif " p2" in action:
            assigned_to = "Robot"
        # --- END FIXED LOGIC ---
            
        task_str = action.strip('()')
        
        plan_data["plan"].append({
            "step": i + 1,
            "task": task_str,
            "assigned_to": assigned_to
        })

    print("  " + "-"*60)
    print(f"  {'Step':<5} | {'Assigned To':<11} | {'Task':<35}") 
    print("  " + "-"*60)
    
    for item in plan_data.get("plan", []):
        step = item.get('step', '?')
        task = item.get('task', 'No task defined')
        assigned = item.get('assigned_to', 'N/A')
    
        task_str = str(task)
        
        print(f"  {step:<5} | {assigned:<11} | {task_str:<35}")
    print("  " + "-"*60)
    
    return plan_data


def print_ui(kb, plan):
    """
    Simulates the Dual-Panel UI in the terminal.
    """
    print("\n" + "=" * 80)
    
    print("PANEL 1: SHARED PLAN")
    print("-" * 38)
    print_plan(plan)
            
    print("\n" + "PANEL 2: KNOWLEDGE BASE")
    print("-" * 38)
    
    kb_str = kb.get_state_as_string()
    print(textwrap.indent(kb_str, "  "))
    
    print("=" * 80 + "\n")

def load_domain_pddl():
    try:
        with open(DOMAIN_FILE, 'r') as f:
            domain_pddl_content = f.read()
        return domain_pddl_content
    except FileNotFoundError:
        print(f"[ERROR] Domain file not found: {DOMAIN_FILE}")
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] Could not read domain file: {e}")
        sys.exit(1)

def load_problem_pddl():
    try:
        with open(ORIGINAL_PROBLEM_FILE, 'r') as f:
            problem_pddl_content = f.read()
        return problem_pddl_content
    except FileNotFoundError:
        print(f"[ERROR] Problem file not found: {ORIGINAL_PROBLEM_FILE}")
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] Could not read problem file: {e}")
        sys.exit(1)

def llm_update_problem_pddl(kb, original_problem_pddl_content, target_file_path=CURRENT_PROBLEM_FILE):
    """
    Calls the LLM to modify the PDDL problem file based on the Knowledge Base.
    Saves the new PDDL content to target_file_path.
    Returns True on success, False otherwise.
    """
    print("🔄 LLM modifying problem.pddl based on Knowledge Base...")
    
    new_pddl_content = modify_problem_pddl(
        kb_string=kb.get_state_as_string(),
        original_problem_pddl=original_problem_pddl_content
    )
    
    if not new_pddl_content:
        print("[LLM Error] LLM failed to generate a new PDDL file.")
        return False

    try:
        with open(target_file_path, "w") as f:
            f.write(new_pddl_content)
        print(f"New PDDL problem file saved to: {target_file_path}")
        return True
    except Exception as e:
        print(f"[System Error] Could not write new PDDL file: {e}")
        return False


def main():
    kb = KnowledgeBase()
    chat_history = []
    latest_raw_plan = None
    
    print("--- Collaborative HRI Prototype ---")

    domain_pddl = load_domain_pddl()
    problem_pddl = load_problem_pddl()
    
    try:
        shutil.copy(ORIGINAL_PROBLEM_FILE, CURRENT_PROBLEM_FILE)
        print(f"Initial problem copied to {CURRENT_PROBLEM_FILE}")
    except Exception as e:
        print(f"[ERROR] Could not copy initial problem file: {e}")
        sys.exit(1)

    print("\n Running PDDL Solver for initial plan...")
    latest_raw_plan = run_pddl_solver(CURRENT_PROBLEM_FILE)
    
    if not latest_raw_plan:
        print("[ERROR] Initial plan generation failed. Exiting.")
        sys.exit(1)

    print("\n" + "="*80)
    print("INITIAL PLAN GENERATED")
    print("="*80)
    print("Assistant: I've generated an initial plan based on the PDDL files.")
    print("Let's review it. You can enter your preferences or constraints to update the plan.")
    
    # --- 2. ITERATIVE PLANNING LOOP ---

    while True:
        # A. Display the current state (KB and latest plan)
        print_ui(kb, latest_raw_plan)

        # B. Get Human input/updates
        user_input = input("You (Enter preferences or type '/finish'): ").strip()
        
        if user_input.lower() == "/quit":
            print("Exiting prototype. Goodbye!")
            break

        if user_input.lower() == "/finish":
            print("\n👋 User requested to finish planning.")
            print("The latest generated plan will be used for the next part.")
            break

        # C. Process input to update KB
        print("🧠 LLM analyzing human input to update KB...")
        
        chat_history.append(f"You: {user_input}")

        # Use the existing function to process the user input and get regeneration request
        response_data = get_collaborative_response(
            chat_history=chat_history,
            kb_string=kb.get_state_as_string(),
            plan_data=latest_raw_plan, 
            domain_pddl=domain_pddl,
            problem_pddl=problem_pddl
        )

        reply = response_data.get("reply", "I'm not sure what to say.")
        kb_updates = response_data.get("kb_update", [])
        regenerate_plan = response_data.get("request_plan_regeneration", False)
        
        print(f"\nAssistant: {reply}")
        chat_history.append(f"Assistant: {reply}") # Store assistant reply for context

        if kb_updates:
            kb.update_kb_from_llm(kb_updates)
            print("Knowledge Base updated based on user input.")

        # D. Regenerate plan if required by the LLM
        if regenerate_plan:
            # 1. LLM updates the problem file copy based on the new KB
            if llm_update_problem_pddl(kb, problem_pddl, CURRENT_PROBLEM_FILE):
                # 2. Run solver on the new problem file to get the new plan
                print("\n🚀 Running PDDL Solver on modified problem to get new plan...")
                new_raw_plan = run_pddl_solver(CURRENT_PROBLEM_FILE)
                
                if new_raw_plan:
                    latest_raw_plan = new_raw_plan
                    print("New Plan Generated and ready for review.")
                else:
                    print("[Solver Error] PDDL solver failed to generate a new plan.")
                    print("Continuing with the previous plan for review.")
            else:
                print("[System Error] PDDL modification failed. Continuing with previous plan.")

    # --- 3. FINAL OUTPUT ---
    
    print("\n" + "="*80)
    print("FINAL PLAN FOR EXECUTION")
    print("="*80)
    print_plan(latest_raw_plan)
    print(f"\n[System] Final problem state saved in: {CURRENT_PROBLEM_FILE}")
    print("[System] The program now moves to the next part using this plan and problem file.")


if __name__ == "__main__":
    main()