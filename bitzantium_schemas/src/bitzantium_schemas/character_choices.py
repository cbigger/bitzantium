"""
character_choices.py

Compact input model for programmatic character creation.
An agent submits decisions — the server builds and validates the full sheet.
"""

from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field


class CharacterChoices(BaseModel):
    """
    Everything an agent needs to decide to create a valid character.
    All derived values (HP, AC, proficiency bonus, features, resources, etc.)
    are computed server-side from these choices + the RealmTemplate data.
    """

    # Identity
    name: str
    description: str = ""
    alignment: str

    # Species & heritage
    creature_id: str
    race_id: str = ""

    # Background
    background_id: str

    # Class
    class_id: str
    subclass_id: str = ""
    level: int = Field(ge=1, le=20)

    # Ability scores — base values BEFORE racial bonuses
    ability_method: Literal["standard_array", "point_buy", "manual"]
    ability_assignments: dict[str, int]

    # For races with "choice" ASIs, which abilities to apply them to.
    # Each entry is an ability name (e.g. "strength"). Order matches the
    # order of "choice" entries in the race's ability_score_increases.
    racial_asi_choices: list[str] = Field(default_factory=list)

    # Skills chosen from class/background pools (beyond auto-granted ones)
    skill_choices: list[str] = Field(default_factory=list)

    # Bonus languages chosen (beyond species/race/background defaults)
    language_choices: list[str] = Field(default_factory=list)

    # Spellcasting — spell_ids from the class spell list
    cantrip_choices: list[str] = Field(default_factory=list)
    spell_choices: list[str] = Field(default_factory=list)
