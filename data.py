"""
data.py

World content schemas — loaded from JSON files at startup.
These define things that exist in the world: items, abilities, classes,
creatures, races, backgrounds. No runtime state lives here.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, Optional, Union
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ItemType(str, Enum):
    WEAPON = "weapon"
    ARMOR = "armor"
    SHIELD = "shield"
    AMMUNITION = "ammunition"
    POTION = "potion"
    POISON = "poison"
    FOOD = "food"
    SCROLL = "scroll"
    WAND = "wand"
    ROD = "rod"
    STAFF = "staff"
    RING = "ring"
    WONDROUS = "wondrous"
    TOOL = "tool"
    INSTRUMENT = "instrument"
    ADVENTURING_GEAR = "adventuring_gear"
    CONTAINER = "container"
    QUEST = "quest"


class Rarity(str, Enum):
    MUNDANE = "mundane"
    COMMON = "common"
    UNCOMMON = "uncommon"
    RARE = "rare"
    VERY_RARE = "very_rare"
    LEGENDARY = "legendary"
    ARTIFACT = "artifact"


class DamageType(str, Enum):
    SLASHING = "slashing"
    PIERCING = "piercing"
    BLUDGEONING = "bludgeoning"
    FIRE = "fire"
    COLD = "cold"
    LIGHTNING = "lightning"
    THUNDER = "thunder"
    POISON = "poison"
    ACID = "acid"
    PSYCHIC = "psychic"
    RADIANT = "radiant"
    NECROTIC = "necrotic"
    FORCE = "force"


class WeaponCategory(str, Enum):
    SIMPLE = "simple"
    MARTIAL = "martial"
    IMPROVISED = "improvised"


class WeaponType(str, Enum):
    MELEE = "melee"
    RANGED = "ranged"


class ArmorType(str, Enum):
    LIGHT = "light"
    MEDIUM = "medium"
    HEAVY = "heavy"
    SHIELD = "shield"


class ActivationType(str, Enum):
    PASSIVE = "passive"
    COMMAND_WORD = "command_word"
    ACTION = "action"
    BONUS_ACTION = "bonus_action"
    REACTION = "reaction"
    SPECIAL = "special"


class RechargeOn(str, Enum):
    NONE = "none"
    DAWN = "dawn"
    SHORT_REST = "short_rest"
    LONG_REST = "long_rest"
    DAILY = "daily"
    WEEKLY = "weekly"


class ToolCategory(str, Enum):
    ARTISAN = "artisan"
    GAMING_SET = "gaming_set"
    MUSICAL_INSTRUMENT = "musical_instrument"
    THIEVES_TOOLS = "thieves_tools"
    NAVIGATORS_TOOLS = "navigators_tools"
    HERBALISM_KIT = "herbalism_kit"
    POISONERS_KIT = "poisoners_kit"
    DISGUISE_KIT = "disguise_kit"
    FORGERY_KIT = "forgery_kit"
    OTHER = "other"


class ConsumptionType(str, Enum):
    DRINK = "drink"
    APPLY = "apply"
    EAT = "eat"
    INHALE = "inhale"
    CAST = "cast"


class AbilityType(str, Enum):
    SPELL = "spell"
    PASSIVE = "passive"
    ACTIVE = "active"
    LEGENDARY = "legendary"


class ActionCost(str, Enum):
    ACTION = "action"
    BONUS_ACTION = "bonus_action"
    REACTION = "reaction"
    FREE = "free"
    LEGENDARY = "legendary"


class TargetType(str, Enum):
    SELF = "self"
    TOUCH = "touch"
    SINGLE = "single"
    MULTIPLE = "multiple"
    AREA_SPHERE = "area_sphere"
    AREA_CUBE = "area_cube"
    AREA_CONE = "area_cone"
    AREA_LINE = "area_line"


class SpellSchool(str, Enum):
    ABJURATION = "abjuration"
    CONJURATION = "conjuration"
    DIVINATION = "divination"
    ENCHANTMENT = "enchantment"
    EVOCATION = "evocation"
    ILLUSION = "illusion"
    NECROMANCY = "necromancy"
    TRANSMUTATION = "transmutation"


# ---------------------------------------------------------------------------
# Effect system
# ---------------------------------------------------------------------------

class HealEffect(BaseModel):
    effect_type: Literal["heal"] = "heal"
    dice: str
    bonus: int = 0
    max_hp_only: bool = False


class DamageEffect(BaseModel):
    effect_type: Literal["damage"] = "damage"
    dice: str
    damage_type: DamageType
    save_ability: Optional[str] = None
    save_dc: Optional[int] = None
    half_on_save: bool = True


class ApplyConditionEffect(BaseModel):
    effect_type: Literal["apply_condition"] = "apply_condition"
    condition: str
    duration_turns: Optional[int] = None
    save_ability: Optional[str] = None
    save_dc: Optional[int] = None


class RemoveConditionEffect(BaseModel):
    effect_type: Literal["remove_condition"] = "remove_condition"
    condition: str


class ModifyStatEffect(BaseModel):
    effect_type: Literal["modify_stat"] = "modify_stat"
    stat: str
    modifier: int
    set_value: Optional[int] = None


class GrantResistanceEffect(BaseModel):
    effect_type: Literal["grant_resistance"] = "grant_resistance"
    damage_type: str


class GrantImmunityEffect(BaseModel):
    effect_type: Literal["grant_immunity"] = "grant_immunity"
    damage_type: Optional[str] = None
    condition: Optional[str] = None


class CastSpellEffect(BaseModel):
    effect_type: Literal["cast_spell"] = "cast_spell"
    spell_id: str
    cast_at_level: int = 0
    dc_override: Optional[int] = None


class RestoreResourceEffect(BaseModel):
    effect_type: Literal["restore_resource"] = "restore_resource"
    resource: str
    amount: int


class GrantAdvantageEffect(BaseModel):
    effect_type: Literal["grant_advantage"] = "grant_advantage"
    on: str


class GrantProficiencyEffect(BaseModel):
    effect_type: Literal["grant_proficiency"] = "grant_proficiency"
    proficiency_type: str
    proficiency_level: str = "proficient"


Effect = Annotated[
    Union[
        HealEffect,
        DamageEffect,
        ApplyConditionEffect,
        RemoveConditionEffect,
        ModifyStatEffect,
        GrantResistanceEffect,
        GrantImmunityEffect,
        CastSpellEffect,
        RestoreResourceEffect,
        GrantAdvantageEffect,
        GrantProficiencyEffect,
    ],
    Field(discriminator="effect_type"),
]


# ---------------------------------------------------------------------------
# Item property blocks
# ---------------------------------------------------------------------------

class WeaponProperties(BaseModel):
    category: WeaponCategory = WeaponCategory.SIMPLE
    weapon_type: WeaponType = WeaponType.MELEE
    damage_dice: str
    damage_type: DamageType
    versatile_damage: Optional[str] = None
    attack_bonus: int = 0
    damage_bonus: int = 0
    range_normal: Optional[int] = None
    range_max: Optional[int] = None
    properties: list[str] = Field(default_factory=list)
    silvered: bool = False
    adamantine: bool = False


class ArmorProperties(BaseModel):
    armor_type: ArmorType
    base_ac: int
    ac_bonus: int = 0
    max_dex_bonus: Optional[int] = None
    strength_requirement: int = 0
    stealth_disadvantage: bool = False
    don_time_minutes: int = 1
    doff_time_minutes: int = 1


class AmmunitionProperties(BaseModel):
    compatible_weapon_types: list[str] = Field(default_factory=list)
    attack_bonus: int = 0
    damage_bonus: int = 0
    silvered: bool = False
    quantity_per_purchase: int = 20


class ConsumableProperties(BaseModel):
    consumption_type: ConsumptionType = ConsumptionType.DRINK
    charges: int = 1
    effects: list[Effect] = Field(default_factory=list)
    addiction_risk: bool = False


class ScrollProperties(BaseModel):
    spell_id: str
    spell_level: int
    casting_ability: str = "intelligence"
    save_dc: int = 10
    attack_bonus: int = 2
    arcana_check_dc: Optional[int] = None


class ToolProperties(BaseModel):
    tool_category: ToolCategory = ToolCategory.OTHER
    associated_skills: list[str] = Field(default_factory=list)


class InstrumentProperties(BaseModel):
    instrument_type: str
    associated_skills: list[str] = Field(default_factory=list)


class ContainerProperties(BaseModel):
    capacity_lb: Optional[float] = None
    capacity_ft3: Optional[float] = None
    extradimensional: bool = False


class MagicItemProperties(BaseModel):
    activation_type: ActivationType = ActivationType.PASSIVE
    charges: Optional[int] = None
    charges_max: Optional[int] = None
    recharge_on: RechargeOn = RechargeOn.NONE
    recharge_amount: Optional[str] = None
    destroy_on_last_charge: bool = False
    effects_on_equip: list[Effect] = Field(default_factory=list)
    effects_on_use: list[Effect] = Field(default_factory=list)
    concentration_required: bool = False


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------

class ItemBase(BaseModel):
    item_id: str
    name: str
    item_type: Optional[ItemType] = None
    description: str = ""
    weight: float = 0.0
    value_gp: float = 0.0
    rarity: Optional[Rarity] = None
    magical: bool = False
    requires_attunement: bool = False
    attunement_requirements: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    weapon_properties: Optional[WeaponProperties] = None
    armor_properties: Optional[ArmorProperties] = None
    ammunition_properties: Optional[AmmunitionProperties] = None
    consumable_properties: Optional[ConsumableProperties] = None
    scroll_properties: Optional[ScrollProperties] = None
    tool_properties: Optional[ToolProperties] = None
    instrument_properties: Optional[InstrumentProperties] = None
    container_properties: Optional[ContainerProperties] = None
    magic_properties: Optional[MagicItemProperties] = None


class ItemInstance(BaseModel):
    instance_id: str
    item_base: ItemBase
    quantity: int = 1
    attuned: bool = False
    charges: Optional[int] = None
    custom_name: Optional[str] = None
    condition: str = "normal"


class EquipmentSlots(BaseModel):
    main_hand: Optional[ItemInstance] = None
    off_hand: Optional[ItemInstance] = None
    armor: Optional[ItemInstance] = None
    helmet: Optional[ItemInstance] = None
    boots: Optional[ItemInstance] = None
    gloves: Optional[ItemInstance] = None
    ring_1: Optional[ItemInstance] = None
    ring_2: Optional[ItemInstance] = None
    amulet: Optional[ItemInstance] = None
    back: Optional[ItemInstance] = None


# ---------------------------------------------------------------------------
# Feature — shared by character sheets and all definition types
# ---------------------------------------------------------------------------

class Feature(BaseModel):
    feature_id: str
    name: str
    source: str
    description: str


# ---------------------------------------------------------------------------
# Ability / spell definitions
# ---------------------------------------------------------------------------

class SpellComponents(BaseModel):
    verbal: bool = False
    somatic: bool = False
    material: bool = False
    material_description: Optional[str] = None
    material_cost_gp: Optional[float] = None
    consumed_on_cast: bool = False


class EffectsByLevel(BaseModel):
    slot_level: int   # 0 = base/cantrip
    effects: list[Effect] = Field(default_factory=list)


class AbilityDefinition(BaseModel):
    ability_id: str
    name: str
    description: str
    ability_type: Optional[AbilityType] = None
    action_cost: Optional[ActionCost] = None

    target_type: Optional[TargetType] = None
    range_ft: Optional[int] = None      # None = self or touch
    range_max_ft: Optional[int] = None  # for weapons with falloff
    area_ft: Optional[int] = None       # radius, length, or width depending on shape

    duration: str = "instantaneous"
    concentration: bool = False

    effects_by_level: list[EffectsByLevel] = Field(default_factory=list)

    # Spell-only fields — null for non-spells
    spell_school: Optional[SpellSchool] = None
    spell_components: Optional[SpellComponents] = None
    ritual: bool = False

    # Reaction-only — describes what triggers this ability
    reaction_trigger: Optional[str] = None

    # References class_ids that have access to this ability
    spell_lists: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Class / subclass / background definitions
# ---------------------------------------------------------------------------

class ProficiencyGrant(BaseModel):
    type: str
    choose: int   # 0 = all granted; >0 = player picks this many


class AbilityScoreIncrease(BaseModel):
    ability: str   # specific ability name, or "choice"
    amount: int


class ResourceGrant(BaseModel):
    resource_id: str
    name: str
    current: int
    max: int
    die: Optional[str] = None
    recharge_on: str


class ResourceUpdate(BaseModel):
    resource_id: str
    max: Optional[int] = None
    die: Optional[str] = None


class LevelProgression(BaseModel):
    level: int
    proficiency_bonus: int = 0
    features: list[Feature] = Field(default_factory=list)
    resource_grants: list[ResourceGrant] = Field(default_factory=list)
    resource_updates: list[ResourceUpdate] = Field(default_factory=list)
    spell_slots: Optional[dict[int, int]] = None
    cantrips_knowable: Optional[int] = None
    spells_knowable: Optional[int] = None


class ClassDefinition(BaseModel):
    class_id: str
    name: str
    description: str
    hit_die: int
    proficiency_grants: list[ProficiencyGrant] = Field(default_factory=list)
    starting_equipment: list[str] = Field(default_factory=list)
    starting_equipment_currency_gp: int = 0
    spellcasting_ability: str = ""
    spell_prepare_style: str = ""
    level_progression: list[LevelProgression]
    subclass_level: int


class SubclassDefinition(BaseModel):
    subclass_id: str
    name: str
    parent_class_id: str
    granted_at_level: int
    description: str
    spellcasting_ability: str = ""
    spell_prepare_style: str = ""
    level_progression: list[LevelProgression] = Field(default_factory=list)


class BackgroundDefinition(BaseModel):
    background_id: str
    name: str
    description: str
    proficiency_grants: list[ProficiencyGrant] = Field(default_factory=list)
    bonus_languages: int = 0
    starting_equipment: list[str] = Field(default_factory=list)
    starting_currency_gp: int = 0
    features: list[Feature] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Creature / race definitions
# ---------------------------------------------------------------------------

class SensesDefinition(BaseModel):
    passive_perception: int = 0
    darkvision_ft: int = 0
    blindsight_ft: int = 0
    tremorsense_ft: int = 0
    truesight_ft: int = 0


class SpeedDefinition(BaseModel):
    walk: int = 0
    swim: int = 0
    climb: int = 0
    fly: int = 0
    burrow: int = 0


class RaceDefinition(BaseModel):
    race_id: str
    name: str
    parent_creature_id: str
    description: str
    ability_score_increases: list[AbilityScoreIncrease] = Field(default_factory=list)
    senses_override: dict[str, int] = Field(default_factory=dict)
    speed_override: dict[str, int] = Field(default_factory=dict)
    traits: list[Feature] = Field(default_factory=list)
    proficiency_grants: list[ProficiencyGrant] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    bonus_languages: int = 0
    resistances: list[str] = Field(default_factory=list)
    immunities: list[str] = Field(default_factory=list)


class CreatureActionDamage(BaseModel):
    damage_dice_count: int
    damage_die: int
    damage_flat_bonus: int = 0
    damage_type: str


class CreatureAction(BaseModel):
    action_id: str
    name: str
    action_cost: str
    attack_type: Optional[str] = None
    reach_ft: Optional[int] = None
    range_normal_ft: Optional[int] = None
    range_max_ft: Optional[int] = None
    target_count: int = 1
    attack_bonus: Optional[int] = None
    save_ability: Optional[str] = None
    save_dc: Optional[int] = None
    on_hit: list[CreatureActionDamage] = Field(default_factory=list)
    description: str


class CreatureReaction(BaseModel):
    action_id: str
    name: str
    trigger: str
    attack_bonus: Optional[int] = None
    save_ability: Optional[str] = None
    save_dc: Optional[int] = None
    on_hit: list[CreatureActionDamage] = Field(default_factory=list)
    description: str


class AbilityScores(BaseModel):
    strength: int
    dexterity: int
    constitution: int
    intelligence: int
    wisdom: int
    charisma: int

    def modifier(self, score: int) -> int:
        return (score - 10) // 2


class CreatureDefinition(BaseModel):
    creature_id: str
    name: str
    description: str
    creature_type: str
    size: str
    ability_scores: AbilityScores
    armor_class: int
    armor_type: str
    hp_dice_count: int
    hp_die: int
    hp_flat_bonus: int
    hp_average: int
    speed: SpeedDefinition = Field(default_factory=SpeedDefinition)
    senses: SensesDefinition = Field(default_factory=SensesDefinition)
    resistances: list[str] = Field(default_factory=list)
    immunities: list[str] = Field(default_factory=list)
    vulnerabilities: list[str] = Field(default_factory=list)
    condition_immunities: list[str] = Field(default_factory=list)
    proficiency_bonus: int = 0
    traits: list[Feature] = Field(default_factory=list)
    actions: list[CreatureAction] = Field(default_factory=list)
    bonus_actions: list[CreatureAction] = Field(default_factory=list)
    reactions: list[CreatureReaction] = Field(default_factory=list)
    legendary_actions: list[CreatureAction] = Field(default_factory=list)
    legendary_action_count: int = 0
    xp_value: int = 0
    challenge_rating: str = ""
