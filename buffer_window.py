from __future__ import annotations
"""
buffer_window.py

Pauses execution after a stop or failure so the observer can update the world
state in the dashboard before replanning begins.
"""


def run_buffer_window(reason: str, **_kwargs) -> str | None:
    """
    Display the stop reason, prompt the observer to update the dashboard,
    and wait for them to press Enter before replanning proceeds.

    Returns any optional note typed before Enter, or None if Enter was pressed alone.
    """
    print(f"\n  Execution stopped: {reason}")
    print(f"  Update completed steps in the dashboard (http://localhost:5050).")
    raw = input("  Press Enter when done (or type a note to help with replanning): ").strip()
    return raw if raw else None
