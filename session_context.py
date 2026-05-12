"""
session_context.py

SessionContext is the single object threaded through all three phases
(PRE_TASK → REPLAN → POST_TASK). It holds everything that needs to survive
a phase transition: KB, world state, plan, logs, and file references.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import datetime


@dataclass
class SessionContext:
    # Core state
    kb: object                          # KnowledgeBase instance
    world_state: object                 # WorldStateTracker instance
    current_plan: str | None            # raw sas_plan text (None until first solve)

    # Persistent memory
    incident_log: list[dict] = field(default_factory=list)
    session_notes: list[str] = field(default_factory=list)

    # Current-execution state (populated during REPLAN phase)
    current_incident: dict | None = None

    # Identity
    session_id: str = field(default_factory=lambda: datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    run_number: int = 1

    # File paths
    original_problem_file: Path = field(default_factory=lambda: Path("pddl/problem_simple.pddl"))
    current_problem_file: Path = field(default_factory=lambda: Path("current_problem_simple.pddl"))
    domain_file: Path = field(default_factory=lambda: Path("pddl/domain_simple.pddl"))

    def start_incident(self, trigger: str) -> None:
        """Open a new incident record. Called when a failure is detected."""
        self.current_incident = {
            "session_id": self.session_id,
            "run_number": self.run_number,
            "trigger": trigger,
            "timestamp": datetime.datetime.now().isoformat(),
            "steps_completed": 0,
            "failure_reason": None,
            "resolution": None,
            "replan_count": 0,
        }

    def close_incident(self, resolution: str) -> None:
        """Finalise the current incident and append it to the log."""
        if self.current_incident is None:
            return
        self.current_incident["resolution"] = resolution
        self.incident_log.append(self.current_incident)
        self.current_incident = None
