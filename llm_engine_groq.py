import os
import json
import re
from groq import Groq
from dotenv import load_dotenv
import sys

load_dotenv()

try:
    client = Groq(api_key=os.environ["GROQ_API_KEY"])
except KeyError:
    print("=" * 50)
    print("ERROR: GROQ_API_KEY not found. Please set it in your .env file.")
    print("=" * 50)
    sys.exit(1)
except Exception as e:
    print("=" * 50)
    print(f"ERROR: Groq client initialization failed: {e}")
    print("=" * 50)
    sys.exit(1)

# Separate model names allow independent tuning per task
MODEL_CHAT = "llama-3.3-70b-versatile"
MODEL_EXTRACT = "llama-3.3-70b-versatile"
MODEL_SUMMARIZE = "llama-3.3-70b-versatile"

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
    print("[LLM-Chat] Calling Groq for conversation reply...")

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

    messages = [{"role": "system", "content": system_prompt}]
    for msg in chat_history[:-1]:
        role = "user" if msg.startswith("You:") else "assistant"
        content = msg.replace("You: ", "").replace("Assistant: ", "")
        if content.strip():
            messages.append({"role": role, "content": content})
    last_msg = chat_history[-1].replace("You: ", "")
    messages.append({"role": "user", "content": last_msg})

    try:
        response = client.chat.completions.create(
            model=MODEL_CHAT,
            messages=messages,
            temperature=0.7,
            response_format={"type": "json_object"}
        )
        return json.loads(response.choices[0].message.content)
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
    print("[LLM-Extract] Calling Groq for KB extraction...")

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
1. Extract ONLY what is DIRECTLY stated. Do NOT create extra entries beyond what the message says.
2. Use EXACTLY ONE category per fact. A preference is NOT a limitation.
3. If the message contains no new constraints or preferences, return an empty list.
4. PDDL removal for preferences:
   - If the human wants to DO a task themselves (wash, slice, assemble), set pddl_removal
     to remove the ROBOT's corresponding capability so the planner must assign it to the human.
   - If the human wants to AVOID something (don't want to enter a room, don't want to take an item),
     set pddl_removal to remove the HUMAN's corresponding capability.

EXAMPLES OF CORRECT EXTRACTION:
  Message: "Robot can't slice"
  Output: [{{"type": "robot_limitations", "fact": "Robot cannot slice.", "pddl_removal": "(can-slice p2)"}}]

  Message: "I want to wash the lettuce"
  Output: [{{"type": "human_preference", "fact": "Human prefers to wash the lettuce.", "pddl_removal": "(can-wash p2)"}}]

  Message: "I want to slice the ham"
  Output: [{{"type": "human_preference", "fact": "Human prefers to slice the ham.", "pddl_removal": "(can-slice p2)"}}]

  Message: "I want to assemble the sandwich"
  Output: [{{"type": "human_preference", "fact": "Human prefers to assemble the sandwich.", "pddl_removal": "(can-assemble p2)"}}]

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
        response = client.chat.completions.create(
            model=MODEL_EXTRACT,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.01,
            response_format={"type": "json_object"}
        )
        return json.loads(response.choices[0].message.content)
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
        response = client.chat.completions.create(
            model=MODEL_SUMMARIZE,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"[LLM-Summarize ERROR] {e}")
        return "[Summary unavailable due to API error]"
