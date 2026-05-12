from __future__ import annotations
"""
world_state.py

WorldStateTracker maintains a live ground-truth model of the physical world.
It is updated in real time during execution and can serialize itself into a
PDDL :init block for replanning.

Rooms:  kitchen, pantry
Agents: p1 (human), p2 (robot)
Items:  bread, ham, cheese, lettuce
"""

import json

_ROOMS  = ("kitchen", "pantry")
_AGENTS = ("p1", "p2")
_ITEMS  = ("bread", "ham", "cheese", "lettuce")

_INITIAL_ITEM_ROOM = {
    "bread":   "pantry",
    "ham":     "pantry",
    "cheese":  "kitchen",
    "lettuce": "kitchen",
}


class WorldStateTracker:

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        # Where is each item? (room name or None if held)
        self.item_room: dict[str, str | None] = dict(_INITIAL_ITEM_ROOM)

        # Who is holding what? (item name or None)
        self.agent_holding: dict[str, str | None] = {a: None for a in _AGENTS}

        # Where is each agent?
        self.agent_room: dict[str, str] = {a: "kitchen" for a in _AGENTS}

    # -----------------------------------------------------------------------
    # Mutation helpers — called by execution_manager when actions complete
    # -----------------------------------------------------------------------

    def agent_moved(self, agent: str, room: str) -> None:
        if agent in _AGENTS and room in _ROOMS:
            self.agent_room[agent] = room

    def agent_took(self, agent: str, item: str) -> None:
        if agent in _AGENTS and item in _ITEMS:
            self.item_room[item] = None
            self.agent_holding[agent] = item

    def agent_dropped(self, agent: str, item: str, room: str) -> None:
        if agent in _AGENTS and item in _ITEMS and room in _ROOMS:
            self.item_room[item] = room
            self.agent_holding[agent] = None

    # -----------------------------------------------------------------------
    # Serialization
    # -----------------------------------------------------------------------

    def to_pddl_init(self) -> str:
        """
        Emit the :init block body (indented, no surrounding parens).
        Called by pddl_utils._generate_problem_pddl().
        """
        lines = []

        # Agent locations
        for agent, room in self.agent_room.items():
            lines.append(f"    (at {agent} {room})")

        # Agent hand state
        for agent in _AGENTS:
            held = self.agent_holding[agent]
            if held:
                lines.append(f"    (holding {agent} {held})")
            else:
                lines.append(f"    (empty-hand {agent})")

        # Item locations (only those not held)
        for item, room in self.item_room.items():
            if room is not None:
                lines.append(f"    (in-room {item} {room})")

        # Connectivity (static)
        lines.append("    (connected kitchen pantry)")
        lines.append("    (connected pantry kitchen)")

        # Permissions (static — removed by KB if needed)
        for agent in _AGENTS:
            for room in _ROOMS:
                lines.append(f"    (can-enter {agent} {room})")
        for agent in _AGENTS:
            for item in _ITEMS:
                lines.append(f"    (can-take {agent} {item})")

        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "item_room":     dict(self.item_room),
            "agent_holding": dict(self.agent_holding),
            "agent_room":    dict(self.agent_room),
        }

    def from_dict(self, data: dict) -> None:
        self.item_room     = data.get("item_room",     dict(_INITIAL_ITEM_ROOM))
        self.agent_holding = data.get("agent_holding", {a: None for a in _AGENTS})
        self.agent_room    = data.get("agent_room",    {a: "kitchen" for a in _AGENTS})

    def summary(self) -> str:
        """One-paragraph human-readable snapshot for LLM prompts."""
        lines = []
        for item, room in self.item_room.items():
            if room:
                lines.append(f"{item} is in the {room}")
        for agent, item in self.agent_holding.items():
            label = "You" if agent == "p1" else "Robot"
            if item:
                lines.append(f"{label} is holding {item}")
        return "; ".join(lines) + "." if lines else "World state unknown."
