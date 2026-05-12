"""
phase_configs.py

Concrete PhaseConfig instances for each of the three conversation phases.
Imported by main.py; opening_message_fn callbacks are defined here to avoid
cluttering main.py with LLM call logic.
"""

from phase_config import PhaseConfig
from plan_utils import _compute_plan_rows
from llm_engine_groq import (
    get_replanning_opening,
    get_posttask_opening,
    extract_session_note,
)
import memory_store


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _plan_steps_str(ctx) -> str:
    rows = _compute_plan_rows(ctx.current_plan or "")
    if not rows:
        return "(no plan)"
    return "\n".join(f"  {i}. {r['actor']}: {r['description']}"
                     for i, r in enumerate(rows, 1))


# ---------------------------------------------------------------------------
# PRE_TASK — collaborative pre-planning conversation
# ---------------------------------------------------------------------------

_PRE_TASK_SYSTEM_PROMPT = """You are Stretch, a friendly collaborative robot assistant.
You are helping a human plan an ingredient swap task: bread and ham need to move to the kitchen,
cheese and lettuce need to move to the pantry. Two agents will divide the steps: p1 (human) and p2 (robot).
Your job is to have a natural, helpful conversation about preferences and constraints.
- Acknowledge what the user tells you.
- If their message is ambiguous, ask ONE clarifying question about the specific constraint.
- Do NOT describe or modify the plan yourself — that happens automatically.
- Do NOT use jargon or mention PDDL.

Current Knowledge Base:
{kb_string}

Current Plan:
{plan_data}

Return ONLY this JSON:
{{
  "reply": "your response here",
  "is_clarifying_question": false
}}"""

PRE_TASK_CONFIG = PhaseConfig(
    phase_name="PRE_TASK",
    system_prompt_override=None,  # uses default get_conversation_reply prompt
    finish_commands=("/finish", "/done"),
    enable_kb_updates=True,
    offer_execution=True,
    input_prompt="You (or '/finish' to start execution): ",
    show_plan_diff=True,
    enable_proposals=False,
    opening_message_fn=None,  # intro is handled by main.py
)


# ---------------------------------------------------------------------------
# REPLAN — during-task recovery conversation
# ---------------------------------------------------------------------------

def _replan_opening(ctx) -> str:
    incident = ctx.current_incident or {}
    rows = _compute_plan_rows(ctx.current_plan or "")
    remaining = "\n".join(
        f"  {i}. {r['actor']}: {r['description']}"
        for i, r in enumerate(rows, 1)
    )
    stopped_by_user = incident.get("stopped_by") == "user"
    return get_replanning_opening(
        world_state_summary=ctx.world_state.summary(),
        failure_reason=incident.get("failure_reason", "an unexpected issue"),
        steps_completed=incident.get("steps_completed", 0),
        plan_steps_str=remaining,
        stopped_by_user=stopped_by_user,
    )


_REPLAN_SYSTEM_PROMPT = """You are Stretch, a collaborative robot assistant. Execution of the shared task
was just interrupted. The plan has already been updated to reflect the current world state.
Your role in this conversation is to:
- Understand why the stop happened (ask if the human stopped it, explain if it was a robot failure).
- Discuss whether any new constraints or preferences should change the remaining plan.
- Let the human know the plan updates automatically as they talk — they just need to share their thoughts.
- When the human is ready to continue, they type /continue.

Do NOT describe or modify the plan yourself — replanning happens automatically when constraints change.
Do NOT use PDDL jargon. Keep the tone warm, collaborative, and focused on recovery.

Current Knowledge Base:
{kb_string}

Current Plan (already updated from world state):
{plan_data}

Return ONLY this JSON:
{{
  "reply": "your response here",
  "is_clarifying_question": false
}}
Set "is_clarifying_question" to true ONLY if your reply ends with a question that requires
the human to answer before a constraint can be fully understood."""

REPLAN_CONFIG = PhaseConfig(
    phase_name="REPLAN",
    system_prompt_override=_REPLAN_SYSTEM_PROMPT,
    finish_commands=("/continue", "/resume", "/finish"),
    enable_kb_updates=True,
    offer_execution=True,
    input_prompt="You (or '/continue' to resume execution): ",
    show_plan_diff=True,
    enable_proposals=False,
    opening_message_fn=_replan_opening,
)


# ---------------------------------------------------------------------------
# POST_TASK — post-completion deliberation
# ---------------------------------------------------------------------------

def _posttask_opening(ctx) -> str:
    return get_posttask_opening(
        world_state_summary=ctx.world_state.summary(),
        incident_log=ctx.incident_log,
        session_notes=ctx.session_notes,
    )


def _posttask_end(ctx) -> None:
    """Save any useful note from the post-task conversation to persistent memory."""
    note = extract_session_note([], ctx.kb.get_state_as_string())
    if note:
        ctx.session_notes.append(note)
        memory_store.save_session_notes(ctx.session_notes)
        print(f"\n  (Remembered for next time: {note})")


POST_TASK_CONFIG = PhaseConfig(
    phase_name="POST_TASK",
    system_prompt_override=None,
    finish_commands=("/finish", "/done", "/exit"),
    enable_kb_updates=True,    # allow late preference updates
    offer_execution=False,
    input_prompt="You (or '/finish' to exit): ",
    show_plan_diff=False,
    enable_proposals=True,
    opening_message_fn=_posttask_opening,
    on_phase_end=_posttask_end,
)
