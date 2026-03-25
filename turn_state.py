"""
turn_state.py

Persistent turn engine. Always running — no combat/non-combat distinction.

The DM sets the turn order (via roll_initiative or set_turn_order).
Tick increments every time the order wraps around, giving a monotonic
realm-time counter usable for effect durations, downtime, etc.
"""

import threading
from typing import Optional

from pydantic import BaseModel, Field

import dice
import state as char_state


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

class TurnState(BaseModel):
    tick:             int            = 0          # realm-time counter; increments each full rotation
    turn_order:       list[str]      = Field(default_factory=list)   # entity_ids in order
    turn_index:       int            = 0
    initiative_rolls: dict[str, int] = Field(default_factory=dict)  # stored for reference

    @property
    def current_entity(self) -> Optional[str]:
        if not self.turn_order:
            return None
        return self.turn_order[self.turn_index % len(self.turn_order)]


# ---------------------------------------------------------------------------
# Module-level store
# ---------------------------------------------------------------------------

_turn: TurnState = TurnState()
_lock = threading.RLock()


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------

def get_turn() -> TurnState:
    with _lock:
        return _turn


def roll_initiative(entity_ids: list[str]) -> TurnState:
    """
    Roll initiative for the given entities and set the turn order.
    Ties broken by DEX modifier already baked into the roll.
    Resets action economy for whoever goes first.
    """
    global _turn

    rolls: dict[str, int] = {}
    for eid in entity_ids:
        cs = char_state.get_character(eid)
        if cs is None:
            raise ValueError(f"Entity {eid!r} not registered.")
        dex_mod    = (cs.sheet.ability_scores.dexterity - 10) // 2
        rolls[eid] = dice.roll_d20()["roll"] + dex_mod

    order = sorted(rolls.keys(), key=lambda e: rolls[e], reverse=True)

    with _lock:
        _turn = TurnState(
            tick=_turn.tick,          # preserve realm-time across re-orders
            turn_order=order,
            turn_index=0,
            initiative_rolls=rolls,
        )

    if order:
        char_state.reset_economy(order[0])

    return _turn


def set_turn_order(entity_ids: list[str]) -> TurnState:
    """
    Manually assign turn order without rolling.
    Used for scripted scenes, cutscenes, or DM-driven sequencing.
    """
    global _turn

    with _lock:
        _turn = TurnState(
            tick=_turn.tick,
            turn_order=list(entity_ids),
            turn_index=0,
            initiative_rolls={},
        )

    if entity_ids:
        char_state.reset_economy(entity_ids[0])

    return _turn


def advance_turn() -> tuple[str, TurnState]:
    """
    Advance to the next entity. Increments tick when the order wraps.
    Resets the incoming entity's action economy.
    Returns (next_entity_id, updated_TurnState).
    """
    global _turn

    with _lock:
        if not _turn.turn_order:
            raise RuntimeError("Turn order is empty — call roll_initiative or set_turn_order first.")

        next_idx  = (_turn.turn_index + 1) % len(_turn.turn_order)
        new_tick  = _turn.tick + (1 if next_idx == 0 else 0)

        _turn = _turn.model_copy(update={
            "turn_index": next_idx,
            "tick":       new_tick,
        })

    next_entity = _turn.turn_order[next_idx]
    char_state.reset_economy(next_entity)

    return next_entity, _turn


def add_to_order(entity_id: str, after_index: Optional[int] = None) -> TurnState:
    """
    Insert an entity into the turn order.
    after_index: position to insert after (None = append to end).
    """
    global _turn

    with _lock:
        order = list(_turn.turn_order)
        if entity_id in order:
            return _turn  # already present
        if after_index is None:
            order.append(entity_id)
        else:
            order.insert(after_index + 1, entity_id)
        _turn = _turn.model_copy(update={"turn_order": order})

    return _turn


def remove_from_order(entity_id: str) -> TurnState:
    """
    Remove an entity from the turn order (fled, dead, left scene, etc.).
    Adjusts turn_index if needed so the current turn is not skipped.
    """
    global _turn

    with _lock:
        order = list(_turn.turn_order)
        if entity_id not in order:
            return _turn

        removed_idx = order.index(entity_id)
        order.remove(entity_id)

        new_idx = _turn.turn_index
        if removed_idx < _turn.turn_index:
            new_idx = max(0, new_idx - 1)
        if order:
            new_idx = new_idx % len(order)
        else:
            new_idx = 0

        _turn = _turn.model_copy(update={"turn_order": order, "turn_index": new_idx})

    return _turn
