"""
combat_state.py

In-memory combat state.  One active combat per server instance for now;
this will be scoped per zone/encounter when the persistent world layer arrives.
"""

import threading
from typing import Optional

from pydantic import BaseModel, Field

import dice
import state as char_state


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

class CombatState(BaseModel):
    active:            bool       = True
    round:             int        = 1
    initiative_order:  list[str]  = Field(default_factory=list)   # entity_ids, sorted
    initiative_rolls:  dict[str, int] = Field(default_factory=dict)
    current_index:     int        = 0

    @property
    def current_entity(self) -> Optional[str]:
        if not self.initiative_order:
            return None
        return self.initiative_order[self.current_index % len(self.initiative_order)]


# ---------------------------------------------------------------------------
# Module-level store
# ---------------------------------------------------------------------------

_combat: Optional[CombatState] = None
_lock   = threading.RLock()


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------

def get_combat() -> Optional[CombatState]:
    with _lock:
        return _combat


def init_combat(entity_ids: list[str]) -> CombatState:
    """
    Roll initiative for all entities and initialise CombatState.
    Resets action economy for the entity who goes first.
    """
    global _combat

    rolls: dict[str, int] = {}
    for eid in entity_ids:
        cs = char_state.get_character(eid)
        if cs is None:
            raise ValueError(f"Entity {eid!r} not found in state store.")
        dex_mod   = (cs.sheet.ability_scores.dexterity - 10) // 2
        rolls[eid] = dice.roll_d20()["roll"] + dex_mod

    # Stable descending sort — ties preserve insertion order
    order = sorted(rolls.keys(), key=lambda e: rolls[e], reverse=True)

    combat = CombatState(
        initiative_order=order,
        initiative_rolls=rolls,
        current_index=0,
        round=1,
    )

    with _lock:
        _combat = combat

    # Reset economy for whoever goes first
    char_state.reset_economy(order[0])

    return combat


def advance_turn() -> tuple[str, CombatState]:
    """
    Step to the next entity in initiative order.
    Wraps around and increments round counter when the list is exhausted.
    Resets the incoming entity's action economy.

    Returns (next_entity_id, updated_CombatState).
    """
    global _combat

    with _lock:
        if _combat is None:
            raise RuntimeError("No active combat.")

        next_idx   = (_combat.current_index + 1) % len(_combat.initiative_order)
        new_round  = _combat.round + (1 if next_idx == 0 else 0)

        updated    = _combat.model_copy(update={
            "current_index": next_idx,
            "round":         new_round,
        })
        _combat = updated

    next_entity = updated.initiative_order[next_idx]
    char_state.reset_economy(next_entity)

    return next_entity, updated


def end_combat() -> None:
    global _combat
    with _lock:
        _combat = None
