"""
state.py

In-memory character state store, keyed by character_id.
The DM backend writes back a full CharacterState after each tool resolves.
The MCP server reads from it each context request to gate tools and return the sheet.
"""

import threading
from typing import Optional
from character import CharacterState, PlayerSheet, ActionEconomy


_store: dict[str, CharacterState] = {}
_lock = threading.RLock()


def get_character(character_id: str) -> Optional[CharacterState]:
    with _lock:
        return _store.get(character_id)


def update_character(character_id: str, state: CharacterState) -> None:
    """Full state replacement. DM backend calls this after each tool resolves."""
    with _lock:
        _store[character_id] = state


def register_character(character_id: str, sheet: PlayerSheet) -> CharacterState:
    """
    Register a new character with a fresh economy state.
    Called when an agent-player connects to this server instance.
    """
    with _lock:
        state = CharacterState(sheet=sheet, economy=ActionEconomy())
        _store[character_id] = state
        return state


def reset_economy(character_id: str) -> Optional[CharacterState]:
    """
    Reset action economy to a fresh turn state.
    Called by the DM backend at the start of each new turn.
    """
    with _lock:
        state = _store.get(character_id)
        if state is None:
            return None
        updated = state.model_copy(update={"economy": ActionEconomy()})
        _store[character_id] = updated
        return updated


def remove_character(character_id: str) -> None:
    """Clean up when a session ends."""
    with _lock:
        _store.pop(character_id, None)


def list_characters() -> list[str]:
    with _lock:
        return list(_store.keys())
