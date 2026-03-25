"""
rules.py

Pure D&D 5e rule calculations. No state mutation, no I/O.
Takes explicit arguments, returns result dicts.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "bitzantium_schemas", "src"))

from bitzantium_schemas.character import PlayerSheet, ProficiencyLevel
from bitzantium_schemas.data import DamageType, WeaponProperties, WeaponType

import dice


# ---------------------------------------------------------------------------
# Skill → governing ability mapping
# ---------------------------------------------------------------------------

SKILL_ABILITY: dict[str, str] = {
    "athletics":       "strength",
    "acrobatics":      "dexterity",
    "sleight_of_hand": "dexterity",
    "stealth":         "dexterity",
    "arcana":          "intelligence",
    "history":         "intelligence",
    "investigation":   "intelligence",
    "nature":          "intelligence",
    "religion":        "intelligence",
    "animal_handling": "wisdom",
    "insight":         "wisdom",
    "medicine":        "wisdom",
    "perception":      "wisdom",
    "survival":        "wisdom",
    "deception":       "charisma",
    "intimidation":    "charisma",
    "performance":     "charisma",
    "persuasion":      "charisma",
}

# Synthetic unarmed-strike weapon entry used when no weapon is equipped
UNARMED_PROPS = WeaponProperties(
    damage_dice="1",
    damage_type=DamageType.BLUDGEONING,
    properties=[],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ability_mod(score: int) -> int:
    return (score - 10) // 2


def _score(sheet: PlayerSheet, ability: str) -> int:
    return getattr(sheet.ability_scores, ability.lower())


def _skill_prof_level(sheet: PlayerSheet, skill: str) -> ProficiencyLevel:
    for sp in sheet.skill_proficiencies:
        if sp.skill.lower() == skill.lower():
            return sp.proficiency_level
    return ProficiencyLevel.NONE


def _prof_bonus_for_level(level: ProficiencyLevel, prof_bonus: int) -> int:
    if level == ProficiencyLevel.NONE:
        return 0
    if level == ProficiencyLevel.HALF:
        return prof_bonus // 2
    if level == ProficiencyLevel.PROFICIENT:
        return prof_bonus
    if level == ProficiencyLevel.EXPERT:
        return prof_bonus * 2
    return 0


def _attack_stat(sheet: PlayerSheet, wp: WeaponProperties) -> tuple[int, str]:
    """Return (modifier, ability_name) for an attack/damage roll."""
    if "finesse" in wp.properties:
        str_mod = ability_mod(_score(sheet, "strength"))
        dex_mod = ability_mod(_score(sheet, "dexterity"))
        if str_mod >= dex_mod:
            return str_mod, "strength"
        return dex_mod, "dexterity"
    if wp.weapon_type == WeaponType.RANGED:
        return ability_mod(_score(sheet, "dexterity")), "dexterity"
    return ability_mod(_score(sheet, "strength")), "strength"


# ---------------------------------------------------------------------------
# Attack resolution
# ---------------------------------------------------------------------------

def calc_attack_roll(
    sheet: PlayerSheet,
    wp: WeaponProperties,
    proficient: bool = True,
    advantage: str = "normal",
) -> dict:
    """
    Roll an attack with a weapon.

    Returns:
        d20, rolls, advantage, is_crit, is_miss, total,
        stat_mod, stat_used, proficiency, weapon_bonus
    """
    d20 = dice.roll_d20(advantage)
    d20_val = d20["roll"]

    stat_mod, stat_used = _attack_stat(sheet, wp)
    prof      = sheet.proficiency_bonus if proficient else 0
    wpn_bonus = wp.attack_bonus

    return {
        "d20":          d20_val,
        "rolls":        d20["rolls"],
        "advantage":    advantage,
        "is_crit":      d20_val == 20,
        "is_miss":      d20_val == 1,
        "total":        d20_val + stat_mod + prof + wpn_bonus,
        "stat_mod":     stat_mod,
        "stat_used":    stat_used,
        "proficiency":  prof,
        "weapon_bonus": wpn_bonus,
    }


def calc_damage(
    sheet: PlayerSheet,
    wp: WeaponProperties,
    is_crit: bool = False,
    two_handed: bool = False,
) -> dict:
    """
    Roll weapon damage.

    Returns:
        amount, damage_type, dice_rolled, stat_mod, weapon_bonus, is_crit
    """
    expr = wp.versatile_damage if (two_handed and wp.versatile_damage) else wp.damage_dice
    stat_mod, _ = _attack_stat(sheet, wp)
    wpn_bonus   = wp.damage_bonus

    base = dice.roll(expr)
    if is_crit:
        base += dice.roll(expr)  # double the dice

    return {
        "amount":       max(1, base + stat_mod + wpn_bonus),
        "damage_type":  wp.damage_type.value,
        "dice_rolled":  base,
        "stat_mod":     stat_mod,
        "weapon_bonus": wpn_bonus,
        "is_crit":      is_crit,
    }


def apply_resistances(amount: int, damage_type: str, sheet: PlayerSheet) -> int:
    """Apply immunity → vulnerability → resistance. Returns adjusted damage."""
    dt = damage_type.lower()
    if dt in [i.lower() for i in sheet.immunities]:
        return 0
    if dt in [v.value for v in sheet.vulnerabilities]:
        return amount * 2
    if dt in [r.value for r in sheet.resistances]:
        return amount // 2
    return amount


# ---------------------------------------------------------------------------
# Saving throws & ability checks
# ---------------------------------------------------------------------------

def calc_saving_throw(
    sheet: PlayerSheet,
    ability: str,
    advantage: str = "normal",
) -> dict:
    """
    Roll a saving throw.

    Returns:
        d20, rolls, advantage, ability, modifier, proficiency,
        is_proficient, total
    """
    d20     = dice.roll_d20(advantage)
    d20_val = d20["roll"]
    ability = ability.lower()

    mod          = ability_mod(_score(sheet, ability))
    is_prof      = ability in [s.lower() for s in sheet.saving_throw_proficiencies]
    prof         = sheet.proficiency_bonus if is_prof else 0

    return {
        "d20":           d20_val,
        "rolls":         d20["rolls"],
        "advantage":     advantage,
        "ability":       ability,
        "modifier":      mod,
        "proficiency":   prof,
        "is_proficient": is_prof,
        "total":         d20_val + mod + prof,
    }


def calc_ability_check(
    sheet: PlayerSheet,
    skill_or_ability: str,
    advantage: str = "normal",
) -> dict:
    """
    Roll a skill check or raw ability check.

    skill_or_ability: skill name (e.g. 'athletics') or ability (e.g. 'strength')

    Returns:
        d20, rolls, advantage, skill, ability, modifier, proficiency,
        proficiency_level, total
    """
    d20     = dice.roll_d20(advantage)
    d20_val = d20["roll"]
    key     = skill_or_ability.lower()

    if key in SKILL_ABILITY:
        ability    = SKILL_ABILITY[key]
        prof_level = _skill_prof_level(sheet, key)
        prof       = _prof_bonus_for_level(prof_level, sheet.proficiency_bonus)
    else:
        ability    = key
        prof_level = ProficiencyLevel.NONE
        prof       = 0

    mod   = ability_mod(_score(sheet, ability))
    total = d20_val + mod + prof

    return {
        "d20":               d20_val,
        "rolls":             d20["rolls"],
        "advantage":         advantage,
        "skill":             key,
        "ability":           ability,
        "modifier":          mod,
        "proficiency":       prof,
        "proficiency_level": prof_level.value,
        "total":             total,
    }
