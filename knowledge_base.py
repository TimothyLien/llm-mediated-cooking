import json
from pathlib import Path

class KnowledgeBase:

    def __init__(self):
        self.state = {
            "human_preference": [],
            "human_limitations": [],
            "robot_limitations": [],
            "environmental_factors": []
        }

    def reset(self):
        self.state = {
            "human_preference": [],
            "human_limitations": [],
            "robot_limitations": [],
            "environmental_factors": []
        }
        pass

    def get_state_as_string(self):
        """
        Returns a JSON string of the KB for LLM prompts.
        Shows only 'fact' strings (hides pddl_removal) for readability.
        """
        simplified = {}
        for category, facts in self.state.items():
            simplified[category] = [
                f["fact"] if isinstance(f, dict) else f
                for f in facts
            ]
        return json.dumps(simplified, indent=2)

    def get_state(self):
        return self.state

    def get_pddl_removals(self):
        """
        Returns a deduplicated list of PDDL predicates that should be removed
        from :init. Deduplication prevents the same predicate being applied twice
        when multiple KB entries map to the same PDDL fact.
        """
        seen = set()
        removals = []
        for facts in self.state.values():
            for fact in facts:
                if isinstance(fact, dict) and fact.get("pddl_removal"):
                    pred = fact["pddl_removal"]
                    if pred not in seen:
                        seen.add(pred)
                        removals.append(pred)
        return removals

    def update_kb_from_llm(self, updates_data):
        """
        APPENDS new facts from the LLM to the KB. Never resets.

        The LLM now returns only the delta (new facts from the current message),
        not the full KB state. Accumulation is handled here, not by the LLM.

        Supported input formats:
          List:  [{"type": "robot_limitations", "fact": "...", "pddl_removal": "..."}]
          Dict:  {"robot_limitations": [{"fact": "...", "pddl_removal": "..."}], ...}

        The 'pddl_removal' field is optional in both formats.
        """
        if not updates_data:
            return

        if isinstance(updates_data, dict):
            for category, facts_list in updates_data.items():
                if category in self.state and isinstance(facts_list, list):
                    for fact in facts_list:
                        self.state[category].append(fact)
                else:
                    pass  # unknown category — silently skip

        elif isinstance(updates_data, list):
            for update in updates_data:
                if not isinstance(update, dict):
                    continue

                category = update.get("type")
                fact_text = update.get("fact")
                pddl_removal = update.get("pddl_removal")  # May be None

                if category in self.state and fact_text:
                    entry = {"fact": fact_text}
                    if pddl_removal:
                        entry["pddl_removal"] = pddl_removal
                    self.state[category].append(entry)
                # else: unknown category or missing fact — silently skip
        # else: unexpected format — silently ignore

    # -----------------------------------------------------------------------
    # Persistence
    # -----------------------------------------------------------------------

    def load_from_file(self, path: Path) -> None:
        """Replace current state with contents of a JSON file (if it exists)."""
        try:
            data = json.loads(path.read_text())
            if isinstance(data, dict):
                for category in self.state:
                    if category in data and isinstance(data[category], list):
                        self.state[category] = data[category]
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def save_to_file(self, path: Path) -> None:
        """Write current state to a JSON file, creating parent dirs as needed."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.state, indent=2))
