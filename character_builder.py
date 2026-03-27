"""
character_builder.py
====================
Programmatic character creation with full class-rule validation.

Two public functions:

    get_creation_options()
        Returns a dict describing every available choice (classes, species,
        backgrounds, etc.) with constraints, so an agent can make informed
        decisions without knowing the rule book.

    build_character(choices: CharacterChoices) -> dict
        Validates the choices against class/race/background rules and, if
        valid, returns {"character_state": <CharacterState dict>}.
        On failure returns {"errors": [<str>, ...]}.

Both require that loader.load_all() has been called first.
"""

from __future__ import annotations

import uuid
from typing import Optional

from bitzantium_schemas.character import (
    ActionEconomy,
    CharacterState,
    ClassEntry,
    ClassResource,
    HitDicePool,
    PlayerSheet,
    ProficiencyLevel,
    SkillProficiency,
    SpellReference,
)
from bitzantium_schemas.character_choices import CharacterChoices
from bitzantium_schemas.data import (
    ClassDefinition,
    ItemBase,
    SubclassDefinition,
    RaceDefinition,
)

import loader

# ---------------------------------------------------------------------------
# Constants (mirrored from fabricate.py — canonical D&D 5e values)
# ---------------------------------------------------------------------------

ABILITIES = ["strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"]

STANDARD_ARRAY = [15, 14, 13, 12, 10, 8]

POINT_BUY_COST = {8: 0, 9: 1, 10: 2, 11: 3, 12: 4, 13: 5, 14: 7, 15: 9}
POINT_BUY_BUDGET = 27

ALL_SKILLS = [
    "acrobatics", "animal_handling", "arcana", "athletics", "deception",
    "history", "insight", "intimidation", "investigation", "medicine",
    "nature", "perception", "performance", "persuasion", "religion",
    "sleight_of_hand", "stealth", "survival",
]

SKILL_ABILITY = {
    "acrobatics": "dexterity", "animal_handling": "wisdom", "arcana": "intelligence",
    "athletics": "strength", "deception": "charisma", "history": "intelligence",
    "insight": "wisdom", "intimidation": "charisma", "investigation": "intelligence",
    "medicine": "wisdom", "nature": "intelligence", "perception": "wisdom",
    "performance": "charisma", "persuasion": "charisma", "religion": "intelligence",
    "sleight_of_hand": "dexterity", "stealth": "dexterity", "survival": "wisdom",
}

ALIGNMENTS = [
    "Lawful Good", "Neutral Good", "Chaotic Good",
    "Lawful Neutral", "True Neutral", "Chaotic Neutral",
    "Lawful Evil", "Neutral Evil", "Chaotic Evil",
]

COMMON_LANGUAGES = [
    "common", "elvish", "dwarvish", "halfling", "gnomish", "orcish",
    "goblin", "draconic", "infernal", "celestial", "sylvan",
    "undercommon", "abyssal", "deep_speech", "primordial", "giant",
]


# ---------------------------------------------------------------------------
# Internal helpers (same logic as fabricate.py)
# ---------------------------------------------------------------------------

def _ability_modifier(score: int) -> int:
    return (score - 10) // 2


def _proficiency_bonus_for_level(level: int) -> int:
    return max(2, (level - 1) // 4 + 2)


def _parse_grants(grants) -> dict:
    result = {
        "skill_auto": [], "skill_choose": [],
        "saving_throw": [],
        "tool_auto": [], "tool_choose": [],
    }
    for g in grants:
        raw_type: str = g.type
        choose_n: int = g.choose
        if ":" not in raw_type:
            continue
        category, _, pool_str = raw_type.partition(":")
        pool = [p.strip() for p in pool_str.split("|") if p.strip()]
        if category == "skill":
            if choose_n == 0:
                result["skill_auto"].extend(pool)
            else:
                result["skill_choose"].append((choose_n, pool))
        elif category == "saving_throw":
            result["saving_throw"].extend(pool)
        elif category == "tool":
            if choose_n == 0:
                result["tool_auto"].extend(pool)
            else:
                result["tool_choose"].append((choose_n, pool))
    return result


def _aggregate_level_progression(class_def: ClassDefinition, level: int) -> dict:
    result = {
        "features": [], "resource_grants": [], "resource_updates": [],
        "spell_slots": None, "cantrips_knowable": None, "spells_knowable": None,
        "proficiency_bonus": _proficiency_bonus_for_level(level),
    }
    for entry in class_def.level_progression:
        if entry.level > level:
            break
        result["features"].extend(entry.features)
        result["resource_grants"].extend(entry.resource_grants)
        result["resource_updates"].extend(entry.resource_updates)
        if entry.spell_slots is not None:
            result["spell_slots"] = entry.spell_slots
        if entry.cantrips_knowable is not None:
            result["cantrips_knowable"] = entry.cantrips_knowable
        if entry.spells_knowable is not None:
            result["spells_knowable"] = entry.spells_knowable
        if entry.proficiency_bonus:
            result["proficiency_bonus"] = entry.proficiency_bonus
    return result


def _build_class_resources(
    class_def: ClassDefinition,
    subclass_def: Optional[SubclassDefinition],
    level: int,
) -> list[ClassResource]:
    grants: dict[str, dict] = {}
    updates: list = []
    sources = [class_def.level_progression] + (
        [subclass_def.level_progression] if subclass_def else []
    )
    for progression in sources:
        for entry in progression:
            if entry.level > level:
                continue
            for rg in entry.resource_grants:
                grants[rg.resource_id] = {
                    "resource_id": rg.resource_id, "name": rg.name,
                    "current": rg.current, "max": rg.max,
                    "die": rg.die, "recharge_on": rg.recharge_on,
                }
            updates.extend(entry.resource_updates)
    for ru in updates:
        if ru.resource_id in grants:
            if ru.max is not None:
                grants[ru.resource_id]["max"] = ru.max
                grants[ru.resource_id]["current"] = ru.max
            if ru.die is not None:
                grants[ru.resource_id]["die"] = ru.die
    return [ClassResource(**v) for v in grants.values()]


def _calculate_hp(
    class_def: ClassDefinition,
    con_score: int,
    level: int,
    race_def: Optional[RaceDefinition],
) -> int:
    con_mod = _ability_modifier(con_score)
    hp = class_def.hit_die + con_mod
    hp += ((class_def.hit_die // 2 + 1) + con_mod) * (level - 1)
    if race_def:
        for trait in race_def.traits:
            if "hit point maximum increases by 1" in trait.description.lower():
                hp += level
    return max(1, hp)


def _item_to_instance(item: ItemBase) -> dict:
    return {
        "instance_id": f"inst_{item.item_id}_{uuid.uuid4().hex[:6]}",
        "item_base": item.model_dump(),
        "quantity": 1, "attuned": False,
        "charges": item.magic_properties.charges if item.magic_properties else None,
        "custom_name": None, "condition": "normal",
    }


def _is_cantrip(ability) -> bool:
    return bool(ability.effects_by_level) and all(
        e.slot_level == 0 for e in ability.effects_by_level
    )


# ---------------------------------------------------------------------------
# get_creation_options
# ---------------------------------------------------------------------------

def get_creation_options() -> dict:
    """
    Return a structured dict of every available character creation choice,
    with enough detail for an agent to make valid decisions.
    """
    options: dict = {}

    # Alignments
    options["alignments"] = ALIGNMENTS

    # Ability score methods
    options["ability_methods"] = {
        "standard_array": {
            "description": "Assign [15, 14, 13, 12, 10, 8] to six abilities. Each value used exactly once.",
            "values": STANDARD_ARRAY,
        },
        "point_buy": {
            "description": f"Budget of {POINT_BUY_BUDGET} points. Scores range 8-15 before racial bonuses.",
            "cost_table": POINT_BUY_COST,
            "budget": POINT_BUY_BUDGET,
        },
        "manual": {
            "description": "Set each score directly (1-30). Use when the DM has you roll dice.",
        },
    }
    options["abilities"] = ABILITIES

    # Species
    species = []
    for cid in sorted(loader.list_sapient_creatures()):
        creature = loader.get_creature(cid)
        if not creature:
            continue
        races_for_creature = []
        for rid in sorted(loader.list_races()):
            race = loader.get_race(rid)
            if race and race.parent_creature_id == cid:
                choice_asi_count = sum(
                    1 for asi in race.ability_score_increases if asi.ability == "choice"
                )
                races_for_creature.append({
                    "race_id": race.race_id,
                    "name": race.name,
                    "description": race.description,
                    "ability_score_increases": [
                        {"ability": asi.ability, "amount": asi.amount}
                        for asi in race.ability_score_increases
                    ],
                    "choice_asi_count": choice_asi_count,
                    "languages": race.languages,
                    "bonus_languages": race.bonus_languages,
                    "traits": [{"name": t.name, "description": t.description} for t in race.traits],
                    "resistances": race.resistances,
                })
        species.append({
            "creature_id": creature.creature_id,
            "name": creature.name,
            "description": creature.description,
            "speed": creature.speed.walk,
            "darkvision_ft": creature.senses.darkvision_ft,
            "languages": creature.languages,
            "bonus_languages": creature.bonus_languages,
            "traits": [{"name": t.name, "description": t.description} for t in creature.traits],
            "races": races_for_creature,
        })
    options["species"] = species

    # Backgrounds
    backgrounds = []
    for bid in sorted(loader.list_backgrounds()):
        bg = loader.get_background(bid)
        if not bg:
            continue
        bg_grants = _parse_grants(bg.proficiency_grants)
        backgrounds.append({
            "background_id": bg.background_id,
            "name": bg.name,
            "description": bg.description,
            "skill_proficiencies_auto": bg_grants["skill_auto"],
            "skill_proficiencies_choose": [
                {"count": c, "from": p} for c, p in bg_grants["skill_choose"]
            ],
            "tool_proficiencies_auto": bg_grants["tool_auto"],
            "bonus_languages": bg.bonus_languages,
            "features": [{"name": f.name, "description": f.description} for f in bg.features],
        })
    options["backgrounds"] = backgrounds

    # Classes
    classes = []
    for cid in sorted(loader.list_classes()):
        cls = loader.get_class(cid)
        if not cls:
            continue
        cls_grants = _parse_grants(cls.proficiency_grants)

        # Subclasses for this class
        subclasses = []
        for sid in sorted(loader.list_subclasses()):
            sub = loader.get_subclass(sid)
            if sub and sub.parent_class_id == cid:
                subclasses.append({
                    "subclass_id": sub.subclass_id,
                    "name": sub.name,
                    "description": sub.description,
                    "granted_at_level": sub.granted_at_level,
                })

        # Spell list for this class
        class_spells = []
        for aid in sorted(loader.list_abilities()):
            ability = loader.get_ability(aid)
            if ability and cid in ability.spell_lists:
                class_spells.append({
                    "spell_id": ability.ability_id,
                    "name": ability.name,
                    "is_cantrip": _is_cantrip(ability),
                    "description": ability.description,
                    "school": ability.spell_school.value if ability.spell_school else None,
                })

        classes.append({
            "class_id": cls.class_id,
            "name": cls.name,
            "description": cls.description,
            "hit_die": cls.hit_die,
            "spellcasting_ability": cls.spellcasting_ability or None,
            "spell_prepare_style": cls.spell_prepare_style or None,
            "subclass_level": cls.subclass_level,
            "saving_throws": cls_grants["saving_throw"],
            "skill_proficiencies_auto": cls_grants["skill_auto"],
            "skill_proficiencies_choose": [
                {"count": c, "from": p} for c, p in cls_grants["skill_choose"]
            ],
            "armor_proficiencies": [
                g.type for g in cls.proficiency_grants
                if g.type.startswith("armor:")
            ],
            "weapon_proficiencies": [
                g.type for g in cls.proficiency_grants
                if g.type.startswith("weapon:")
            ],
            "subclasses": subclasses,
            "spells": class_spells,
        })
    options["classes"] = classes

    options["all_skills"] = ALL_SKILLS
    options["common_languages"] = COMMON_LANGUAGES

    return options


# ---------------------------------------------------------------------------
# build_character
# ---------------------------------------------------------------------------

def build_character(choices: CharacterChoices) -> dict:
    """
    Validate choices and build a full CharacterState dict.

    Returns:
        {"character_state": <dict>}  on success
        {"errors": [<str>, ...]}     on validation failure
    """
    errors: list[str] = []

    # --- Look up definitions ---

    creature = loader.get_creature(choices.creature_id)
    if not creature:
        errors.append(f"Unknown creature_id: {choices.creature_id!r}")
    elif choices.creature_id not in loader.list_sapient_creatures():
        errors.append(f"Creature {choices.creature_id!r} is not a playable species")

    race: Optional[RaceDefinition] = None
    if choices.race_id:
        race = loader.get_race(choices.race_id)
        if not race:
            errors.append(f"Unknown race_id: {choices.race_id!r}")
        elif creature and race.parent_creature_id != choices.creature_id:
            errors.append(
                f"Race {choices.race_id!r} does not belong to creature {choices.creature_id!r}"
            )

    background = loader.get_background(choices.background_id)
    if not background:
        errors.append(f"Unknown background_id: {choices.background_id!r}")

    class_def = loader.get_class(choices.class_id)
    if not class_def:
        errors.append(f"Unknown class_id: {choices.class_id!r}")

    subclass_def: Optional[SubclassDefinition] = None
    if choices.subclass_id:
        subclass_def = loader.get_subclass(choices.subclass_id)
        if not subclass_def:
            errors.append(f"Unknown subclass_id: {choices.subclass_id!r}")
        elif class_def and subclass_def.parent_class_id != choices.class_id:
            errors.append(
                f"Subclass {choices.subclass_id!r} does not belong to class {choices.class_id!r}"
            )
        elif class_def and choices.level < class_def.subclass_level:
            errors.append(
                f"Subclass requires level {class_def.subclass_level}, but level is {choices.level}"
            )

    if choices.alignment not in ALIGNMENTS:
        errors.append(
            f"Invalid alignment: {choices.alignment!r}. Must be one of: {ALIGNMENTS}"
        )

    # Bail early if we can't look up definitions
    if errors:
        return {"errors": errors}

    # --- Validate ability scores ---

    assignments = choices.ability_assignments
    if sorted(assignments.keys()) != sorted(ABILITIES):
        errors.append(
            f"ability_assignments must have exactly these keys: {ABILITIES}. "
            f"Got: {sorted(assignments.keys())}"
        )
    else:
        if choices.ability_method == "standard_array":
            provided = sorted(assignments.values())
            expected = sorted(STANDARD_ARRAY)
            if provided != expected:
                errors.append(
                    f"Standard array requires values {expected}, got {provided}"
                )
        elif choices.ability_method == "point_buy":
            total_cost = 0
            for ability, score in assignments.items():
                if score < 8 or score > 15:
                    errors.append(
                        f"Point buy: {ability} score {score} out of range 8-15"
                    )
                elif score in POINT_BUY_COST:
                    total_cost += POINT_BUY_COST[score]
            if total_cost > POINT_BUY_BUDGET:
                errors.append(
                    f"Point buy: total cost {total_cost} exceeds budget of {POINT_BUY_BUDGET}"
                )
        else:  # manual
            for ability, score in assignments.items():
                if score < 1 or score > 30:
                    errors.append(f"Manual: {ability} score {score} out of range 1-30")

    # --- Validate racial ASI choices ---

    if race:
        choice_asis = [asi for asi in race.ability_score_increases if asi.ability == "choice"]
        if len(choices.racial_asi_choices) != len(choice_asis):
            errors.append(
                f"Race {race.race_id!r} has {len(choice_asis)} choice ASI(s), "
                f"but {len(choices.racial_asi_choices)} racial_asi_choices provided"
            )
        else:
            for chosen_ability in choices.racial_asi_choices:
                if chosen_ability not in ABILITIES:
                    errors.append(
                        f"Invalid racial ASI choice: {chosen_ability!r}. Must be one of: {ABILITIES}"
                    )

    if errors:
        return {"errors": errors}

    # --- Apply racial bonuses to ability scores ---

    scores = dict(assignments)
    if race:
        for asi in race.ability_score_increases:
            if asi.ability == "choice":
                # consume from racial_asi_choices in order
                pass  # handled below
            else:
                scores[asi.ability] = min(30, scores[asi.ability] + asi.amount)
        choice_idx = 0
        for asi in race.ability_score_increases:
            if asi.ability == "choice":
                target = choices.racial_asi_choices[choice_idx]
                scores[target] = min(30, scores[target] + asi.amount)
                choice_idx += 1

    # --- Level progression ---

    level_data = _aggregate_level_progression(class_def, choices.level)
    proficiency_bonus = level_data["proficiency_bonus"]

    # --- Validate skill choices ---

    class_grants = _parse_grants(class_def.proficiency_grants)
    bg_grants = _parse_grants(background.proficiency_grants)

    auto_skills = set(class_grants["skill_auto"] + bg_grants["skill_auto"])
    all_choose_pools = class_grants["skill_choose"] + bg_grants["skill_choose"]
    total_choices_needed = sum(count for count, _ in all_choose_pools)
    all_chooseable = set()
    for _, pool in all_choose_pools:
        all_chooseable.update(pool)
    all_chooseable -= auto_skills  # can't pick what's already granted

    if len(choices.skill_choices) != total_choices_needed:
        errors.append(
            f"Expected {total_choices_needed} skill choice(s), got {len(choices.skill_choices)}"
        )
    else:
        for skill in choices.skill_choices:
            if skill not in all_chooseable:
                errors.append(
                    f"Skill {skill!r} is not available to choose from. "
                    f"Available: {sorted(all_chooseable)}"
                )
        if len(set(choices.skill_choices)) != len(choices.skill_choices):
            errors.append("Duplicate skills in skill_choices")

    # Validate per-pool counts (skills must satisfy each pool's count)
    if not errors:
        remaining_choices = list(choices.skill_choices)
        for count, pool in all_choose_pools:
            available_in_pool = [s for s in remaining_choices if s in set(pool) - auto_skills]
            if len(available_in_pool) < count:
                errors.append(
                    f"Need {count} skill(s) from {sorted(set(pool) - auto_skills)}, "
                    f"but only {len(available_in_pool)} of your choices match"
                )
            else:
                # Consume the matched choices
                for s in available_in_pool[:count]:
                    remaining_choices.remove(s)

    # --- Validate language choices ---

    base_languages = list(creature.languages) if creature.languages else []
    if race and race.languages:
        for lang in race.languages:
            if lang not in base_languages:
                base_languages.append(lang)
    bonus_count = (
        creature.bonus_languages
        + (race.bonus_languages if race else 0)
        + background.bonus_languages
    )

    if len(choices.language_choices) != bonus_count:
        errors.append(
            f"Expected {bonus_count} bonus language choice(s), got {len(choices.language_choices)}"
        )
    else:
        for lang in choices.language_choices:
            if lang in base_languages:
                errors.append(f"Language {lang!r} is already a base language, cannot pick it as bonus")
            elif lang not in COMMON_LANGUAGES:
                errors.append(
                    f"Unknown language: {lang!r}. Available: {COMMON_LANGUAGES}"
                )

    # --- Validate spell choices ---

    spellcasting_ability = (
        subclass_def.spellcasting_ability
        if (subclass_def and subclass_def.spellcasting_ability)
        else class_def.spellcasting_ability
    )

    cantrips_knowable = level_data.get("cantrips_knowable") or 0
    spells_knowable = level_data.get("spells_knowable")
    is_prepared = class_def.spell_prepare_style == "prepare"

    class_spell_ids = set()
    class_cantrip_ids = set()
    for aid in loader.list_abilities():
        ability = loader.get_ability(aid)
        if ability and class_def.class_id in ability.spell_lists:
            if _is_cantrip(ability):
                class_cantrip_ids.add(ability.ability_id)
            else:
                class_spell_ids.add(ability.ability_id)

    if not spellcasting_ability:
        if choices.cantrip_choices:
            errors.append(f"Class {choices.class_id!r} has no spellcasting, but cantrip_choices provided")
        if choices.spell_choices:
            errors.append(f"Class {choices.class_id!r} has no spellcasting, but spell_choices provided")
    else:
        # Cantrips
        if len(choices.cantrip_choices) != cantrips_knowable:
            errors.append(
                f"Expected {cantrips_knowable} cantrip(s), got {len(choices.cantrip_choices)}"
            )
        for sid in choices.cantrip_choices:
            if sid not in class_cantrip_ids:
                errors.append(
                    f"Cantrip {sid!r} is not on the {choices.class_id} cantrip list"
                )

        # Leveled spells
        if is_prepared:
            sp_mod = _ability_modifier(scores[spellcasting_ability])
            prepare_count = max(1, sp_mod + choices.level)
            if len(choices.spell_choices) > prepare_count:
                errors.append(
                    f"Can prepare at most {prepare_count} spell(s) "
                    f"({spellcasting_ability.upper()} mod {sp_mod} + level {choices.level}), "
                    f"got {len(choices.spell_choices)}"
                )
        elif spells_knowable:
            if len(choices.spell_choices) > spells_knowable:
                errors.append(
                    f"Can know at most {spells_knowable} spell(s), got {len(choices.spell_choices)}"
                )

        for sid in choices.spell_choices:
            if sid not in class_spell_ids:
                errors.append(
                    f"Spell {sid!r} is not on the {choices.class_id} spell list"
                )

    if errors:
        return {"errors": errors}

    # --- Build the character sheet ---

    chosen_skills = auto_skills | set(choices.skill_choices)
    saving_throws = list(set(class_grants["saving_throw"]))

    wis_mod = _ability_modifier(scores["wisdom"])
    perc_bonus = proficiency_bonus if "perception" in chosen_skills else 0
    passive_perception = 10 + wis_mod + perc_bonus

    languages = list(base_languages) + list(choices.language_choices)
    if not languages:
        languages = ["common"]

    # Spells
    spell_refs: list[SpellReference] = []
    for sid in choices.cantrip_choices:
        spell_refs.append(SpellReference(spell_id=sid))
    for sid in choices.spell_choices:
        spell_refs.append(SpellReference(spell_id=sid, prepared=is_prepared))

    sp_mod = _ability_modifier(scores[spellcasting_ability]) if spellcasting_ability else 0
    spell_dc = (8 + proficiency_bonus + sp_mod) if spellcasting_ability else 0
    spell_attack = (proficiency_bonus + sp_mod) if spellcasting_ability else 0

    raw_slots = level_data.get("spell_slots") or {}
    spell_slots = {int(k): {"total": v, "remaining": v} for k, v in raw_slots.items() if v > 0}

    # Equipment
    all_item_ids = list(set(class_def.starting_equipment + background.starting_equipment))
    inventory: list[dict] = []
    for item_id in all_item_ids:
        item = loader.get_item(item_id)
        if item:
            inventory.append(_item_to_instance(item))

    starting_gp = class_def.starting_equipment_currency_gp + background.starting_currency_gp

    # HP & AC
    dex_mod = _ability_modifier(scores["dexterity"])
    hp_max = _calculate_hp(class_def, scores["constitution"], choices.level, race)

    base_ac = 10 + dex_mod
    for inst in inventory:
        ap = inst["item_base"].get("armor_properties")
        if ap:
            max_dex = ap.get("max_dex_bonus")
            effective_dex = min(dex_mod, max_dex) if max_dex is not None else dex_mod
            base_ac = max(base_ac, ap["base_ac"] + effective_dex + ap.get("ac_bonus", 0))

    # Speed / senses
    speed = creature.speed.walk
    darkvision = creature.senses.darkvision_ft
    if race:
        if race.speed_override.get("walk"):
            speed = race.speed_override["walk"]
        if race.senses_override.get("darkvision_ft"):
            darkvision = race.senses_override["darkvision_ft"]

    # Resistances / immunities
    resistances = list(creature.resistances) + (list(race.resistances) if race else [])
    immunities = list(creature.immunities) + (list(race.immunities) if race else [])

    # Features
    all_features = list(level_data["features"])
    if subclass_def:
        for entry in subclass_def.level_progression:
            if entry.level <= choices.level:
                all_features.extend(entry.features)
    if race:
        all_features.extend(race.traits)
    all_features.extend(creature.traits)

    # Class resources & hit dice
    class_resources = _build_class_resources(class_def, subclass_def, choices.level)
    hit_dice = [HitDicePool(die=f"d{class_def.hit_die}", total=choices.level, remaining=choices.level)]

    # Skill proficiencies (full list for sheet)
    skill_proficiencies = [
        SkillProficiency(
            skill=s,
            proficiency_level=(
                ProficiencyLevel.PROFICIENT if s in chosen_skills
                else ProficiencyLevel.NONE
            ),
        )
        for s in ALL_SKILLS
    ]

    # Assemble sheet
    sheet = PlayerSheet(
        entity_id=f"char_{uuid.uuid4().hex[:12]}",
        name=choices.name,
        creature_id=creature.creature_id,
        race_id=race.race_id if race else "",
        description=choices.description,
        background_id=background.background_id,
        alignment=choices.alignment,
        inspiration=False,
        classes=[ClassEntry(
            class_id=class_def.class_id,
            subclass_id=subclass_def.subclass_id if subclass_def else "",
            level=choices.level,
            hit_die=f"d{class_def.hit_die}",
        )],
        level=choices.level,
        experience=0,
        proficiency_bonus=proficiency_bonus,
        hp_current=hp_max,
        hp_max=hp_max,
        hp_temp=0,
        hit_dice=hit_dice,
        armor_class=base_ac,
        speed=speed,
        initiative_bonus=dex_mod,
        ability_scores=scores,
        saving_throw_proficiencies=saving_throws,
        skill_proficiencies=skill_proficiencies,
        tool_proficiencies=[],
        languages=languages,
        senses={
            "passive_perception": passive_perception,
            "darkvision_ft": darkvision,
            "blindsight_ft": 0, "tremorsense_ft": 0, "truesight_ft": 0,
        },
        conditions=[],
        active_effects=[],
        concentration_effect_id=None,
        exhaustion_level=0,
        features=[f.model_dump() for f in all_features],
        feats=[],
        class_resources=[cr.model_dump() for cr in class_resources],
        equipment={
            "main_hand": None, "off_hand": None, "armor": None, "helmet": None,
            "boots": None, "gloves": None, "ring_1": None, "ring_2": None,
            "amulet": None, "back": None,
        },
        inventory=inventory,
        currency={"cp": 0, "sp": 0, "ep": 0, "gp": starting_gp, "pp": 0},
        carrying_capacity=scores["strength"] * 15,
        resistances=resistances,
        immunities=immunities,
        vulnerabilities=[],
        spellcasting_ability=spellcasting_ability or "",
        spell_save_dc=spell_dc,
        spell_attack_bonus=spell_attack,
        spell_slots=spell_slots,
        spells=[s.model_dump() for s in spell_refs],
    )

    state = CharacterState(sheet=sheet, economy=ActionEconomy())

    return {"character_state": state.model_dump(mode="json")}
