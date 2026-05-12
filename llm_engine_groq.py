from __future__ import annotations
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
MODEL_CHAT      = "openai/gpt-oss-120b"    # strongest available — best for natural conversation quality
MODEL_EXTRACT   = "qwen/qwen3-32b"         # excellent structured JSON output and constraint reasoning
MODEL_SUMMARIZE = "llama-3.1-8b-instant"   # simplest task — fastest model keeps history compression invisible

# Explicit predicate guide — avoids sending the full domain PDDL in chat calls.
# Update this if the domain gains new constraint predicates.
CONSTRAINT_PREDICATE_GUIDE = """
Available PDDL constraint predicates (removing one enforces the corresponding limitation):
  Room entry:   (can-enter p1 kitchen)  (can-enter p1 pantry)
                (can-enter p2 kitchen)  (can-enter p2 pantry)
  Item pickup:  (can-take p1 bread)  (can-take p1 ham)  (can-take p1 cheese)  (can-take p1 lettuce)
                (can-take p2 bread)  (can-take p2 ham)  (can-take p2 cheese)  (can-take p2 lettuce)
Note: p1 = human, p2 = robot.
"""


def _parse_json(raw: str) -> dict:
    """Strip markdown fences and parse JSON."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"```(json|pddl)?\n?", "", raw).rstrip("`").strip()
    return json.loads(raw)


# ---------------------------------------------------------------------------
# 0. Opening introduction  (called once at startup)
# ---------------------------------------------------------------------------

def get_introduction(plan_steps: str) -> str:
    """
    Generates a warm, first-person introduction spoken by the robot.
    Called once at startup, before the conversation loop begins.

    Args:
        plan_steps: a plain-English numbered list of the initial plan steps

    Returns:
        A natural-language introduction string.
    """
    prompt = f"""You are a friendly collaborative robot assistant named Stretch.
You are about to work alongside a human on a fetch-and-swap task in a kitchen setting.
There are four ingredients: bread and ham currently in the pantry, and cheese and lettuce
currently in the kitchen. The goal is to swap them — bread and ham should end up in the
kitchen, and cheese and lettuce should end up in the pantry.
You and the human will divide the steps between yourselves.

Based on the initial plan below, introduce yourself to the human in 3–4 sentences.
Your introduction should:
  1. Briefly say who you are and what you're here to help with.
  2. Describe the goal of the task in plain, everyday language (no jargon).
  3. Mention that you've already put together an initial plan and invite them to review it.
  4. Let them know they can ask you to change who does what, or flag any limitations.

Keep the tone warm, clear, and conversational — like a helpful colleague, not a manual.
Do NOT use bullet points. Do NOT mention PDDL, predicates, or any technical terms.
Write in first person as Stretch.

Initial plan:
{plan_steps}

Reply with only the introduction text, nothing else."""

    try:
        response = client.chat.completions.create(
            model=MODEL_CHAT,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        # Fallback if the API call fails
        return (
            "Hi! I'm Stretch, your robot assistant. Today we're going to work together "
            "to swap some ingredients between the kitchen and the pantry — bread and ham "
            "need to come into the kitchen, while cheese and lettuce need to go to the pantry. "
            "I've put together an initial plan for us — take a look and let me know "
            "if you'd like to adjust who handles which steps."
        )


# ---------------------------------------------------------------------------
# 1. Conversation reply  (lean prompt, no domain PDDL)
# ---------------------------------------------------------------------------

def get_conversation_reply(chat_history: list, kb_string: str, plan_data: str,
                           system_prompt_override: str | None = None) -> dict:
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

    if system_prompt_override:
        system_prompt = system_prompt_override
    else:
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


# ---------------------------------------------------------------------------
# 4. Phase opening messages
# ---------------------------------------------------------------------------

def get_replanning_opening(world_state_summary: str, failure_reason: str,
                           steps_completed: int, plan_steps_str: str,
                           stopped_by_user: bool = False) -> str:
    """Opening message for the REPLAN phase after an execution stop or failure."""
    if stopped_by_user:
        prompt = f"""You are Stretch, a collaborative robot assistant. The human just manually stopped the task.

Current world state: {world_state_summary}
Steps completed before stop: {steps_completed}

Updated remaining plan steps (replanned from current position):
{plan_steps_str}

In 2-3 sentences, acknowledge the human stopped, confirm you've updated the plan to reflect
how far we got, and warmly ask them what made them want to stop and how they'd like to proceed.
Be non-judgmental and curious. Do NOT use bullet points. Write in first person as Stretch."""
    else:
        prompt = f"""You are Stretch, a collaborative robot assistant. An issue occurred during task execution.

Current world state: {world_state_summary}
What went wrong: {failure_reason}
Steps completed before failure: {steps_completed}

Updated remaining plan steps (replanned from current position):
{plan_steps_str}

In 2-3 sentences, explain what went wrong in plain language, confirm you've updated the plan
to continue from where things stand now, and invite the human to discuss how to proceed.
Keep it warm and non-technical. Do NOT use bullet points. Write in first person as Stretch."""

    try:
        response = client.chat.completions.create(
            model=MODEL_CHAT,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        if stopped_by_user:
            return ("I noticed you stopped the task. I've updated the plan to reflect our progress — "
                    "what made you want to stop, and how would you like to proceed?")
        return (f"I had to stop — {failure_reason}. I've updated the plan from our current position. "
                "Let's figure out how to continue from here.")


def get_posttask_opening(world_state_summary: str, incident_log: list[dict],
                         session_notes: list[str]) -> str:
    """Opening message for the POST_TASK phase after task completion."""
    incidents_text = ""
    if incident_log:
        recent = incident_log[-3:]
        incidents_text = "\n".join(
            f"- {i.get('trigger', 'unknown')}: {i.get('resolution', 'no resolution')}"
            for i in recent
        )

    notes_text = "\n".join(f"- {n}" for n in session_notes[-5:]) if session_notes else "(none)"

    prompt = f"""You are Stretch, a collaborative robot assistant. The task has just been completed.

Current world state: {world_state_summary}
Recent incidents: {incidents_text or "(none)"}
Past session notes: {notes_text}

In 2-3 sentences, congratulate the human on completing the task, briefly mention anything
noteworthy that happened (failures, replans), and invite them to reflect on how it went.
Keep it warm and conversational. Do NOT use bullet points. Write in first person as Stretch."""

    try:
        response = client.chat.completions.create(
            model=MODEL_CHAT,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return ("Great work — the task is complete! Let's take a moment to reflect on "
                "how it went and if there's anything we should remember for next time.")


# ---------------------------------------------------------------------------
# 5. Proactive proposals  (POST_TASK phase)
# ---------------------------------------------------------------------------

def get_proactive_proposals(chat_history: list, kb_string: str,
                            incident_log: list[dict]) -> str:
    """
    After the human has spoken in POST_TASK, suggest 1-2 concrete improvements
    the robot noticed based on what happened. Returns plain text (not JSON).
    """
    recent_chat = "\n".join(chat_history[-6:])
    recent_incidents = json.dumps(incident_log[-3:], indent=2) if incident_log else "[]"

    prompt = f"""You are Stretch, a collaborative robot. The task is done and you're debriefing.

Recent conversation:
{recent_chat}

Recent incidents:
{recent_incidents}

Current preferences/constraints:
{kb_string}

Based on what happened, suggest 1-2 specific, actionable improvements for next time.
Examples: "Next time I could carry the bread and ham together to save a trip."
Keep each suggestion to one sentence. Be concrete, not vague. Do NOT say "great job" again."""

    try:
        response = client.chat.completions.create(
            model=MODEL_CHAT,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.6,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return "I think we worked well together. Let me know if there's anything to improve."


# ---------------------------------------------------------------------------
# 6. State update parsing  (REPLAN phase — observer describes what changed)
# ---------------------------------------------------------------------------

def parse_state_update(user_message: str, current_world_summary: str) -> dict:
    """
    Parse a free-text observer description into structured world state changes.

    Returns:
        {
          "moves": [{"agent": "p2", "room": "pantry"}, ...],
          "takes": [{"agent": "p2", "item": "cheese"}, ...],
          "drops": [{"agent": "p2", "item": "cheese", "room": "kitchen"}, ...]
        }
    """
    prompt = f"""Extract world state changes from this observer message.

Current world state: {current_world_summary}
Observer message: "{user_message}"

Extract any of the following events:
- An agent (p1=human, p2=robot) moving to a room (kitchen or pantry)
- An agent picking up an item (bread, ham, cheese, lettuce)
- An agent putting down an item in a room

Return ONLY this JSON:
{{
  "moves": [{{"agent": "p1|p2", "room": "kitchen|pantry"}}],
  "takes": [{{"agent": "p1|p2", "item": "bread|ham|cheese|lettuce"}}],
  "drops": [{{"agent": "p1|p2", "item": "bread|ham|cheese|lettuce", "room": "kitchen|pantry"}}]
}}
Use empty lists if nothing of that type was described."""

    try:
        response = client.chat.completions.create(
            model=MODEL_EXTRACT,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.01,
            response_format={"type": "json_object"},
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        print(f"[LLM-StateUpdate ERROR] {e}")
        return {"moves": [], "takes": [], "drops": []}


# ---------------------------------------------------------------------------
# 7. Post-task session note extraction
# ---------------------------------------------------------------------------

def extract_session_note(chat_history: list, kb_string: str) -> str | None:
    """
    After a POST_TASK conversation, distil one concise note worth remembering
    for future sessions. Returns None if there is nothing worth saving.
    """
    formatted = "\n".join(chat_history[-12:])
    prompt = f"""After a post-task debrief, extract ONE short note worth remembering for next time.
Focus on: new preferences discovered, recurring failures, or useful strategies.

Conversation:
{formatted}

Current KB:
{kb_string}

If there is something worth remembering, return it as a single sentence (max 25 words).
If there is nothing new to record, return null.

Return ONLY this JSON: {{"note": "..." | null}}"""

    try:
        response = client.chat.completions.create(
            model=MODEL_EXTRACT,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        result = json.loads(response.choices[0].message.content)
        return result.get("note")
    except Exception as e:
        print(f"[LLM-Note ERROR] {e}")
        return None
