"""
character.py

Runtime character state models.

These are distinct from the definition schemas in schemas.py.
Definitions describe what a class/creature/item *is*.
These describe what a character *currently is* during play.

Imports shared primitives (AbilityScores, Feature, ItemInstance, etc.)
from schemas.py.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field

from bitzantium_schemas.data import (
    AbilityScores,
    EquipmentSlots,
    Feature,
    ItemInstance,
    DamageType,
)
from bitzantium_schemas.schemas import (
    ActiveEffect,
    Condition,
)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ProficiencyLevel(str, Enum):
    NONE = "none"
    HALF = "half"
    PROFICIENT = "proficient"
    EXPERT = "expert"


# ---------------------------------------------------------------------------
# Character sheet supporting models
# ---------------------------------------------------------------------------

class ClassEntry(BaseModel):
    class_id: str
    subclass_id: str = ""
    level: int
    hit_die: str


class HitDicePool(BaseModel):
    die: str
    total: int
    remaining: int


class SkillProficiency(BaseModel):
    skill: str
    proficiency_level: ProficiencyLevel = ProficiencyLevel.NONE


class ToolProficiency(BaseModel):
    tool: str
    proficiency_level: ProficiencyLevel = ProficiencyLevel.NONE


class DeathSaves(BaseModel):
    successes: int = 0
    failures: int = 0
    stable: bool = False


class Senses(BaseModel):
    passive_perception: int = 0
    darkvision_ft: int = 0
    blindsight_ft: int = 0
    tremorsense_ft: int = 0
    truesight_ft: int = 0


class Currency(BaseModel):
    cp: int = 0
    sp: int = 0
    ep: int = 0
    gp: int = 0
    pp: int = 0


class SpellSlotEntry(BaseModel):
    total: int = 0
    remaining: int = 0


class SpellReference(BaseModel):
    """
    Runtime spell state on the character sheet.
    Full spell definition is looked up from the abilities store by spell_id.
    """
    spell_id: str
    prepared: bool = False
    always_prepared: bool = False


class Feat(BaseModel):
    feat_id: str
    name: str
    description: str


class ClassResource(BaseModel):
    resource_id: str
    name: str
    current: int
    max: int
    die: Optional[str] = None
    recharge_on: str


# ---------------------------------------------------------------------------
# Player sheet — full runtime character state
# ---------------------------------------------------------------------------

class PlayerSheet(BaseModel):
    entity_id: str
    name: str
    creature_id: str = ""       # references CreatureDefinition (species)
    race_id: str = ""           # references RaceDefinition, if applicable
    description: str = ""
    background_id: str = ""     # references BackgroundDefinition
    alignment: str = ""
    inspiration: bool = False

    classes: list[ClassEntry] = Field(default_factory=list)
    level: int = 0
    experience: int = 0
    proficiency_bonus: int = 0

    hp_current: int = 0
    hp_max: int = 0
    hp_temp: int = 0
    hit_dice: list[HitDicePool] = Field(default_factory=list)
    armor_class: int = 0
    speed: int = 0
    initiative_bonus: int = 0

    death_saves: DeathSaves = Field(default_factory=DeathSaves)

    ability_scores: AbilityScores = Field(default_factory=lambda: AbilityScores(
        strength=0, dexterity=0, constitution=0,
        intelligence=0, wisdom=0, charisma=0
    ))
    saving_throw_proficiencies: list[str] = Field(default_factory=list)
    skill_proficiencies: list[SkillProficiency] = Field(default_factory=list)
    tool_proficiencies: list[ToolProficiency] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)

    senses: Senses = Field(default_factory=Senses)

    conditions: list[Condition] = Field(default_factory=list)
    active_effects: list[ActiveEffect] = Field(default_factory=list)
    concentration_effect_id: Optional[str] = None
    exhaustion_level: int = 0

    features: list[Feature] = Field(default_factory=list)
    feats: list[Feat] = Field(default_factory=list)
    class_resources: list[ClassResource] = Field(default_factory=list)

    equipment: EquipmentSlots = Field(default_factory=EquipmentSlots)
    inventory: list[ItemInstance] = Field(default_factory=list)
    currency: Currency = Field(default_factory=Currency)
    carrying_capacity: int = 0

    resistances: list[DamageType] = Field(default_factory=list)
    immunities: list[str] = Field(default_factory=list)
    vulnerabilities: list[DamageType] = Field(default_factory=list)

    spellcasting_ability: str = ""
    spell_save_dc: int = 0
    spell_attack_bonus: int = 0
    spell_slots: dict[int, SpellSlotEntry] = Field(default_factory=dict)
    spells: list[SpellReference] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Action economy — turn-scoped, lives alongside the sheet
# ---------------------------------------------------------------------------

class ActionEconomy(BaseModel):
    action_spent: bool = False
    bonus_action_spent: bool = False
    reaction_spent: bool = False
    movement_used: int = 0


class CharacterState(BaseModel):
    sheet: PlayerSheet
    economy: ActionEconomy = Field(default_factory=ActionEconomy)
