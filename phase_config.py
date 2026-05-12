"""
phase_config.py

PhaseConfig parameterizes the single unified conversation loop so the same
engine can power PRE_TASK, REPLAN, and POST_TASK without branching.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class PhaseConfig:
    # Phase identity
    phase_name: str                     # "PRE_TASK" | "REPLAN" | "POST_TASK"

    # LLM prompt overrides (None → use the default system prompt)
    system_prompt_override: Optional[str] = None

    # What finish commands end the phase
    finish_commands: tuple[str, ...] = ("/finish",)

    # Whether to collect KB updates and trigger replanning
    enable_kb_updates: bool = True

    # Whether to offer plan execution at phase end
    offer_execution: bool = False

    # Prompt shown to the user for their input
    input_prompt: str = "You (or '/finish' to finalize): "

    # Whether to show a plan diff when the plan changes
    show_plan_diff: bool = True

    # Whether to show proactive robot proposals (post-task deliberation)
    enable_proposals: bool = False

    # Optional callback: called once at phase start, returns an opening message
    # Signature: (ctx: SessionContext) -> str
    opening_message_fn: Optional[Callable] = None

    # Optional callback: called once at phase end
    # Signature: (ctx: SessionContext) -> None
    on_phase_end: Optional[Callable] = None
