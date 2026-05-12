"""
conversation_engine.py

Single unified conversation loop parameterized by PhaseConfig.
Used for all three phases: PRE_TASK, REPLAN, POST_TASK.
"""

import sys
from pathlib import Path

import dashboard
from plan_utils import print_plan, diff_plans, KB_CATEGORY_LABELS, _compute_plan_actions
from pddl_utils import apply_kb_to_pddl, run_pddl_solver
from llm_engine_groq import (
    get_conversation_reply,
    extract_kb_updates,
    summarize_chat_history,
    get_proactive_proposals,
)

MAX_HISTORY_TURNS   = 10
SUMMARIZE_THRESHOLD = 18

_DEBUG = "--debug" in sys.argv


def _compute_plan_rows(raw_plan_string: str) -> list[dict]:
    from plan_utils import _compute_plan_rows as _cpr
    return _cpr(raw_plan_string)


def _manage_history(chat_history: list) -> list:
    if len(chat_history) <= SUMMARIZE_THRESHOLD:
        return chat_history
    if _DEBUG:
        print("[History] Compressing old conversation history...")
    summary = summarize_chat_history(chat_history[:-MAX_HISTORY_TURNS])
    return [f"[CONVERSATION SUMMARY]: {summary}"] + chat_history[-MAX_HISTORY_TURNS:]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_conversation_phase(ctx, phase_config) -> None:
    """
    Run one conversation phase to completion.

    ctx         — SessionContext (mutated in place)
    phase_config — PhaseConfig instance describing this phase's behaviour
    """
    chat_history = []
    pending_user_input = None

    # Opening message
    if phase_config.opening_message_fn:
        opening = phase_config.opening_message_fn(ctx)
        if opening:
            print(f"\n  Stretch: {opening}\n")
            chat_history.append(f"Assistant: {opening}")

    while True:
        if _DEBUG:
            print(f"\n[Phase: {phase_config.phase_name}] World: {ctx.world_state.summary()}")

        user_input = input(phase_config.input_prompt).strip()

        if not user_input:
            continue

        finish = any(user_input.lower() == cmd for cmd in phase_config.finish_commands)
        if finish or user_input.lower() == "/quit":
            break

        chat_history.append(f"You: {user_input}")

        # Conversational reply
        dashboard.set_status("Thinking…")
        print("\n  (thinking...)", end="\r", flush=True)
        reply_data = get_conversation_reply(
            chat_history=chat_history,
            kb_string=ctx.kb.get_state_as_string(),
            plan_data=ctx.current_plan or "(no plan yet)",
            system_prompt_override=phase_config.system_prompt_override,
        )
        reply         = reply_data.get("reply", "I'm not sure what to say.")
        is_clarifying = reply_data.get("is_clarifying_question", False)
        print(f"  Stretch: {reply}\n")
        chat_history.append(f"Assistant: {reply}")
        dashboard.set_status("Waiting for input…")

        # Proactive proposals after the human speaks (POST_TASK only)
        if phase_config.enable_proposals:
            proposals = get_proactive_proposals(
                chat_history=chat_history,
                kb_string=ctx.kb.get_state_as_string(),
                incident_log=ctx.incident_log,
            )
            if proposals:
                print(f"  Stretch (suggestion): {proposals}\n")

        if is_clarifying:
            pending_user_input = user_input
            continue

        if not phase_config.enable_kb_updates:
            chat_history = _manage_history(chat_history)
            pending_user_input = None
            continue

        # --- KB extraction ---
        if pending_user_input is not None:
            clarifying_q = chat_history[-2].replace("Assistant: ", "")
            extraction_context = (
                f"User originally said: \"{pending_user_input}\"\n"
                f"Assistant asked: \"{clarifying_q}\"\n"
                f"User confirmed: \"{user_input}\""
            )
            pending_user_input = None
        else:
            extraction_context = user_input

        extract_data = extract_kb_updates(user_message=extraction_context)
        kb_updates   = extract_data.get("kb_update", [])

        if kb_updates:
            print("  I understood the following:")
            for u in kb_updates:
                if isinstance(u, dict):
                    label = KB_CATEGORY_LABELS.get(u.get("type", ""), u.get("type", ""))
                    print(f"    • [{label}] {u.get('fact', '')}")
                    if _DEBUG and u.get("pddl_removal"):
                        print(f"      [debug] pddl_removal: {u['pddl_removal']}")

            confirm = input("\n  Update the plan with these changes? [y/n]: ").strip().lower()
            if confirm == "y":
                kb_snapshot = ctx.kb.get_state_as_string()
                ctx.kb.update_kb_from_llm(kb_updates)
                has_pddl_changes = ctx.kb.get_state_as_string() != kb_snapshot

                if has_pddl_changes:
                    dashboard.set_status("Replanning…")
                    print("\n  Replanning...", end="", flush=True)

                    if phase_config.phase_name == "REPLAN":
                        # Replan from current world state
                        from pddl_utils import run_pddl_solver_from_world_state
                        new_plan = run_pddl_solver_from_world_state(
                            ctx.world_state, ctx.kb, ctx.domain_file
                        )
                    else:
                        # Replan from original problem file
                        if apply_kb_to_pddl(ctx.kb, ctx.original_problem_file,
                                            ctx.current_problem_file):
                            new_plan = run_pddl_solver(ctx.domain_file,
                                                       ctx.current_problem_file)
                        else:
                            new_plan = None

                    if new_plan:
                        print(" done.")
                        if phase_config.show_plan_diff and ctx.current_plan:
                            diff_plans(ctx.current_plan, new_plan)
                        ctx.current_plan = new_plan
                        dashboard.update(
                            _compute_plan_rows(new_plan), ctx.kb, "Plan updated ✓",
                            plan_actions=_compute_plan_actions(new_plan),
                        )
                    else:
                        print(" could not find a valid plan with these constraints.")
                        print("  The previous plan has been kept.")
                        dashboard.update(
                            _compute_plan_rows(ctx.current_plan or ""),
                            ctx.kb, "Replan failed — previous plan kept",
                        )
                else:
                    dashboard.update(
                        _compute_plan_rows(ctx.current_plan or ""),
                        ctx.kb, "Preferences updated ✓",
                    )
                    print("  (No change to the plan was needed.)")
            else:
                print("  Changes discarded.")

        chat_history = _manage_history(chat_history)

    # Phase end callback
    if phase_config.on_phase_end:
        phase_config.on_phase_end(ctx)
