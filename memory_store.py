"""
memory_store.py

Read/write helpers for the three JSON files in memory/:
  - kb_persistent.json     (KB state across sessions)
  - incident_log.json      (list of incident records)
  - session_notes.json     (list of free-text notes)

All functions are pure I/O — no business logic.
"""

import json
from pathlib import Path

_MEMORY_DIR = Path("memory")

KB_PATH       = _MEMORY_DIR / "kb_persistent.json"
INCIDENT_PATH = _MEMORY_DIR / "incident_log.json"
NOTES_PATH    = _MEMORY_DIR / "session_notes.json"

_KB_EMPTY = {
    "human_preference": [],
    "human_limitations": [],
    "robot_limitations": [],
    "environmental_factors": [],
}


def load_kb() -> dict:
    try:
        return json.loads(KB_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(_KB_EMPTY)


def save_kb(state: dict) -> None:
    _MEMORY_DIR.mkdir(exist_ok=True)
    KB_PATH.write_text(json.dumps(state, indent=2))


def load_incident_log() -> list[dict]:
    try:
        return json.loads(INCIDENT_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_incident_log(log: list[dict]) -> None:
    _MEMORY_DIR.mkdir(exist_ok=True)
    INCIDENT_PATH.write_text(json.dumps(log, indent=2))


def load_session_notes() -> list[str]:
    try:
        return json.loads(NOTES_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_session_notes(notes: list[str]) -> None:
    _MEMORY_DIR.mkdir(exist_ok=True)
    NOTES_PATH.write_text(json.dumps(notes, indent=2))


def append_incident(incident: dict) -> None:
    """Load, append, save — safe for single-writer use."""
    log = load_incident_log()
    log.append(incident)
    save_incident_log(log)


def append_session_note(note: str) -> None:
    notes = load_session_notes()
    notes.append(note)
    save_session_notes(notes)


def clear_all() -> None:
    """Wipe all persistent memory back to empty defaults."""
    _MEMORY_DIR.mkdir(exist_ok=True)
    KB_PATH.write_text(json.dumps(_KB_EMPTY, indent=2))
    INCIDENT_PATH.write_text(json.dumps([], indent=2))
    NOTES_PATH.write_text(json.dumps([], indent=2))
