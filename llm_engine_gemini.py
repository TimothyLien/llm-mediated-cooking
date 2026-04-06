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

# --- Model configurations ---
# Conversation: higher temperature for natural replies
_config_chat = {
    "temperature": 0.7,
    "max_output_tokens": 2048,
    "response_mime_type": "application/json",
}
# Extraction: near-deterministic for structured slot-filling
_config_extract = {
    "temperature": 0.1,
    "max_output_tokens": 1024,
    "response_mime_type": "application/json",
}
# Summarization: moderate temperature for fluent compression
_config_summarize = {
    "temperature": 0.3,
    "max_output_tokens": 512,
}

try:
    _chat_model = genai.GenerativeModel(MODEL_NAME, generation_config=_config_chat)
    _extract_model = genai.GenerativeModel(MODEL_NAME, generation_config=_config_extract)
    _summary_model = genai.GenerativeModel(MODEL_NAME, generation_config=_config_summarize)
except Exception as e:
    print(f"CRITICAL ERROR: Could not initialize Gemini models: {e}")
    sys.exit(1)


# Explicit predicate guide — avoids sending the full domain PDDL in chat calls.
# Update this if the domain gains new constraint predicates.
CONSTRAINT_PREDICATE_GUIDE = """
Available PDDL constraint predicates (removing one enforces the corresponding limitation):
  Room entry:   (can-enter p1 kitchen)  (can-enter p1 pantry)
                (can-enter p2 kitchen)  (can-enter p2 pantry)
  Item pickup:  (can-take p1 bread)  (can-take p1 cheese)  (can-take p1 ham)  (can-take p1 lettuce)
                (can-take p2 bread)  (can-take p2 cheese)  (can-take p2 ham)  (can-take p2 lettuce)
  Slicing:      (can-slice p1)  (can-slice p2)
  Washing:      (can-wash p1)   (can-wash p2)
  Assembly:     (can-assemble p1)  (can-assemble p2)
Note: p1 = human, p2 = robot.
"""


def _parse_json(raw: str) -> dict:
    """Strip markdown fences and parse JSON."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"```(json|pddl)?\n?", "", raw).rstrip("`").strip()
    return json.loads(raw)


# ---------------------------------------------------------------------------
# 1. Conversation reply  (lean prompt, no domain PDDL)
# ---------------------------------------------------------------------------

def get_conversation_reply(chat_history: list, kb_string: str, plan_data: str) -> dict:
    """
    Generates a natural language reply to the user.
    Does NOT perform KB extraction — that is handled separately by extract_kb_updates().

    Args:
        chat_history: list of "You: ..." / "Assistant: ..." strings
        kb_string:    JSON string of current KB state
        plan_data:    raw plan string (latest sas_plan content)

    Returns:
        {"reply": "..."}
    """
    print("[LLM-Chat] Calling Gemini for conversation reply...")

    system_prompt = f"""You are a collaborative robot assistant helping plan a sandwich-making task.
Two agents are involved: p1 (human) and p2 (robot), working across a kitchen and a pantry.
Your only job here is to have a natural, helpful conversation.
- Acknowledge what the user tells you.
- If their message is ambiguous or incomplete, ask ONE clarifying question
  directly about the constraint they just stated. Never ask about unrelated topics.
- Do NOT attempt to describe or modify the plan yourself.

Current Knowledge Base:
{kb_string}

Current Plan:
{plan_data}

Return ONLY this JSON:
{{
  "reply": "your response here",
  "is_clarifying_question": false
}}
Set "is_clarifying_question" to true ONLY if your reply ends with a question that
requires the user to answer before the constraint can be fully understood."""

    history_for_api = [
        {
            "role": "user" if msg.startswith("You:") else "model",
            "parts": [msg.replace("You: ", "").replace("Assistant: ", "")]
        }
        for msg in chat_history[:-1]
    ]

    try:
        convo = _chat_model.start_chat(history=history_for_api)
        last_msg = chat_history[-1].replace("You: ", "")
        response = convo.send_message(system_prompt + f"\n\nUser: {last_msg}")
        return _parse_json(response.text)
    except Exception as e:
        print(f"[LLM-Chat ERROR] {e}")
        return {"reply": "Sorry, I had trouble with that. Could you rephrase?",
                "is_clarifying_question": False}


# ---------------------------------------------------------------------------
# 2. KB extraction  (low-temp, structured slot-filling)
# ---------------------------------------------------------------------------

def extract_kb_updates(user_message: str) -> dict:
    """
    Extracts ONLY NEW facts stated in the current user message (delta extraction).
    The caller is responsible for accumulating facts in the KB across turns.

    Args:
        user_message: the raw user input string

    Returns:
        {"kb_update": [{"type": "...", "fact": "...", "pddl_removal": "..."|null}, ...]}
        Returns {"kb_update": []} if the message contains no new constraints.
    """
    print("[LLM-Extract] Calling Gemini for KB extraction...")

    prompt = f"""You are a constraint extraction system for an AI task planner.
Your ONLY job is to extract facts that are NEW and EXPLICITLY stated in the USER MESSAGE below.

{CONSTRAINT_PREDICATE_GUIDE}

KB categories — pick exactly one per entry:
  "human_preference"      — a task the human wants to do themselves
                            (e.g. "I want to slice", "I prefer to chop")
  "human_limitations"     — a physical inability or allergy of the human
                            (e.g. "I can't lift heavy things", "I'm allergic to bread")
  "robot_limitations"     — an inability of the robot
                            (e.g. "robot arm is broken", "robot can't slice")
  "environmental_factors" — a change in the environment
                            (e.g. "there's a spill near the sink")

STRICT RULES:
1. Extract ONLY what is DIRECTLY stated. Never infer the opposite constraint
   (e.g. "human wants to wash" does NOT imply "robot cannot wash" — do not add that).
2. Use EXACTLY ONE category per fact. A preference is NOT a limitation.
3. If the message contains no new constraints or preferences, return an empty list.

EXAMPLES OF CORRECT EXTRACTION:
  Message: "Robot can't slice"
  Output: [{{"type": "robot_limitations", "fact": "Robot cannot slice.", "pddl_removal": "(can-slice p2)"}}]

  Message: "I want to do the washing"
  Output: [{{"type": "human_preference", "fact": "Human prefers to wash the lettuce.", "pddl_removal": null}}]

  Message: "I don't want to move to the pantry"
  Output: [{{"type": "human_preference", "fact": "Human prefers not to enter the pantry.", "pddl_removal": "(can-enter p1 pantry)"}}]

  Message: "There's a spill near the sink"
  Output: [{{"type": "environmental_factors", "fact": "There is a spill near the sink.", "pddl_removal": null}}]

  Message: "Sounds good to me"
  Output: []

Return ONLY this JSON (no extra text):
{{"kb_update": [...]}}

USER MESSAGE: {user_message}"""

    try:
        response = _extract_model.generate_content(prompt)
        return _parse_json(response.text)
    except Exception as e:
        print(f"[LLM-Extract ERROR] {e}")
        return {"kb_update": []}


# ---------------------------------------------------------------------------
# 3. Chat history summarization  (compression for rolling window)
# ---------------------------------------------------------------------------

def summarize_chat_history(chat_history: list) -> str:
    """
    Compresses a list of old chat messages into a concise summary string.
    Called by main.py when history exceeds the rolling window threshold.

    Args:
        chat_history: list of "You: ..." / "Assistant: ..." strings to summarize

    Returns:
        A short plain-text summary string.
    """
    print("[LLM-Summarize] Compressing old chat history...")
    formatted = "\n".join(chat_history)
    prompt = f"""Summarize the following conversation between a human and a robot planning assistant.
Capture: constraints expressed, preferences stated, and key decisions made.
Be concise — 3 to 5 sentences maximum. Do not use bullet points.

CONVERSATION:
{formatted}

SUMMARY:"""

    try:
        response = _summary_model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"[LLM-Summarize ERROR] {e}")
        return "[Summary unavailable due to API error]"
