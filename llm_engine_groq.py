import os
import json
import re
from groq import Groq
from dotenv import load_dotenv
import sys


load_dotenv()

try:
    # Initialize the Groq Client
    client = Groq(
        api_key=os.environ["GROQ_API_KEY"],
    )
except KeyError:
    print("="*50)
    print("ERROR: GROQ_API_KEY not found. Please set it in your .env file.")
    print("="*50)
    sys.exit(1)
except Exception as e:
    # Catch broader connection/initialization errors
    print("="*50)
    print(f"ERROR: Groq client initialization failed: {e}")
    print("="*50)
    sys.exit(1)

# Define models for specific tasks
MODEL_CHAT = "llama-3.3-70b-versatile"
MODEL_PDDL_MODIFY = "llama-3.3-70b-versatile" # Dedicated model for syntax-critical tasks


def build_pddl_modification_prompt(kb_string, original_problem_pddl):
    """
    Builds the specialized system prompt for the PDDL modification task,
    using concise few-shot examples for constraint enforcement via removal.
    """
    return f"""
    You are an expert PDDL (Planning Domain Definition Language) modification system.
    Your task is to integrate **ALL** constraints found in the Knowledge Base (KB)
    into the PDDL problem file by **editing the :init section**.

    You MUST output ONLY the complete, syntactically correct, MODIFIED PDDL problem file content.
    Do NOT include any explanations, Markdown formatting (e.g., ```pddl```), or extra text.

    --- Rules for Modification ---

    1. **Cumulative Changes:** The KB contains all current constraints. Ensure the modified PDDL file reflects ALL existing KB facts.
    2. **Translate Limitations to REMOVAL (CRITICAL):** To enforce a limitation, you must REMOVE the corresponding positive fact from the :init section.

    ***FEW-SHOT EXAMPLES (Illustrating PDDL Removal Logic):***
    
    // Example 1: Agent Movement Limitation
    // KB Constraint: "robot cannot enter pantry"
    // PDDL Removal: REMOVE (can-enter p2 pantry) from the :init section.

    // Example 2: Agent Ability Limitation
    // KB Constraint: "human cannot slice ham"
    // PDDL Removal: REMOVE (can-slice p1) from the :init section.
    
    // Example 3: Item Retrieval Limitation
    // KB Constraint: "human cannot take bread"
    // PDDL Removal: REMOVE (can-take p1 bread) from the :init section.

    Note: Every limitation pertaining to the human is a modifcation that must be made with p1, 
    and every limitation pertaining to the robot is a modification that must be made with p2.

    --- KNOWLEDGE BASE (ALL CURRENT CONSTRAINTS) ---
    {kb_string}

    --- ORIGINAL PDDL PROBLEM ---
    {original_problem_pddl}

    Your output MUST be ONLY the complete, modified PDDL problem file content.
    """

def modify_problem_pddl(kb_string: str, original_problem_pddl: str) -> str | None:
    """
    Uses the LLM to modify the PDDL problem file based on the Knowledge Base.
    """
    if not client:
        # Should be caught by initialization, but good failsafe
        print("[LLM Error] Groq client not available.")
        return None

    print("[LLM-Modify] Calling LLM API (Groq) for PDDL modification...")
    
    prompt = build_pddl_modification_prompt(kb_string, original_problem_pddl)
    
    try:
        response = client.chat.completions.create(
            model=MODEL_PDDL_MODIFY, # Use dedicated model
            messages=[
                {"role": "user", "content": prompt}
            ],
            temperature=0.01, # Use very low temperature for deterministic code generation
        )

        modified_pddl = response.choices[0].message.content.strip()
        
        # IMPROVEMENT: Use regex to aggressively clean markdown wrappers
        if modified_pddl.startswith("```"):
            modified_pddl = re.sub(r"```(pddl|json|)?\n", "", modified_pddl, count=1)
            modified_pddl = modified_pddl.rstrip("`").strip()
            
        # Basic sanity check
        if modified_pddl.startswith("(define"):
            return modified_pddl
        else:
            print("[LLM-Modify ERROR] Response did not start with PDDL definition.")
            return None

    except Exception as e:
        print(f"[LLM-Modify ERROR] Groq API call failed: {e}")
        return None

def kb_update_prompt(kb_string, domain_pddl, problem_pddl):
    """Builds the full system prompt for the chat LLM."""
    return f"""
    You are a collaborative robot assistant. Your goal is to chat with a human
    to refine a plan. Your tasks are:
    1. Listen and extract information to populate/edit a Knowledge Base (KB).
    2. Be a conversational partner.
    3. Decide if the *current plan* is now invalid due to the conversation.
    
    The KB has four categories:
    1. "human_preference": (e.g., "likes spicy food", "prefers to chop")
    2. "human_limitations": (e.g., "leg injury", "allergic to nuts")
    3. "robot_limitations": (e.g., "unable to move", "can't use knife")
    4. "environmental_factors": (e.g., "spill on floor", "oven is dirty")
    
    You MUST return a JSON object with THREE keys: "reply", "kb_update", and "request_plan_regeneration".
    
    1. "reply": Your natural language response to the user.
    2. "kb_update": A *complete list* of all KB facts.
    3. "request_plan_regeneration": A boolean (true/false). Set this to `True`
       if a new KB item or user request makes the current plan obsolete or
       sub-optimal. Additionally, if the user is simply requesting to add additional 
       steps to the plan, set this to 'True'. Otherwise, set it to `False`.

    EXAMPLE 1:
    User says: "Robot cannot enter the pantry."
    Your Output:
    {{
      "reply": "Understood, I have noted that the robot cannot enter the pantry.",
      "kb_update": [
        {{
          "type": "robot_limitations",
          "fact": "Robot cannot enter the pantry."
        }}
      ],
      "request_plan_regeneration": True
    }}
    
    [PDDL DOMAIN]
    {domain_pddl}
    
    [PDDL PROBLEM]
    {problem_pddl}

    CURRENT KNOWLEDGE BASE:
    {kb_string}
    """ # Note: Removed repetitive examples for conciseness

def get_collaborative_response(chat_history, kb_string, plan_data, domain_pddl, problem_pddl):
    """Makes a API call to the Chat LLM."""
    print("[LLM-Chat] Calling LLM API (Groq)...")
    
    prompt = kb_update_prompt(kb_string, domain_pddl, problem_pddl)
    
    # 1. Prepare the full prompt with context for the LLM
    full_user_prompt = (
        f"\n\nCURRENT SHARED PLAN:\n{plan_data}" +
        f"\n\nUser: {chat_history[-1].replace('You: ', '')}"
    )

    # 2. Build the message history list for the Groq API
    messages = [
        {"role": "system", "content": prompt}
    ]
    # Add previous chat history, skipping the last 'You:' message
    for msg in chat_history[:-1]:
        role = "user" if msg.startswith("You:") else "assistant"
        content = msg.replace("You: ", "").replace("Assistant: ", "")
        if content.strip():
             messages.append({"role": role, "content": content})

    # Add the final, combined user message
    messages.append({"role": "user", "content": full_user_prompt})


    try:
        response = client.chat.completions.create(
            model=MODEL_CHAT,
            messages=messages,
            temperature=0.7,
            response_format={"type": "json_object"}
        )
        
        response_text = response.choices[0].message.content
        return json.loads(response_text)

    except json.JSONDecodeError:
        print(f"[LLM-Chat ERROR] Could not decode JSON: {response_text}")
        
        # IMPROVEMENT: More robust JSON cleanup logic
        clean_text = response_text.strip()
        # Regex to aggressively remove code fences (e.g., ```json\n...\n```)
        clean_text = re.sub(r"```(json|)?\s*\n|\s*\n```", "", clean_text, flags=re.IGNORECASE).strip()
        
        try:
            return json.loads(clean_text)
        except json.JSONDecodeError:
            print("[LLM-Chat ERROR] Failed to clean and parse JSON even after cleanup.")
            # Fallback to returning an error structure
            return {
                "reply": "I'm sorry, I received a corrupted response. What was that again?", 
                "kb_update": [],
                "request_plan_regeneration": False
            }
            
    except Exception as e:
        print(f"[LLM-Chat ERROR] API call failed: {e}")
        return {
            "reply": "I'm sorry, I'm having trouble connecting to my brain.", 
            "kb_update": [],
            "request_plan_regeneration": False
        }