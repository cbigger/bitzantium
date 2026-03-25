#!/usr/bin/env python3
"""
fabricate.py — Character creation wizard for RealmTemplate campaigns.

Extra dependencies:
    pip install questionary rich

Usage:
    python fabricate.py [--realm ./RealmTemplate] [--output ./characters] [--noob]
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import uuid
from pathlib import Path
from typing import Optional

import questionary
from questionary import Style as QStyle
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from loader import (
    load_all,
    get_class, get_subclass, get_background,
    get_creature, get_race, get_item, get_ability,
    list_classes, list_subclasses, list_backgrounds,
    list_sapient_creatures, list_races, list_items, list_abilities,
)
from bitzantium_schemas.data import ClassDefinition, SubclassDefinition, BackgroundDefinition, CreatureDefinition, RaceDefinition, ItemBase
from bitzantium_schemas.character import (
    PlayerSheet, CharacterState, ActionEconomy,
    HitDicePool, SkillProficiency, ProficiencyLevel, SpellReference, ClassResource,
)

# ── Ctrl-C ──────────────────────────────────────────────────────────────────
# Handle at the process level so questionary can't swallow it.

def _sigint(sig, frame):
    print("\nCancelled.")
    sys.exit(0)

signal.signal(signal.SIGINT, _sigint)

# ── Console & prompt style ──────────────────────────────────────────────────

console = Console()

QSTYLE = QStyle([
    ("qmark",       "fg:#e5b94e bold"),
    ("question",    "fg:#ffffff bold"),
    ("answer",      "fg:#98c379 bold"),
    ("pointer",     "fg:#e5b94e bold"),
    ("highlighted", "fg:#e5b94e bold"),
    ("selected",    "fg:#98c379"),
    ("separator",   "fg:#6c7a89"),
    ("instruction", "fg:#6c7a89 italic"),
])

# ── Constants ───────────────────────────────────────────────────────────────

STANDARD_ARRAY = [15, 14, 13, 12, 10, 8]
ABILITIES = ["strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"]
ABILITY_ABBR = {
    "strength": "STR", "dexterity": "DEX", "constitution": "CON",
    "intelligence": "INT", "wisdom": "WIS", "charisma": "CHA",
}
ALIGNMENTS = [
    "Lawful Good", "Neutral Good", "Chaotic Good",
    "Lawful Neutral", "True Neutral", "Chaotic Neutral",
    "Lawful Evil", "Neutral Evil", "Chaotic Evil",
]
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
COMMON_LANGUAGES = [
    "common", "elvish", "dwarvish", "halfling", "gnomish", "orcish",
    "goblin", "draconic", "infernal", "celestial", "sylvan",
    "undercommon", "abyssal", "deep_speech", "primordial", "giant",
]

# ── Noob text ───────────────────────────────────────────────────────────────

NOOB_TEXT = {
    "identity": (
        "Your character's identity is purely flavour — it has no effect on any numbers.\n\n"
        "The [bold]name[/bold] is how other characters in the world address you.\n\n"
        "The [bold]description[/bold] is a note for yourself and your DM: how your character looks, "
        "talks, dresses, moves. Entirely optional.\n\n"
        "[bold]Alignment[/bold] is a rough moral compass on two axes:\n"
        "  Lawful / Neutral / Chaotic — how much your character respects rules and order.\n"
        "  Good / Neutral / Evil — how much your character cares about others' wellbeing.\n\n"
        "Most player characters land somewhere Good or Neutral. Chaotic Good — 'I do the right "
        "thing, my way' — is one of the most common picks for adventurers."
    ),
    "species": (
        "Your species (sometimes called 'race') is your character's biological and cultural heritage. "
        "It affects your base speed and senses, any passive traits you always have, starting languages, "
        "and ability score bonuses that stack on top of the scores you choose later.\n\n"
        "Some species have [bold]subraces[/bold] (Heritage here). Hill Dwarves and Mountain Dwarves are "
        "both Dwarves but with different bonuses. If subraces exist you'll be asked to pick one.\n\n"
        "Only playable species appear here. Encounter creatures like goblins and skeletons are "
        "kept separately and will never show up in this list."
    ),
    "background": (
        "Your background is what your character did before becoming an adventurer. It grants two "
        "skill proficiencies, sometimes tool proficiencies or bonus languages, a small amount of "
        "starting gold, and a [bold]background feature[/bold] — a social or world benefit the DM can call on.\n\n"
        "A [bold]Soldier[/bold] gets deference from military personnel and can requisition supplies "
        "from friendly garrisons. An [bold]Acolyte[/bold] gets free healing at temples of their faith. "
        "These aren't combat abilities — they're hooks into the world."
    ),
    "class": (
        "Your class is the most mechanically significant choice. It determines:\n"
        "  Your [bold]hit die[/bold] — rolled for HP each level (d10 Fighter is tougher than d6 Wizard)\n"
        "  What [bold]weapons and armour[/bold] you can use without penalty\n"
        "  Your [bold]core abilities[/bold] and special powers\n"
        "  Whether you [bold]cast spells[/bold] and how\n\n"
        "Classes go from level 1 to 20. Most campaigns play in the 1–10 range. At a certain level "
        "(usually 3, sometimes 1) you pick a [bold]subclass[/bold] that specialises your character further.\n\n"
        "If you're starting a new campaign, enter level 1. If joining an existing group your DM will "
        "tell you what level to use."
    ),
    "ability_scores": (
        "Six scores underpin every check, attack, and save in the game:\n\n"
        "  [bold]STR[/bold] — melee attacks, carrying, athletics\n"
        "  [bold]DEX[/bold] — ranged attacks, armour class, initiative, stealth\n"
        "  [bold]CON[/bold] — hit point maximum, concentration saves\n"
        "  [bold]INT[/bold] — knowledge skills, wizard spells\n"
        "  [bold]WIS[/bold] — perception, insight, cleric/druid spells\n"
        "  [bold]CHA[/bold] — persuasion, deception, bard/paladin/sorcerer spells\n\n"
        "Each score generates a [bold]modifier[/bold] — the number actually added to dice rolls:\n"
        "  8–9 → -1   10–11 → +0   12–13 → +1   14–15 → +2   16–17 → +3   18–19 → +4\n\n"
        "[bold]Standard array[/bold] — assign [15,14,13,12,10,8] as you like. Simple and balanced.\n"
        "[bold]Point buy[/bold] — 27-point budget, max 15 before racial bonuses. Good for a specific spread.\n"
        "[bold]Manual[/bold] — type your own numbers. Use this when your DM had you roll dice.\n\n"
        "Racial bonuses are added on top of your chosen scores."
    ),
    "skills": (
        "Skills are specific applications of ability scores. Being [bold]proficient[/bold] means "
        "you add your Proficiency Bonus to that roll (at level 1 that's +2).\n\n"
        "Every skill is listed below with its governing ability and your total bonus. "
        "Proficient skills are highlighted.\n\n"
        "[bold]Passive Perception[/bold] is a fixed number (10 + WIS modifier + Perception bonus) "
        "that works automatically — the DM uses it to decide what you notice without rolling.\n\n"
        "[bold]Saving throws[/bold] are for resisting things done to you: spells, traps, poison. "
        "Your class gives you proficiency in two automatically."
    ),
    "languages": (
        "Languages are mostly a roleplay tool. [bold]Common[/bold] is spoken everywhere. "
        "Other languages let you communicate with (or eavesdrop on) specific groups.\n\n"
        "Your species gives you starting languages. Your background may grant bonus languages "
        "you choose freely.\n\n"
        "When they matter — overhearing a plot, reading an ancient inscription, negotiating "
        "with a creature that speaks no Common — they matter a lot."
    ),
    "spellcasting": (
        "Not all classes cast spells. If yours doesn't, this section is skipped.\n\n"
        "[bold]Prepared casters[/bold] (Cleric, Druid, Paladin, Wizard): access to the full class "
        "spell list, prepare a subset each day after a long rest. Prepared count = key ability "
        "modifier + class level. You can change prepared spells daily.\n\n"
        "[bold]Known casters[/bold] (Bard, Ranger, Sorcerer, Warlock): a fixed number of spells "
        "learned permanently. Fewer spells, but always available without daily prep.\n\n"
        "[bold]Spell slots[/bold] — the fuel for levelled spells. Limited per day, refresh on long rest.\n"
        "[bold]Cantrips[/bold] — level 0 spells. No slots required, cast freely.\n\n"
        "[bold]Spell Save DC[/bold] — enemies must beat this to resist your spells.\n"
        "[bold]Spell Attack Bonus[/bold] — added to attack rolls for spells that target AC.\n"
        "Both scale with your spellcasting ability modifier and proficiency bonus."
    ),
    "equipment": (
        "Starting equipment comes from your class and background. Each item ID is looked up in "
        "the realm registry. Items not yet in the registry are listed and skipped — add them to "
        "items/ to include them.\n\n"
        "[bold]Armour[/bold] sets your AC (Armour Class) — the number attackers must beat to hit you:\n"
        "  Light:   11 + full DEX modifier\n"
        "  Medium:  base AC + DEX modifier (capped at +2)\n"
        "  Heavy:   flat AC, no DEX added\n"
        "  None:    10 + DEX modifier\n\n"
        "Starting gold is your class and background amounts combined."
    ),
}


# ═══════════════════════════════════════════════════════════════════════════
# UI helpers
# ═══════════════════════════════════════════════════════════════════════════

def ask(prompt: str, **kwargs) -> str:
    return questionary.text(prompt, style=QSTYLE, **kwargs).ask() or ""


def choose(prompt: str, choices: list, **kwargs) -> str:
    return questionary.select(prompt, choices=choices, style=QSTYLE, **kwargs).ask()


def checkbox(prompt: str, choices: list, **kwargs) -> list:
    return questionary.checkbox(prompt, choices=choices, style=QSTYLE, **kwargs).ask() or []


def confirm(prompt: str, default: bool = True) -> bool:
    return questionary.confirm(prompt, default=default, style=QSTYLE).ask()


def section(title: str) -> None:
    console.print()
    console.rule(f"[bold yellow]{title}[/bold yellow]")
    console.print()


def noob_panel(key: str, noob: bool) -> None:
    if not noob:
        return
    text = NOOB_TEXT.get(key, "")
    if text:
        console.print(Panel(text, title="[bold cyan]ℹ  How this works[/bold cyan]",
                            border_style="cyan", padding=(1, 2)))
        console.print()


def print_options(items: list[tuple[str, str]]) -> None:
    """
    Print a labelled list of (name, description) pairs using Rich before
    showing a questionary prompt. Rich handles all wrapping; questionary
    only ever sees the short names.
    """
    for name, description in items:
        console.print(f"  [bold]{name}[/bold]")
        console.print(f"    [dim]{description}[/dim]")
    console.print()


# ═══════════════════════════════════════════════════════════════════════════
# Logic helpers
# ═══════════════════════════════════════════════════════════════════════════

def ability_modifier(score: int) -> int:
    return (score - 10) // 2


def proficiency_bonus_for_level(level: int) -> int:
    return max(2, (level - 1) // 4 + 2)


def parse_grants(grants) -> dict:
    result = {"skill_auto": [], "skill_choose": [], "saving_throw": [],
              "tool_auto": [], "tool_choose": []}
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


def aggregate_level_progression(class_def: ClassDefinition, level: int) -> dict:
    result = {
        "features": [], "resource_grants": [], "resource_updates": [],
        "spell_slots": None, "cantrips_knowable": None, "spells_knowable": None,
        "proficiency_bonus": proficiency_bonus_for_level(level),
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


def build_class_resources(class_def, subclass_def, level: int) -> list[ClassResource]:
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


def calculate_hp(class_def: ClassDefinition, con_score: int, level: int,
                 race_def: Optional[RaceDefinition]) -> int:
    con_mod = ability_modifier(con_score)
    hp = class_def.hit_die + con_mod
    hp += ((class_def.hit_die // 2 + 1) + con_mod) * (level - 1)
    if race_def:
        for trait in race_def.traits:
            if "hit point maximum increases by 1" in trait.description.lower():
                hp += level
    return max(1, hp)


def item_to_instance(item: ItemBase) -> dict:
    return {
        "instance_id": f"inst_{item.item_id}_{uuid.uuid4().hex[:6]}",
        "item_base": item.model_dump(),
        "quantity": 1, "attuned": False,
        "charges": item.magic_properties.charges if item.magic_properties else None,
        "custom_name": None, "condition": "normal",
    }


def is_cantrip(ability) -> bool:
    return bool(ability.effects_by_level) and all(
        e.slot_level == 0 for e in ability.effects_by_level
    )


# ═══════════════════════════════════════════════════════════════════════════
# Stages
# ═══════════════════════════════════════════════════════════════════════════

def stage_identity(noob: bool) -> dict:
    section("Identity")
    noob_panel("identity", noob)
    name = ask("Character name:")
    if not name:
        name = "Unnamed Adventurer"
    description = ask("Brief description (optional — press Enter to skip):")
    alignment = choose("Alignment:", ALIGNMENTS)
    return {"name": name, "description": description, "alignment": alignment}


def stage_species(noob: bool) -> tuple[CreatureDefinition, Optional[RaceDefinition]]:
    section("Species & Heritage")
    noob_panel("species", noob)

    sapient_ids = list_sapient_creatures()
    if not sapient_ids:
        console.print("[red]No playable species found in creatures/sapient/. Check your --realm path.[/red]")
        sys.exit(1)

    options = [(get_creature(cid).name, get_creature(cid).description) for cid in sorted(sapient_ids)]
    print_options(options)
    chosen_id = choose("Species:", [get_creature(cid).name for cid in sorted(sapient_ids)])
    # map name back to id
    chosen_id = next(cid for cid in sapient_ids if get_creature(cid).name == chosen_id)
    creature = get_creature(chosen_id)

    matching_races = sorted(
        rid for rid in list_races()
        if get_race(rid).parent_creature_id == chosen_id
    )

    race: Optional[RaceDefinition] = None
    if matching_races:
        race_options = [(get_race(rid).name, get_race(rid).description) for rid in matching_races]
        print_options(race_options)
        race_names = [get_race(rid).name for rid in matching_races] + ["(No subrace)"]
        chosen_race_name = choose("Heritage (subrace):", race_names)
        if chosen_race_name != "(No subrace)":
            race_id = next(rid for rid in matching_races if get_race(rid).name == chosen_race_name)
            race = get_race(race_id)

    if noob:
        traits = list(creature.traits) + (list(race.traits) if race else [])
        if race and race.ability_score_increases:
            asis = ", ".join(
                f"{'choice' if asi.ability == 'choice' else asi.ability.upper()} +{asi.amount}"
                for asi in race.ability_score_increases
            )
            console.print(f"  [green]Racial ability score bonuses:[/green] {asis}")
        if traits:
            console.print("  [green]Traits you gain:[/green]")
            for t in traits:
                console.print(f"    [bold]{t.name}[/bold] — {t.description}")
        console.print()

    return creature, race


def stage_background(noob: bool) -> BackgroundDefinition:
    section("Background")
    noob_panel("background", noob)

    bg_ids = sorted(list_backgrounds())
    print_options([(get_background(bid).name, get_background(bid).description) for bid in bg_ids])
    chosen_name = choose("Background:", [get_background(bid).name for bid in bg_ids])
    bg = next(get_background(bid) for bid in bg_ids if get_background(bid).name == chosen_name)

    if noob and bg.features:
        console.print("  [green]Background feature:[/green]")
        for f in bg.features:
            console.print(f"    [bold]{f.name}[/bold] — {f.description}")
        console.print()

    return bg


def stage_class_and_level(noob: bool) -> tuple[ClassDefinition, Optional[SubclassDefinition], int]:
    section("Class & Level")
    noob_panel("class", noob)

    class_ids = sorted(list_classes())
    print_options([
        (f"{get_class(cid).name}  (d{get_class(cid).hit_die} hit die)", get_class(cid).description)
        for cid in class_ids
    ])
    chosen_name = choose("Class:", [get_class(cid).name for cid in class_ids])
    class_def = next(get_class(cid) for cid in class_ids if get_class(cid).name == chosen_name)

    raw_level = ask(
        "Level (1–20):",
        validate=lambda v: (v.isdigit() and 1 <= int(v) <= 20) or "Enter a number from 1 to 20.",
    )
    level = int(raw_level)

    subclass_def: Optional[SubclassDefinition] = None
    if level >= class_def.subclass_level:
        matching = sorted(
            sid for sid in list_subclasses()
            if get_subclass(sid).parent_class_id == class_def.class_id
        )
        if matching:
            print_options([
                (get_subclass(sid).name, get_subclass(sid).description) for sid in matching
            ])
            sub_names = [get_subclass(sid).name for sid in matching] + ["(None / decide later)"]
            label = (
                "Subclass (granted at level 1):" if class_def.subclass_level == 1
                else f"Subclass (granted at level {class_def.subclass_level}):"
            )
            chosen_sub_name = choose(label, sub_names)
            if chosen_sub_name != "(None / decide later)":
                sub_id = next(sid for sid in matching if get_subclass(sid).name == chosen_sub_name)
                subclass_def = get_subclass(sub_id)

    if noob:
        prog = aggregate_level_progression(class_def, level)
        if prog["features"]:
            console.print(f"\n  [green]Class features you have at level {level}:[/green]")
            for f in prog["features"]:
                console.print(f"    [bold]{f.name}[/bold] — {f.description}")
        if subclass_def:
            for entry in subclass_def.level_progression:
                if entry.level <= level and entry.features:
                    console.print(f"\n  [green]Subclass features ({subclass_def.name}):[/green]")
                    for f in entry.features:
                        console.print(f"    [bold]{f.name}[/bold] — {f.description}")
        console.print()

    return class_def, subclass_def, level


def stage_ability_scores(race_def: Optional[RaceDefinition], noob: bool) -> dict[str, int]:
    section("Ability Scores")
    noob_panel("ability_scores", noob)

    method = choose(
        "Score generation method:",
        [
            questionary.Choice("Standard array  [15, 14, 13, 12, 10, 8]", "standard"),
            questionary.Choice("Point buy  (27 points, 8–15 before racial bonuses)", "pointbuy"),
            questionary.Choice("Manual entry  (type each score)", "manual"),
        ],
    )

    scores: dict[str, int] = {}

    if method == "standard":
        remaining = STANDARD_ARRAY[:]
        console.print("\nAssign each value to an ability. Values: " +
                      ", ".join(str(v) for v in remaining))
        for ability in ABILITIES:
            chosen_val = choose(
                f"  {ABILITY_ABBR[ability]} ({ability}):",
                [str(v) for v in sorted(remaining, reverse=True)],
            )
            val = int(chosen_val)
            remaining.remove(val)
            scores[ability] = val

    elif method == "pointbuy":
        COST = {8: 0, 9: 1, 10: 2, 11: 3, 12: 4, 13: 5, 14: 7, 15: 9}
        budget = 27
        for ability in ABILITIES:
            opts = [
                questionary.Choice(
                    f"{v}  (cost {COST[v]}, remaining after: {budget - COST[v]})", str(v)
                )
                for v in range(8, 16) if COST[v] <= budget
            ]
            val = int(choose(f"  {ABILITY_ABBR[ability]} ({ability}):", opts))
            budget -= COST[val]
            scores[ability] = val
        console.print(f"  [dim]Unspent points: {budget}[/dim]")

    else:
        for ability in ABILITIES:
            raw = ask(
                f"  {ABILITY_ABBR[ability]} ({ability}):",
                validate=lambda v: (v.isdigit() and 1 <= int(v) <= 30) or "Enter a number 1–30.",
            )
            scores[ability] = int(raw)

    if race_def:
        racial_increases: dict[str, int] = {}
        for asi in race_def.ability_score_increases:
            if asi.ability == "choice":
                chosen_ability = choose(
                    f"  Racial ASI +{asi.amount}: choose an ability:",
                    [questionary.Choice(f"{ABILITY_ABBR[a]} ({a})", a) for a in ABILITIES],
                )
                racial_increases[chosen_ability] = racial_increases.get(chosen_ability, 0) + asi.amount
            else:
                racial_increases[asi.ability] = racial_increases.get(asi.ability, 0) + asi.amount

        if racial_increases:
            console.print("\n  [green]Racial bonuses applied:[/green]")
            for ability, bonus in racial_increases.items():
                old = scores[ability]
                scores[ability] = min(30, old + bonus)
                console.print(f"    {ABILITY_ABBR[ability]}  {old} + {bonus} → {scores[ability]}")

    table = Table(box=box.SIMPLE, show_header=True, header_style="bold yellow")
    table.add_column("Ability", style="bold")
    table.add_column("Score", justify="center")
    table.add_column("Modifier", justify="center")
    for ability in ABILITIES:
        mod = ability_modifier(scores[ability])
        table.add_row(
            f"{ABILITY_ABBR[ability]}  {ability}",
            str(scores[ability]),
            f"+{mod}" if mod >= 0 else str(mod),
        )
    console.print(table)
    return scores


def stage_skills(
    class_def: ClassDefinition,
    background_def: BackgroundDefinition,
    ability_scores: dict[str, int],
    proficiency_bonus: int,
    noob: bool,
) -> tuple[list[SkillProficiency], list[str], int]:
    section("Skills & Proficiencies")
    noob_panel("skills", noob)

    class_grants = parse_grants(class_def.proficiency_grants)
    bg_grants    = parse_grants(background_def.proficiency_grants)

    chosen_skills: set[str] = set(class_grants["skill_auto"] + bg_grants["skill_auto"])

    for count, pool in class_grants["skill_choose"] + bg_grants["skill_choose"]:
        available = sorted(set(pool) - chosen_skills)
        if not available:
            continue
        console.print(
            f"  Choose [bold yellow]{count}[/bold yellow] skill(s). "
            f"Your proficiency bonus (+{proficiency_bonus}) will be added to rolls with these skills."
        )
        picked = checkbox(
            f"  Select {count}:",
            choices=[
                questionary.Choice(
                    f"{s.replace('_', ' ').title()}  ({ABILITY_ABBR[SKILL_ABILITY[s]]})", s
                )
                for s in available
            ],
            validate=lambda items, c=count: len(items) == c or f"Select exactly {c}.",
        )
        chosen_skills.update(picked)

    saving_throws = list(set(class_grants["saving_throw"]))

    wis_mod = ability_modifier(ability_scores["wisdom"])
    perc_bonus = proficiency_bonus if "perception" in chosen_skills else (proficiency_bonus // 2)
    passive_perception = 10 + wis_mod + perc_bonus

    console.print()
    table = Table(box=box.SIMPLE, header_style="bold yellow", show_header=True)
    table.add_column("Skill", style="bold")
    table.add_column("Ability", style="dim", justify="center")
    table.add_column("Prof", justify="center")
    table.add_column("Bonus", justify="center")
    for s in sorted(ALL_SKILLS):
        ab = SKILL_ABILITY[s]
        base_mod = ability_modifier(ability_scores[ab])
        proficient = s in chosen_skills
        total = base_mod + (proficiency_bonus if proficient else 0)
        total_str = f"+{total}" if total >= 0 else str(total)
        table.add_row(
            s.replace("_", " ").title(),
            ABILITY_ABBR[ab],
            "[green]✓[/green]" if proficient else "",
            f"[green bold]{total_str}[/green bold]" if proficient else f"[dim]{total_str}[/dim]",
        )
    console.print(table)
    console.print(f"  Saving throw proficiencies: [green]{', '.join(saving_throws) or 'none'}[/green]")
    console.print(f"  Passive Perception: [bold]{passive_perception}[/bold]")

    result = [
        SkillProficiency(
            skill=s,
            proficiency_level=ProficiencyLevel.PROFICIENT if s in chosen_skills else ProficiencyLevel.NONE,
        )
        for s in ALL_SKILLS
    ]
    return result, saving_throws, passive_perception


def stage_languages(
    creature_def: CreatureDefinition,
    race_def: Optional[RaceDefinition],
    background_def: BackgroundDefinition,
    noob: bool,
) -> list[str]:
    section("Languages")
    noob_panel("languages", noob)

    base_languages: list[str] = list(creature_def.languages) if creature_def.languages else []
    if race_def and race_def.languages:
        for lang in race_def.languages:
            if lang not in base_languages:
                base_languages.append(lang)
    bonus_count = creature_def.bonus_languages + (race_def.bonus_languages if race_def else 0) + background_def.bonus_languages

    console.print(
        f"  Base languages: [green]{', '.join(base_languages) if base_languages else 'none'}[/green]"
    )

    chosen: list[str] = list(base_languages)
    if bonus_count > 0:
        available = [l for l in COMMON_LANGUAGES if l not in chosen]
        console.print(f"  Choose [bold yellow]{bonus_count}[/bold yellow] additional language(s).")
        picked = checkbox(
            "  Select languages:",
            choices=[questionary.Choice(l.replace("_", " ").title(), l) for l in available],
            validate=lambda items, c=bonus_count: len(items) == c or f"Select exactly {c}.",
        )
        chosen.extend(picked)

    if not chosen:
        console.print("  [dim]No languages from species or background — defaulting to Common.[/dim]")
        chosen = ["common"]

    console.print(f"  Languages: [green]{', '.join(chosen)}[/green]")
    return chosen


def stage_spells(
    class_def: ClassDefinition,
    subclass_def: Optional[SubclassDefinition],
    level_data: dict,
    level: int,
    ability_scores: dict[str, int],
    proficiency_bonus: int,
    noob: bool,
) -> tuple[list[SpellReference], int, int]:
    section("Spellcasting")
    noob_panel("spellcasting", noob)

    spellcasting_ability = (
        subclass_def.spellcasting_ability
        if (subclass_def and subclass_def.spellcasting_ability)
        else class_def.spellcasting_ability
    )

    if not spellcasting_ability:
        console.print("  [dim]This class does not use spells. Skipping.[/dim]")
        return [], 0, 0

    sp_mod    = ability_modifier(ability_scores[spellcasting_ability])
    spell_dc  = 8 + proficiency_bonus + sp_mod
    sp_attack = proficiency_bonus + sp_mod

    console.print(
        f"  Spellcasting ability: [bold yellow]{spellcasting_ability.upper()}[/bold yellow]"
        f"   Save DC: [bold]{spell_dc}[/bold]   Attack bonus: [bold]+{sp_attack}[/bold]"
    )

    raw_slots = level_data.get("spell_slots") or {}
    if raw_slots:
        console.print("  Spell slots: " + "   ".join(
            f"L{k}: [bold]{v}[/bold]" for k, v in sorted(raw_slots.items()) if v > 0
        ))

    all_spells   = [
        get_ability(aid) for aid in list_abilities()
        if get_ability(aid) and class_def.class_id in get_ability(aid).spell_lists
    ]
    cantrip_pool = [s for s in all_spells if is_cantrip(s)]
    leveled_pool = [s for s in all_spells if not is_cantrip(s)]

    spell_refs: list[SpellReference] = []
    cantrips_knowable = level_data.get("cantrips_knowable") or 0

    if cantrips_knowable > 0:
        if cantrip_pool:
            console.print(f"\n  Choose [bold yellow]{cantrips_knowable}[/bold yellow] cantrip(s).")
            print_options([(s.name, s.description) for s in sorted(cantrip_pool, key=lambda x: x.name)])
            picked = checkbox(
                "  Cantrips:",
                choices=[
                    questionary.Choice(
                        f"{s.name}  [{s.spell_school.value if s.spell_school else 'special'}]",
                        s.ability_id,
                    )
                    for s in sorted(cantrip_pool, key=lambda x: x.name)
                ],
                validate=lambda items, c=cantrips_knowable: len(items) == c or f"Select exactly {c}.",
            )
            spell_refs.extend(SpellReference(spell_id=sid) for sid in picked)
        else:
            console.print(f"  [dim]No cantrips found in registry for {class_def.name}.[/dim]")

    is_prepared     = class_def.spell_prepare_style == "prepare"
    spells_knowable = level_data.get("spells_knowable")

    if is_prepared:
        prepare_count = max(1, sp_mod + level)
        console.print(
            f"\n  [dim]{class_def.name} prepares spells after each long rest. "
            f"Limit is {spellcasting_ability.upper()} modifier ({sp_mod:+d}) + level ({level}) "
            f"= [bold]{prepare_count}[/bold]. You can change this list daily.[/dim]"
        )
        if leveled_pool:
            console.print(f"  Select your initial prepared spells (up to {prepare_count}).")
            print_options([(s.name, s.description) for s in sorted(leveled_pool, key=lambda x: x.name)])
            picked = checkbox(
                "  Prepare spells:",
                choices=[
                    questionary.Choice(
                        f"{s.name}  [{s.spell_school.value if s.spell_school else '?'}]",
                        s.ability_id,
                    )
                    for s in sorted(leveled_pool, key=lambda x: x.name)
                ],
                validate=lambda items, c=prepare_count: (
                    len(items) <= c or f"You can prepare at most {c} spells."
                ),
            )
            spell_refs.extend(SpellReference(spell_id=sid, prepared=True) for sid in picked)
        else:
            console.print(f"  [dim]No levelled spells found in registry for {class_def.name}.[/dim]")

    elif spells_knowable:
        if leveled_pool:
            console.print(f"\n  Choose up to [bold yellow]{spells_knowable}[/bold yellow] spell(s) known.")
            print_options([(s.name, s.description) for s in sorted(leveled_pool, key=lambda x: x.name)])
            picked = checkbox(
                "  Spells known:",
                choices=[
                    questionary.Choice(
                        f"{s.name}  [{s.spell_school.value if s.spell_school else '?'}]",
                        s.ability_id,
                    )
                    for s in sorted(leveled_pool, key=lambda x: x.name)
                ],
                validate=lambda items, c=spells_knowable: len(items) <= c or f"Select at most {c}.",
            )
            spell_refs.extend(SpellReference(spell_id=sid) for sid in picked)
        else:
            console.print(f"  [dim]No levelled spells found in registry for {class_def.name}.[/dim]")

    return spell_refs, spell_dc, sp_attack


def stage_starting_equipment(
    class_def: ClassDefinition,
    background_def: BackgroundDefinition,
    noob: bool,
) -> tuple[list[dict], int]:
    section("Starting Equipment")
    noob_panel("equipment", noob)

    all_item_ids = list(set(class_def.starting_equipment + background_def.starting_equipment))
    inventory: list[dict] = []
    not_found: list[str] = []

    for item_id in all_item_ids:
        item = get_item(item_id)
        if item:
            inventory.append(item_to_instance(item))
            console.print(f"  [green]✓[/green]  {item.name}")
        else:
            not_found.append(item_id)

    if not_found:
        console.print(f"\n  [dim]Not yet in registry (skipped): {', '.join(not_found)}[/dim]")

    gp = class_def.starting_equipment_currency_gp + background_def.starting_currency_gp
    if gp:
        console.print(f"  [green]✓[/green]  Starting gold: {gp} gp")

    return inventory, gp


# ═══════════════════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════════════════

def print_summary(sheet: dict, level: int, class_def: ClassDefinition) -> None:
    section("Summary")

    scores = sheet["ability_scores"]
    t = Table(box=box.ROUNDED, show_header=False, padding=(0, 2))
    t.add_column("Field", style="dim")
    t.add_column("Value", style="bold white")

    t.add_row("Name",        sheet["name"])
    if sheet["description"]:
        t.add_row("Description", sheet["description"])
    t.add_row("Alignment",   sheet["alignment"])
    t.add_row("Species",     sheet["creature_id"] + (f"  ({sheet['race_id']})" if sheet["race_id"] else ""))
    t.add_row("Background",  sheet["background_id"])
    cls_label = class_def.name + f" {level}"
    if sheet["classes"][0]["subclass_id"]:
        cls_label += f"  ({sheet['classes'][0]['subclass_id']})"
    t.add_row("Class",       cls_label)
    t.add_row("HP",          str(sheet["hp_max"]))
    t.add_row("AC",          str(sheet["armor_class"]))
    t.add_row("Speed",       f"{sheet['speed']} ft")
    t.add_row("Initiative",  f"+{sheet['initiative_bonus']}" if sheet["initiative_bonus"] >= 0 else str(sheet["initiative_bonus"]))
    t.add_row("Prof. Bonus", f"+{sheet['proficiency_bonus']}")
    t.add_row("Abilities",   "  ".join(f"{ABILITY_ABBR[a]} {scores[a]}" for a in ABILITIES))
    t.add_row("Languages",   ", ".join(sheet["languages"]) or "—")

    proficient_skills = [
        sp["skill"].replace("_", " ").title()
        for sp in sheet["skill_proficiencies"]
        if sp["proficiency_level"] == "proficient"
    ]
    t.add_row("Skills", ", ".join(proficient_skills) or "—")

    if sheet["class_resources"]:
        t.add_row("Resources", ", ".join(
            f"{r['name']} {r['current']}/{r['max']}" for r in sheet["class_resources"]
        ))

    if sheet["spellcasting_ability"]:
        t.add_row("Spell DC",  str(sheet["spell_save_dc"]))
        t.add_row("Spell Atk", f"+{sheet['spell_attack_bonus']}")
        slot_summary = "  ".join(
            f"L{k}: {v['remaining']}/{v['total']}"
            for k, v in sorted(sheet["spell_slots"].items())
        )
        if slot_summary:
            t.add_row("Spell Slots", slot_summary)
        if sheet["spells"]:
            t.add_row("Spells", ", ".join(s["spell_id"] for s in sheet["spells"]))

    console.print(Panel(t, title="[bold yellow]Character Sheet[/bold yellow]", border_style="yellow"))


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

def main(realm_path: str, output_dir: str, noob: bool) -> None:
    console.print(Panel(
        "[bold yellow]Character Fabricator[/bold yellow]\n[dim]RealmTemplate Campaign System[/dim]",
        border_style="yellow", padding=(1, 4),
    ))
    console.print(f"  Loading realm data from [cyan]{realm_path}[/cyan]…")
    summary = load_all(realm_path)
    console.print("  " + "  ".join(f"{k}: [bold]{v}[/bold]" for k, v in summary.items() if v > 0))
    console.print()

    if not list_classes():
        console.print("[red]No classes loaded. Check --realm path.[/red]")
        sys.exit(1)

    identity               = stage_identity(noob)
    creature_def, race_def = stage_species(noob)
    background_def         = stage_background(noob)
    class_def, subclass_def, level = stage_class_and_level(noob)
    ability_scores         = stage_ability_scores(race_def, noob)

    level_data        = aggregate_level_progression(class_def, level)
    proficiency_bonus = level_data["proficiency_bonus"]

    skill_proficiencies, saving_throws, passive_perception = stage_skills(
        class_def, background_def, ability_scores, proficiency_bonus, noob
    )
    languages = stage_languages(creature_def, race_def, background_def, noob)
    spells, spell_dc, spell_attack = stage_spells(
        class_def, subclass_def, level_data, level, ability_scores, proficiency_bonus, noob
    )
    inventory, starting_gp = stage_starting_equipment(class_def, background_def, noob)

    dex_mod = ability_modifier(ability_scores["dexterity"])
    hp_max  = calculate_hp(class_def, ability_scores["constitution"], level, race_def)

    base_ac = 10 + dex_mod
    for inst in inventory:
        ap = inst["item_base"].get("armor_properties")
        if ap:
            max_dex = ap.get("max_dex_bonus")
            effective_dex = min(dex_mod, max_dex) if max_dex is not None else dex_mod
            base_ac = max(base_ac, ap["base_ac"] + effective_dex + ap.get("ac_bonus", 0))

    speed      = creature_def.speed.walk
    darkvision = creature_def.senses.darkvision_ft
    if race_def:
        if race_def.speed_override.get("walk"):
            speed = race_def.speed_override["walk"]
        if race_def.senses_override.get("darkvision_ft"):
            darkvision = race_def.senses_override["darkvision_ft"]

    resistances = list(creature_def.resistances) + (list(race_def.resistances) if race_def else [])
    immunities  = list(creature_def.immunities)  + (list(race_def.immunities)  if race_def else [])

    all_features = list(level_data["features"])
    if subclass_def:
        for entry in subclass_def.level_progression:
            if entry.level <= level:
                all_features.extend(entry.features)
    if race_def:
        all_features.extend(race_def.traits)
    all_features.extend(creature_def.traits)

    class_resources = build_class_resources(class_def, subclass_def, level)
    hit_dice        = [HitDicePool(die=f"d{class_def.hit_die}", total=level, remaining=level)]
    raw_slots       = level_data.get("spell_slots") or {}
    spell_slots     = {int(k): {"total": v, "remaining": v} for k, v in raw_slots.items() if v > 0}

    sheet_dict = {
        "entity_id":    f"char_{uuid.uuid4().hex[:12]}",
        "name":         identity["name"],
        "creature_id":  creature_def.creature_id,
        "race_id":      race_def.race_id if race_def else "",
        "description":  identity["description"],
        "background_id": background_def.background_id,
        "alignment":    identity["alignment"],
        "inspiration":  False,
        "classes": [{
            "class_id":    class_def.class_id,
            "subclass_id": subclass_def.subclass_id if subclass_def else "",
            "level":       level,
            "hit_die":     f"d{class_def.hit_die}",
        }],
        "level":             level,
        "experience":        0,
        "proficiency_bonus": proficiency_bonus,
        "hp_current":        hp_max,
        "hp_max":            hp_max,
        "hp_temp":           0,
        "hit_dice":          [hd.model_dump() for hd in hit_dice],
        "armor_class":       base_ac,
        "speed":             speed,
        "initiative_bonus":  dex_mod,
        "death_saves":       {"successes": 0, "failures": 0, "stable": False},
        "ability_scores":    ability_scores,
        "saving_throw_proficiencies": saving_throws,
        "skill_proficiencies": [sp.model_dump() for sp in skill_proficiencies],
        "tool_proficiencies": [],
        "languages":          languages,
        "senses": {
            "passive_perception": passive_perception,
            "darkvision_ft":      darkvision,
            "blindsight_ft":      0, "tremorsense_ft": 0, "truesight_ft": 0,
        },
        "conditions": [], "active_effects": [], "concentration_effect_id": None,
        "exhaustion_level": 0,
        "features":        [f.model_dump() for f in all_features],
        "feats":           [],
        "class_resources": [cr.model_dump() for cr in class_resources],
        "equipment": {
            "main_hand": None, "off_hand": None, "armor": None, "helmet": None,
            "boots": None, "gloves": None, "ring_1": None, "ring_2": None,
            "amulet": None, "back": None,
        },
        "inventory":         inventory,
        "currency":          {"cp": 0, "sp": 0, "ep": 0, "gp": starting_gp, "pp": 0},
        "carrying_capacity": ability_scores["strength"] * 15,
        "resistances":       resistances,
        "immunities":        immunities,
        "vulnerabilities":   [],
        "spellcasting_ability": class_def.spellcasting_ability,
        "spell_save_dc":        spell_dc,
        "spell_attack_bonus":   spell_attack,
        "spell_slots":          spell_slots,
        "spells":               [s.model_dump() for s in spells],
    }

    full_data = {
        "sheet":   sheet_dict,
        "economy": {"action_spent": False, "bonus_action_spent": False,
                    "reaction_spent": False, "movement_used": 0},
    }

    try:
        CharacterState(sheet=PlayerSheet(**sheet_dict), economy=ActionEconomy())
    except Exception as e:
        console.print(f"\n[red]Validation error — character may have issues:[/red]\n{e}")

    print_summary(sheet_dict, level, class_def)

    if not confirm("\nSave this character?"):
        console.print("[yellow]Discarded.[/yellow]")
        return

    out_dir   = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_name = identity["name"].lower().replace(" ", "_")
    out_path  = out_dir / f"{safe_name}.json"
    if out_path.exists():
        out_path = out_dir / f"{safe_name}_{uuid.uuid4().hex[:4]}.json"

    with out_path.open("w", encoding="utf-8") as f:
        json.dump(full_data, f, indent=2)

    console.print(f"\n  [bold green]Saved →[/bold green] [cyan]{out_path}[/cyan]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Character creation wizard for RealmTemplate.")
    parser.add_argument("--realm",  default="./RealmTemplate",
                        help="Path to the RealmTemplate directory (default: ./RealmTemplate)")
    parser.add_argument("--output", default="./characters",
                        help="Directory to save character JSON files (default: ./characters)")
    parser.add_argument("--noob",   action="store_true",
                        help="Show detailed explanations of every step as you go")
    args = parser.parse_args()
    main(args.realm, args.output, args.noob)
