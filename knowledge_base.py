import json

class KnowledgeBase:

    def __init__(self):
        self.state = {
            "human_preference": [],
            "human_limitations": [],
            "robot_limitations": [],
            "environmental_factors": []
        }
        print("[KB] KnowledgeBase initialized (empty).")

    def reset(self):
        self.state = {
            "human_preference": [],
            "human_limitations": [],
            "robot_limitations": [],
            "environmental_factors": []
        }
        print("[KB] KnowledgeBase reset to empty state.")

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

        print("[KB] Appending update(s) from LLM...")

        if isinstance(updates_data, dict):
            for category, facts_list in updates_data.items():
                if category in self.state and isinstance(facts_list, list):
                    for fact in facts_list:
                        self.state[category].append(fact)
                        label = fact.get("fact") if isinstance(fact, dict) else fact
                        removal = fact.get("pddl_removal") if isinstance(fact, dict) else None
                        print(f"[KB] Added to '{category}': {label}" +
                              (f" → remove {removal}" if removal else ""))
                else:
                    print(f"[KB Warning] Unknown category or bad format: '{category}'")

        elif isinstance(updates_data, list):
            for update in updates_data:
                if not isinstance(update, dict):
                    print(f"[KB Warning] Skipping non-dict item: {update}")
                    continue

                category = update.get("type")
                fact_text = update.get("fact")
                pddl_removal = update.get("pddl_removal")  # May be None

                if category in self.state and fact_text:
                    entry = {"fact": fact_text}
                    if pddl_removal:
                        entry["pddl_removal"] = pddl_removal
                    self.state[category].append(entry)
                    print(f"[KB] Added to '{category}': {fact_text}" +
                          (f" → remove {pddl_removal}" if pddl_removal else ""))
                else:
                    print(f"[KB Warning] Unknown category or missing fact: '{category}'")
        else:
            print(f"[KB ERROR] Unexpected update format: {type(updates_data)}")
