"""
plan_utils.py

Shared plan display and PDDL-to-English translation utilities.
Imported by conversation_engine.py, main.py, and phase_configs.py.
"""

AGENT_LABELS = {"p1": "You", "p2": "Robot"}
ROOM_LABELS  = {"kitchen": "the kitchen", "pantry": "the pantry"}
ITEM_LABELS  = {
    "bread":   "the bread",
    "ham":     "the ham",
    "cheese":  "the cheese",
    "lettuce": "the lettuce",
}
KB_CATEGORY_LABELS = {
    "human_preference":      "Your preferences",
    "human_limitations":     "Your limitations",
    "robot_limitations":     "Robot limitations",
    "environmental_factors": "Environment notes",
}


def _tokenize_action(action_str: str) -> list[str]:
    """'(move p2 kitchen pantry)' → ['move', 'p2', 'kitchen', 'pantry']"""
    return action_str.strip("()").split()


def _humanize_action(tokens: list[str]) -> str:
    """Convert a PDDL token list into a plain-English sentence."""
    if not tokens:
        return ""
    action = tokens[0]
    agent  = tokens[1] if len(tokens) > 1 else ""
    actor  = AGENT_LABELS.get(agent, agent)
    human  = agent == "p1"  # "You" uses bare infinitive; "Robot" uses third-person

    if action == "move" and len(tokens) >= 4:
        dest = ROOM_LABELS.get(tokens[3], tokens[3])
        verb = "move" if human else "moves"
        return f"{actor} {verb} to {dest}"
    elif action == "take" and len(tokens) >= 4:
        item = ITEM_LABELS.get(tokens[2], tokens[2])
        verb = "pick up" if human else "picks up"
        return f"{actor} {verb} {item}"
    elif action == "drop" and len(tokens) >= 4:
        item = ITEM_LABELS.get(tokens[2], tokens[2])
        dest = ROOM_LABELS.get(tokens[3], tokens[3])
        verb = "put down" if human else "puts down"
        return f"{actor} {verb} {item} in {dest}"
    elif action == "slice-ham":
        verb = "slice" if human else "slices"
        return f"{actor} {verb} the ham"
    elif action == "wash-lettuce":
        verb = "wash" if human else "washes"
        return f"{actor} {verb} the lettuce"
    elif action == "make-sandwich":
        verb = "assemble" if human else "assembles"
        return f"{actor} {verb} the sandwich"
    else:
        return " ".join(tokens)


def _parse_actions(raw_plan_str: str) -> list[str]:
    if not raw_plan_str:
        return []
    return [
        line.strip()
        for line in raw_plan_str.split("\n")
        if line.strip() and not line.strip().startswith(";")
    ]


def _compute_plan_rows(raw_plan_string: str) -> list[dict]:
    """Return plan as a list of dicts without printing anything."""
    rows = []
    for action_str in _parse_actions(raw_plan_string):
        tokens = _tokenize_action(action_str)
        actor  = AGENT_LABELS.get(tokens[1], tokens[1]) if len(tokens) > 1 else "?"
        desc   = _humanize_action(tokens)
        rows.append({"actor": actor, "description": desc})
    return rows


def _compute_plan_actions(raw_plan_string: str) -> list[list[str]]:
    """Return plan as a list of token lists (for world-state tracking in the dashboard)."""
    return [_tokenize_action(a) for a in _parse_actions(raw_plan_string)]


def print_plan(raw_plan_string: str) -> list[dict]:
    """Print the plan as a formatted table and return the rows."""
    rows = _compute_plan_rows(raw_plan_string)
    if not rows:
        print("  (No steps planned yet)")
        return rows
    col_w = max(len(r["description"]) for r in rows) + 2
    print("  " + "-" * (col_w + 16))
    print(f"  {'Step':<6}{'Who':<10}{'Action'}")
    print("  " + "-" * (col_w + 16))
    for i, row in enumerate(rows, 1):
        print(f"  {i:<6}{row['actor']:<10}{row['description']}")
    print("  " + "-" * (col_w + 16))
    return rows


def diff_plans(old_plan_str: str, new_plan_str: str) -> None:
    """Print a human-readable summary of what changed between two plans."""
    old     = _parse_actions(old_plan_str)
    new     = _parse_actions(new_plan_str)
    old_set = set(old)
    new_set = set(new)

    removed = [a for a in old if a not in new_set]
    added   = [a for a in new if a not in old_set]
    kept    = [a for a in new if a in old_set]

    print("\n  What changed in the plan:")
    print("  " + "-" * 44)
    if not removed and not added:
        print("  No changes to the steps.")
    for a in removed:
        print(f"  ✕  {_humanize_action(_tokenize_action(a))}")
    for a in added:
        print(f"  ✓  {_humanize_action(_tokenize_action(a))}")
    if kept:
        print(f"  {len(kept)} step(s) stayed the same.")
    print("  " + "-" * 44)
