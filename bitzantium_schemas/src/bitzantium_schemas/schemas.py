"""
schemas.py

Engine/runtime schemas — gameplay logic only.
These exist at runtime and are never loaded from data files.

World content (items, abilities, classes, creatures) lives in data.py.
Character state lives in character.py.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field

from bitzantium_schemas.data import DamageType, ItemInstance, AbilityScores


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Condition(str, Enum):
    BLINDED = "blinded"
    CHARMED = "charmed"
    DEAFENED = "deafened"
    FRIGHTENED = "frightened"
    GRAPPLED = "grappled"
    INCAPACITATED = "incapacitated"
    INVISIBLE = "invisible"
    PARALYZED = "paralyzed"
    PETRIFIED = "petrified"
    POISONED = "poisoned"
    PRONE = "prone"
    RESTRAINED = "restrained"
    STUNNED = "stunned"
    UNCONSCIOUS = "unconscious"
    RAGING = "raging"
    CONCENTRATING = "concentrating"
    HIDDEN = "hidden"


class LightLevel(str, Enum):
    BRIGHT = "bright"
    DIM = "dim"
    DARKNESS = "darkness"


# ---------------------------------------------------------------------------
# World / spatial
# ---------------------------------------------------------------------------

class Position(BaseModel):
    x: int
    y: int
    z: int = 0
    area_id: str


class ActiveEffect(BaseModel):
    effect_id: str
    name: str
    source: str
    duration_turns: Optional[int] = None
    concentration: bool = False
    description: str


class EntitySummary(BaseModel):
    entity_id: str
    name: Optional[str] = None
    entity_type: str
    position: Position
    visible: bool
    conditions: list[Condition] = Field(default_factory=list)
    hostile: Optional[bool] = None
    description: Optional[str] = None


# ---------------------------------------------------------------------------
# State delta
# ---------------------------------------------------------------------------

class StateDelta(BaseModel):
    hp_changes: dict[str, int] = Field(default_factory=dict)
    position_changes: dict[str, Position] = Field(default_factory=dict)
    conditions_added: dict[str, list[Condition]] = Field(default_factory=dict)
    conditions_removed: dict[str, list[Condition]] = Field(default_factory=dict)
    effects_added: list[ActiveEffect] = Field(default_factory=list)
    effects_removed: list[str] = Field(default_factory=list)
    items_added: list[ItemInstance] = Field(default_factory=list)
    items_removed: list[str] = Field(default_factory=list)
    resource_changes: dict[str, int] = Field(default_factory=dict)
    entities_revealed: list[EntitySummary] = Field(default_factory=list)
    entities_despawned: list[str] = Field(default_factory=list)
    flags: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Action result
# ---------------------------------------------------------------------------

class ActionResult(BaseModel):
    success: bool
    action: str
    actor_id: str
    narrative: str
    data: dict[str, Any] = Field(default_factory=dict)
    state_delta: StateDelta = Field(default_factory=StateDelta)
    errors: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Exploration
# ---------------------------------------------------------------------------

class Exit(BaseModel):
    direction: str
    destination_area_id: str
    destination_name: str
    blocked: bool = False
    block_reason: Optional[str] = None
    distance_ft: int


class AreaDescription(BaseModel):
    area_id: str
    name: str
    description: str
    visible_entities: list[EntitySummary] = Field(default_factory=list)
    visible_items: list[ItemInstance] = Field(default_factory=list)
    exits: list[Exit] = Field(default_factory=list)
    light_level: LightLevel = LightLevel.BRIGHT
    ambient_effects: list[str] = Field(default_factory=list)
    known_traps: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Dialogue
# ---------------------------------------------------------------------------

class DialogueOption(BaseModel):
    option_id: str
    text: str
    skill_check: Optional[str] = None


class DialogueState(BaseModel):
    npc_id: str
    npc_name: str
    npc_speech: str
    options: list[DialogueOption]
    trade_available: bool = False


# ---------------------------------------------------------------------------
# Combat outcomes
# ---------------------------------------------------------------------------

class AttackOutcome(BaseModel):
    hit: bool
    target_id: str
    damage_dealt: int
    damage_type: DamageType
    damage_resisted: bool = False
    target_hp_remaining: Optional[int] = None
    target_defeated: bool = False


class SpellOutcome(BaseModel):
    ability_id: str
    slot_used: int
    targets_affected: list[str]
    damage_dealt: dict[str, int] = Field(default_factory=dict)
    healing_done: dict[str, int] = Field(default_factory=dict)
    effects_applied: dict[str, list[str]] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Trade
# ---------------------------------------------------------------------------

class TradeResult(BaseModel):
    accepted: bool
    reason: str
    items_transferred_to_player: list[ItemInstance] = Field(default_factory=list)
    items_transferred_to_npc: list[ItemInstance] = Field(default_factory=list)
