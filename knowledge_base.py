import json

class KnowledgeBase:

    def __init__(self):
        """Initializes the new, empty knowledge base state."""
        self.state = {
            "human_preference": [],
            "human_limitations": [],
            "robot_limitations": [],
            "environmental_factors": []
        }
        print("[KB] KnowledgeBase initialized (empty).")

    def reset(self):
        """Resets the knowledge base to an empty state."""
        self.state = {
            "human_preference": [],
            "human_limitations": [],
            "robot_limitations": [],
            "environmental_factors": []
        }
        print("[KB] KnowledgeBase has been reset to empty state.")

    def get_state_as_string(self):
        """Returns a string representation of the KB for the LLM prompt."""
        return json.dumps(self.state, indent=2)

    def get_state(self):
        """Returns the raw state dictionary."""
        return self.state

    def update_kb_from_llm(self, updates_data):
        """
        Parses update data from the LLM and applies them to the current state.
        
        The function must handle two possible update formats from the LLM engine:
        1. A LIST of fact dictionaries (Legacy/Simple format).
        2. A DICTIONARY where keys are KB categories (The full desired state).
        
        This implementation assumes the KB must be reset if the LLM provides 
        a complete, new state dictionary.
        """

        if not updates_data:
            return 0 

        print(f"Updating [KB] with update(s) from LLM...")
        
        # Scenario 1: LLM returned the full, complete state dictionary (as seen in the failed log)
        if isinstance(updates_data, dict):
            
            # --- CRITICAL FIX ---
            # Resetting the state to ensure cumulative facts are applied correctly
            self.reset()
            
            for update_type, facts_list in updates_data.items():
                if update_type in self.state and isinstance(facts_list, list):
                    # Replace the entire fact list with the LLM's new list
                    self.state[update_type] = facts_list
                    for fact in facts_list:
                        # Print statement for visibility (assuming 'fact' is a string)
                        print(f"[KB] Added to '{update_type}': {fact}")
                else:
                    print(f"[KB Warning] Invalid update type or format for type: {update_type}")
            
            return len(self.state.get("robot_limitations", []))
            
        # Scenario 2: LLM returned the legacy LIST of fact dictionaries (your original intent)
        elif isinstance(updates_data, list):
            
            # Since the LLM is expected to return ALL facts, we reset first.
            self.reset()

            for update in updates_data:
                if not isinstance(update, dict):
                    print(f"[KB Warning] Skipping invalid update item (not a dictionary): {update}")
                    continue
                    
                update_type = update.get("type")
                update_fact = update.get("fact")
                
                if update_type in self.state and update_fact:
                    # Append the string fact to the correct list
                    self.state[update_type].append(update_fact)
                    print(f"[KB] Added to '{update_type}': {update_fact}")
                else:
                    print(f"[KB Warning] Unknown update type or missing fact: {update_type}")
        else:
             print(f"[KB ERROR] Update data received in unexpected format: {type(updates_data)}")