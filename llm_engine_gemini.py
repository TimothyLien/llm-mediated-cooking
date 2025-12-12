import os
import json
import re
import google.generativeai as genai
from dotenv import load_dotenv
import sys


load_dotenv()

try:
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
except KeyError:
    print("=" * 50)
    print("ERROR: GEMINI_API_KEY not found. Please set it in your .env file.")
    print("=" * 50)
    sys.exit(1)
except Exception as e:
    print("=" * 50)
    print(f"ERROR: Gemini client initialization failed: {e}")
    print("=" * 50)
    sys.exit(1)

MODEL_NAME = "gemini-2.5-flash" 

generation_config_chat = {
    "temperature": 0.7,
    "top_p": 1,
    "top_k": 1,
    "max_output_tokens": 4096,
    "response_mime_type": "application/json",
}
generation_pddl_modify = {
    "temperature": 0.2,
    "top_p": 1,
    "top_k": 1,
    "max_output_tokens": 8192,
    "response_mime_type": "application/json",
}

chat_model = None
plan_model = None

try:
    chat_model = genai.GenerativeModel(
        model_name=MODEL_NAME,
        generation_config=generation_config_chat
    )
    plan_model = genai.GenerativeModel(
        model_name=MODEL_NAME,
        generation_config=generation_pddl_modify
    )
except Exception as e:
    print(f"CRITICAL ERROR: Could not initialize model '{MODEL_NAME}'.")
    print(f"Details: {e}")
    print("This may be an invalid API key or model name. Exiting.")
    sys.exit(1)


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
    print("[LLM-Modify] Calling LLM API (Gemini) for PDDL modification...")

    prompt = build_pddl_modification_prompt(kb_string, original_problem_pddl)
    
    try:
        response = plan_model.generate_content(prompt)
        modified = response.text.strip()
        if modified.startswith("```"):
            modified = re.sub(r"```(pddl|json)?\n", "", modified, count=1)
            modified = modified.rstrip("`").strip()

        if modified.startswith("(define"):
            return modified
        else:
            print("[LLM-Modify ERROR] Output does not begin with (define ...)")
            return None
    except Exception as e:
        print(f"[LLM-Modify ERROR] {e}")
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
    print("[LLM-Chat] Calling LLM API (Gemini)...")
    
    prompt = kb_update_prompt(kb_string, domain_pddl, problem_pddl)
    
    full_user_prompt = (
        f"\n\nCURRENT SHARED PLAN:\n{plan_data}" +
        f"\n\nUser: {chat_history[-1].replace('You: ', '')}"
    )

    try:
        convo = chat_model.start_chat(history=[
            {
                "role": "user" if msg.startswith("You:") else "model",
                "parts": [msg.replace("You: ", "").replace("Assistant: ", "")]
            }
            for msg in chat_history[:-1]
        ])

        # Send final combined prompt to Gemini
        response = convo.send_message(prompt + full_user_prompt)

        raw = response.text.strip()

        # ------ Clean markdown fences ------
        if raw.startswith("```"):
            raw = re.sub(r"```(json|pddl)?\n", "", raw, count=1)
            raw = raw.rstrip("`").strip()

        # ------ Parse JSON ------
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            print(f"[LLM-Chat ERROR] JSON decode failed. Raw output:\n{raw}")
            # Try a safer cleanup
            cleaned = raw.replace("```", "").strip()
            parsed = json.loads(cleaned)

        return parsed

    except Exception as e:
        print(f"[LLM-Chat ERROR] {e}")
        return {
            "reply": "I'm sorry, something went wrong connecting to my planning module.",
            "kb_update": [],
            "request_plan_regeneration": False
        }
    