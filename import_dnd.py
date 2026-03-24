#!/usr/bin/env python3
"""
import_dnd.py - Import D&D 5e SRD content from the Open5e API using LLM transformation.

Fetches raw data from https://api.open5e.com/v1/, pairs it with the local JSON schema
template and a concrete example, then asks an LLM to produce a valid realm JSON file.

Requires:
    pip install openai requests
    LLM_ENDPOINT  env var  (e.g. https://api.openai.com/v1)
    LLM_API_KEY   env var

Usage:
    python import_dnd.py --type <type> --name <slug> [options]
    python import_dnd.py --type <type> --list          # browse available slugs

Types:
    ability     D&D spell / active ability  (open5e: /v1/spells/)
    item        Weapon, armor, or magic item (open5e: /v1/weapons/, /v1/armor/, /v1/magicitems/)
    class       Character class              (open5e: /v1/classes/)
    subclass    Class archetype             (open5e: /v1/classes/{parent}/ → archetypes)
    race        Subrace / variant           (open5e: /v1/races/{parent}/ → subraces)
    creature    Monster or sapient species  (open5e: /v1/monsters/)
    background  Character background        (open5e: /v1/backgrounds/)

Examples:
    python import_dnd.py --type ability  --name fireball
    python import_dnd.py --type class    --name wizard
    python import_dnd.py --type subclass --name berserker --parent barbarian
    python import_dnd.py --type race     --name hill-dwarf --parent dwarf
    python import_dnd.py --type creature --name goblin
    python import_dnd.py --type creature --name elf --sapient
    python import_dnd.py --type item     --name longsword
    python import_dnd.py --type item     --list
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

OPEN5E_BASE = "https://api.open5e.com/v1"

# Maps data type → (template path, example path, output path builder, fetch function name)
TYPE_CONFIG: dict[str, dict] = {
    "ability": {
        "template": "RealmTemplate/abilities/ability_template.json",
        "example":  "Realms/dnd/abilities/fireball.json",
        "output":   lambda name, **_: f"Realms/dnd/abilities/{name}.json",
    },
    "item": {
        "template": "RealmTemplate/items/item_template.json",
        "example":  "Realms/dnd/items/longsword.json",
        "output":   lambda name, **_: f"Realms/dnd/items/{name}.json",
    },
    "class": {
        "template": "RealmTemplate/character/classes/template.json",
        "example":  "Realms/dnd/character/classes/fighter.json",
        "output":   lambda name, **_: f"Realms/dnd/character/classes/{name}.json",
    },
    "subclass": {
        "template": "RealmTemplate/character/subclasses/template.json",
        "example":  "Realms/dnd/character/subclasses/champion.json",
        "output":   lambda name, **_: f"Realms/dnd/character/subclasses/{name}.json",
    },
    "race": {
        "template": "RealmTemplate/creatures/race_template.json",
        "example":  "Realms/dnd/creatures/sapient/dwarf/races/hill_dwarf.json",
        "output":   lambda name, parent=None, **_: (
            f"Realms/dnd/creatures/sapient/{parent}/{name}.json"
            if parent else f"Realms/dnd/creatures/sapient/unknown/races/{name}.json"
        ),
    },
    "creature": {
        "template": "RealmTemplate/creatures/creature_template.json",
        "example":  "Realms/dnd/creatures/sentient/goblin/goblin.json",
        "output":   lambda name, sapient=False, **_: (
            f"Realms/dnd/creatures/sapient/{name}/{name}.json"
            if sapient else f"Realms/dnd/creatures/sentient/{name}/{name}.json"
        ),
    },
    "background": {
        "template": "RealmTemplate/character/backgrounds/template.json",
        "example":  "Realms/dnd/character/backgrounds/acolyte.json",
        "output":   lambda name, **_: f"Realms/dnd/character/backgrounds/{name}.json",
    },
}

# Detailed schema hints given to the LLM per type (supplements the template)
SCHEMA_HINTS: dict[str, str] = {
    "ability": """\
- ability_id: snake_case version of the name (e.g. "magic_missile")
- ability_type: one of "spell" | "passive" | "active" | "legendary"
- action_cost: one of "action" | "bonus_action" | "reaction" | "free" | "legendary"
- target_type: one of "self" | "touch" | "single" | "multiple" | "area_sphere" | "area_cube" | "area_cone" | "area_line"
- spell_school: one of "abjuration" | "conjuration" | "divination" | "enchantment" | "evocation" | "illusion" | "necromancy" | "transmutation"
- spell_components: object with keys "verbal" (bool), "somatic" (bool), "material" (bool/string describing material)
- effects_by_level: list of {slot_level: int, effects: [...]}. Each effect object must have a "type" discriminator field.
  Effect types and their fields:
    {"type": "damage", "dice": "8d6", "damage_type": "fire", "save_ability": "dexterity", "save_dc": null, "half_on_save": true}
    {"type": "heal", "dice": "1d4", "bonus": 0, "max_hp_only": false}
    {"type": "apply_condition", "condition": "blinded", "duration_turns": 1, "save_ability": null, "save_dc": null}
    {"type": "remove_condition", "condition": "blinded"}
    {"type": "modify_stat", "stat": "ac", "modifier": 5, "set_value": null}
    {"type": "grant_resistance", "damage_type": "fire"}
    {"type": "grant_immunity", "damage_type": "fire"}  OR  {"type": "grant_immunity", "condition": "charmed"}
    {"type": "restore_resource", "resource_id": "hit_points", "dice": "1d8", "bonus": 0}
    {"type": "cast_spell", "spell_id": "magic_missile", "level": 1}
- spell_lists: list of class_ids that know this spell (e.g. ["wizard", "sorcerer"])
- reaction_trigger: null unless this is a reaction spell (e.g. "When you or an ally is hit by an attack")
- ritual: true if it can be cast as a ritual""",

    "item": """\
- item_id: snake_case (e.g. "longsword", "wand_of_magic_missiles")
- item_type: one of "weapon" | "armor" | "shield" | "ammunition" | "potion" | "poison" | "food" | "scroll" | "wand" | "rod" | "staff" | "ring" | "wondrous" | "tool" | "instrument" | "adventuring_gear" | "container" | "quest"
- rarity: one of "mundane" | "common" | "uncommon" | "rare" | "very_rare" | "legendary" | "artifact"
- Only fill in the ONE properties field that matches item_type; all others must be null.
  weapon_properties:  {"damage_dice": "1d8", "damage_type": "slashing", "weapon_category": "martial", "weapon_type": "melee", "versatile_dice": "1d10", "range_normal_ft": null, "range_long_ft": null, "properties": ["versatile"], "silvered": false, "adamantine": false}
  armor_properties:   {"armor_type": "medium", "base_ac": 14, "ac_bonus": 0, "max_dex_bonus": 2, "strength_requirement": 0, "stealth_disadvantage": false}
  consumable_properties: {"consumption_type": "drink", "charges": 1, "effects": [...]}
  magic_properties:   {"activation_type": "action", "charges": 0, "max_charges": 0, "recharge_on": "none", "effects_on_equip": [], "effects_on_use": [], "concentration": false}
- weapon_category: one of "simple" | "martial" | "improvised"
- weapon_type: one of "melee" | "ranged"
- armor_type: one of "light" | "medium" | "heavy" | "shield"
- recharge_on: one of "none" | "dawn" | "short_rest" | "long_rest" | "daily" | "weekly\"""",

    "class": """\
- class_id: snake_case class name (e.g. "fighter", "wizard")
- hit_die: integer (e.g. 10 for Fighter d10)
- proficiency_grants: list of {"type": "...", "options": ["..."], "choose": N}
  type can be: "saving_throw", "skill", "armor", "weapon", "tool"
  For "armor": options like ["light", "medium", "heavy", "shields"]
  For "weapon": options like ["simple", "martial"] or specific weapon ids
  For "skill": options are skill names, choose > 0 means player picks
  For "saving_throw": choose 0, list the two saves in options
- starting_equipment: list of item_ids (snake_case)
- starting_equipment_currency_gp: starting gold as integer
- spellcasting_ability: ability name (e.g. "intelligence") or "" if not a caster
- spell_prepare_style: "known" | "prepared" | "ritual_only" | "" if not a caster
- subclass_level: level at which subclass is chosen (usually 3, sometimes 1 or 2)
- level_progression: MUST have exactly 20 entries, one per level 1–20
  proficiency_bonus: 2 at levels 1-4, 3 at 5-8, 4 at 9-12, 5 at 13-16, 6 at 17-20
  features: list of {"feature_id": "snake_case", "name": "...", "source": "class_id", "description": "..."}
  resource_grants: first time a resource appears, use resource_grants
    {"resource_id": "rage", "name": "Rage", "current": 2, "max": 2, "die": null, "recharge_on": "long_rest"}
  resource_updates: subsequent levels where resource max changes
    {"resource_id": "rage", "max": 3, "die": null}
  spell_slots: null for non-casters; object like {"1": 2, "2": 1} mapping slot level to count
  cantrips_knowable: int or null
  spells_knowable: int or null""",

    "subclass": """\
- subclass_id: snake_case (e.g. "champion", "life_domain", "berserker")
- parent_class_id: snake_case class id (e.g. "fighter", "cleric", "barbarian")
- granted_at_level: the level the subclass features begin (usually 3)
- spellcasting_ability / spell_prepare_style: only fill if this subclass adds spellcasting; otherwise ""
- level_progression: list of ONLY the levels at which this subclass grants features (not all 20 levels)
  Each entry: {"level": N, "features": [...], "resource_grants": [], "resource_updates": [], "spell_slots": null, "cantrips_knowable": null, "spells_knowable": null}
  Include levels: typically the subclass-grant level plus 6, 10, 14 (varies by class)""",

    "race": """\
- race_id: snake_case (e.g. "hill_dwarf", "high_elf", "variant_human")
- parent_creature_id: snake_case species id (e.g. "dwarf", "elf", "human")
- ability_score_increases: list of {"ability": "strength", "amount": 2}
- senses_override: only non-zero values e.g. {"darkvision_ft": 60}
- speed_override: only if different from base species e.g. {"walk": 25}
- proficiency_grants: same format as class (type, options, choose)
- languages: list of language strings (e.g. ["common", "dwarvish"])
- bonus_languages: integer number of extra languages player can choose
- resistances: list of damage type strings (e.g. ["poison"])
- immunities: list of damage type strings
- traits: list of {"feature_id": "...", "name": "...", "source": "race_id", "description": "..."}""",

    "creature": """\
- creature_id: snake_case (e.g. "goblin", "ancient_red_dragon")
- creature_type: one of "humanoid" | "beast" | "undead" | "fiend" | "celestial" | "fey" | "elemental" | "construct" | "giant" | "dragon" | "monstrosity" | "ooze" | "plant" | "aberration"
- size: one of "tiny" | "small" | "medium" | "large" | "huge" | "gargantuan"
- hp_die: the die type as integer (e.g. 8 for d8, 6 for d6)
- hp_dice_count: number of hit dice
- hp_flat_bonus: flat HP bonus (usually CON modifier × hit dice count)
- hp_average: pre-calculated average HP
- armor_type: description string (e.g. "natural armor", "leather armor", "none")
- challenge_rating: string (e.g. "1/4", "1", "17")
- xp_value: integer XP award
- proficiency_bonus: integer (use CR-based: CR 0-4 → 2, 5-8 → 3, 9-12 → 4, 13-16 → 5, 17-20 → 6, 21+ → 7)
- traits/actions/bonus_actions/reactions/legendary_actions: list of
  {"feature_id": "...", "name": "...", "source": "creature_id", "description": "...", "action_cost": "action|bonus_action|reaction|free|legendary", "ability_id": null}
  Set ability_id to a snake_case ability id if this action corresponds to a spell/ability
- legendary_action_count: 0 unless creature has legendary actions
- condition_immunities: list of condition strings""",

    "background": """\
- background_id: snake_case (e.g. "acolyte", "soldier", "criminal")
- proficiency_grants: list of {"type": "skill", "options": ["insight", "religion"], "choose": 0}
  (choose 0 means all listed are granted automatically)
- bonus_languages: integer (typically 0, 1, or 2)
- starting_equipment: list of item_ids
- starting_currency_gp: integer
- features: list of {"feature_id": "...", "name": "...", "source": "background_id", "description": "..."}""",
}


# ---------------------------------------------------------------------------
# Open5e API helpers
# ---------------------------------------------------------------------------

def _get(endpoint: str, params: dict | None = None) -> dict:
    """GET an Open5e endpoint. Raises on HTTP error."""
    url = f"{OPEN5E_BASE}/{endpoint.lstrip('/')}"
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def fetch_ability(slug: str) -> dict:
    return _get(f"/spells/{slug}/")


def fetch_class(slug: str) -> dict:
    return _get(f"/classes/{slug}/")


def fetch_subclass(slug: str, parent_slug: str) -> dict:
    """Fetch the parent class and extract the named archetype from it."""
    cls_data = _get(f"/classes/{parent_slug}/")
    archetypes = cls_data.get("archetypes", [])
    # Try matching by slug field first, then by name-derived slug
    for arch in archetypes:
        arch_slug = arch.get("slug", "").lower().replace(" ", "-")
        if arch_slug == slug or arch.get("name", "").lower().replace(" ", "-") == slug:
            return {"archetype": arch, "parent_class": cls_data}
    # Fall back: return all archetypes so the LLM can pick the closest match
    print(f"[warn] archetype '{slug}' not found by exact slug; passing all archetypes to LLM", file=sys.stderr)
    return {"requested_slug": slug, "parent_class": cls_data, "archetypes": archetypes}


def fetch_race(slug: str, parent_slug: str | None) -> dict:
    """Fetch the parent race and extract the named subrace."""
    if parent_slug:
        race_data = _get(f"/races/{parent_slug}/")
        subraces = race_data.get("subraces", [])
        for sub in subraces:
            sub_slug = sub.get("slug", "").lower().replace(" ", "-")
            if sub_slug == slug or sub.get("name", "").lower().replace(" ", "-") == slug:
                return {"subrace": sub, "parent_race": race_data}
        # Not found in subraces — maybe it IS the top-level race
        print(f"[warn] subrace '{slug}' not found; passing full race data to LLM", file=sys.stderr)
        return {"requested_slug": slug, "parent_race": race_data}
    else:
        # No parent given — treat slug as a top-level race
        return _get(f"/races/{slug}/")


def fetch_creature(slug: str) -> dict:
    return _get(f"/monsters/{slug}/")


def fetch_background(slug: str) -> dict:
    return _get(f"/backgrounds/{slug}/")


def fetch_item(slug: str) -> dict:
    """Try weapons, armor, and magic items in order."""
    for endpoint in ("weapons", "armor", "magicitems"):
        try:
            data = _get(f"/{endpoint}/{slug}/")
            data["_open5e_endpoint"] = endpoint
            return data
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                continue
            raise
    raise ValueError(
        f"Item '{slug}' not found in /weapons/, /armor/, or /magicitems/. "
        "Check the slug with --list."
    )


# ---------------------------------------------------------------------------
# Listing helpers  (--list)
# ---------------------------------------------------------------------------

_LIST_ENDPOINTS: dict[str, list[str]] = {
    "ability":    ["spells"],
    "item":       ["weapons", "armor", "magicitems"],
    "class":      ["classes"],
    "subclass":   ["classes"],
    "race":       ["races"],
    "creature":   ["monsters"],
    "background": ["backgrounds"],
}


def list_available(data_type: str) -> None:
    """Print available slugs for a data type (all pages)."""
    endpoints = _LIST_ENDPOINTS[data_type]
    for endpoint in endpoints:
        print(f"\n=== /{endpoint}/ ===")
        url = f"{OPEN5E_BASE}/{endpoint}/?limit=200"
        while url:
            data = requests.get(url, timeout=15).json()
            results = data.get("results", [])
            for item in results:
                slug = item.get("slug", "")
                name = item.get("name", "")
                print(f"  {slug:<40} {name}")
            url = data.get("next")


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

def call_llm(prompt: str, model: str) -> str:
    """Call the LLM via openai-compatible API. Returns raw text response."""
    from openai import OpenAI  # local import to give a cleaner error if not installed

    endpoint = os.environ.get("LLM_ENDPOINT", "").rstrip("/")
    api_key  = os.environ.get("LLM_API_KEY", "")

    if not endpoint or not api_key:
        sys.exit(
            "Error: LLM_ENDPOINT and LLM_API_KEY environment variables must be set.\n"
            "  export LLM_ENDPOINT=https://api.openai.com/v1\n"
            "  export LLM_API_KEY=sk-..."
        )

    client = OpenAI(base_url=endpoint, api_key=api_key)

    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a D&D 5e data converter. "
                    "You transform raw Open5e API data into a specific JSON schema. "
                    "Respond with ONLY valid JSON — no markdown, no code fences, no commentary."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
    )
    return response.choices[0].message.content.strip()


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def build_prompt(data_type: str, raw_data: dict, template: dict, example: dict) -> str:
    hints = SCHEMA_HINTS.get(data_type, "")
    return f"""\
Convert the following D&D 5e source data (from the Open5e API) into the target JSON schema.

## Target JSON schema (template with empty/placeholder values)
```json
{json.dumps(template, indent=2)}
```

## Concrete filled-in example (shows exactly how a completed entry looks)
```json
{json.dumps(example, indent=2)}
```

## Schema field notes for "{data_type}"
{hints}

## General rules
- All *_id fields must be snake_case (lowercase, underscores instead of spaces/hyphens).
- Do not include fields that are not in the schema template.
- Populate every field with real data — do not leave placeholders like "" or 0 where real data is available.
- If a source field has no clear mapping, use your D&D knowledge to infer the correct value.
- Null is acceptable only where the template itself shows null and the concept doesn't apply.
- Output ONLY the JSON object — no surrounding text.

## Source data to convert
```json
{json.dumps(raw_data, indent=2)}
```

Produce the converted JSON now:"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Import D&D 5e SRD content from Open5e and convert it via LLM.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--type", required=True,
        choices=list(TYPE_CONFIG.keys()),
        metavar="TYPE",
        help=f"Data type to import: {', '.join(TYPE_CONFIG)}",
    )
    p.add_argument(
        "--name",
        help="Open5e slug of the item to import (e.g. 'fireball', 'wizard', 'goblin'). "
             "Use --list to browse available slugs.",
    )
    p.add_argument(
        "--parent",
        help="Parent slug required for subclass (parent class) and race (parent species). "
             "E.g. --type subclass --name berserker --parent barbarian",
    )
    p.add_argument(
        "--sapient", action="store_true",
        help="For --type creature: place output under sapient/ instead of sentient/.",
    )
    p.add_argument(
        "--realm", default="Realms/dnd",
        help="Output realm directory (default: Realms/dnd).",
    )
    p.add_argument(
        "--template-dir", default="RealmTemplate",
        help="Template directory (default: RealmTemplate).",
    )
    p.add_argument(
        "--model", default="gpt-4o",
        help="LLM model name (default: gpt-4o).",
    )
    p.add_argument(
        "--list", action="store_true",
        help="List available slugs for the given type and exit.",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Print the LLM prompt and exit without calling the LLM or writing files.",
    )
    return p.parse_args()


def resolve_path(relative: str, base_dir: str | None = None) -> Path:
    """Resolve a path relative to the script's directory (or base_dir)."""
    script_dir = Path(__file__).parent
    if base_dir:
        root = Path(base_dir) if Path(base_dir).is_absolute() else script_dir / base_dir
    else:
        root = script_dir
    # If the relative path starts with a known top-level dir, use script_dir as root
    return script_dir / relative


def load_json(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"Error: file not found: {path}")
    return json.loads(path.read_text())


def fetch_raw_data(data_type: str, name: str, parent: str | None) -> dict:
    """Dispatch to the correct Open5e fetch function."""
    dispatch = {
        "ability":    lambda: fetch_ability(name),
        "item":       lambda: fetch_item(name),
        "class":      lambda: fetch_class(name),
        "subclass":   lambda: fetch_subclass(name, parent or sys.exit("--parent is required for --type subclass")),
        "race":       lambda: fetch_race(name, parent),
        "creature":   lambda: fetch_creature(name),
        "background": lambda: fetch_background(name),
    }
    print(f"[+] Fetching {data_type} '{name}' from Open5e...", file=sys.stderr)
    try:
        return dispatch[data_type]()
    except requests.HTTPError as exc:
        sys.exit(f"Error fetching from Open5e: {exc}\nURL: {exc.response.url if exc.response else 'unknown'}")
    except requests.RequestException as exc:
        sys.exit(f"Network error: {exc}")


def main() -> None:
    args = parse_args()
    data_type = args.type

    # ── List mode ──────────────────────────────────────────────────────────
    if args.list:
        list_available(data_type)
        return

    if not args.name:
        sys.exit("Error: --name is required unless --list is used.")

    # ── Resolve file paths ─────────────────────────────────────────────────
    script_dir = Path(__file__).parent
    cfg = TYPE_CONFIG[data_type]

    template_path = script_dir / cfg["template"]
    example_path  = script_dir / cfg["example"]

    # Build the output path
    output_rel = cfg["output"](
        name=args.name.replace("-", "_"),
        parent=args.parent,
        sapient=args.sapient,
    )
    output_path = script_dir / args.realm / output_rel.split("/", maxsplit=2)[-1]
    # If output_rel already starts with the realm dir, don't double it
    # Recompute cleanly:
    output_path = script_dir / output_rel

    # ── Load local context ─────────────────────────────────────────────────
    print(f"[+] Loading template: {template_path}", file=sys.stderr)
    template = load_json(template_path)

    print(f"[+] Loading example:  {example_path}", file=sys.stderr)
    example = load_json(example_path)

    # ── Fetch raw data ─────────────────────────────────────────────────────
    raw_data = fetch_raw_data(data_type, args.name, args.parent)

    # ── Build prompt ───────────────────────────────────────────────────────
    prompt = build_prompt(data_type, raw_data, template, example)

    if args.dry_run:
        print(prompt)
        return

    # ── Call LLM ───────────────────────────────────────────────────────────
    print(f"[+] Calling LLM ({args.model})...", file=sys.stderr)
    raw_response = call_llm(prompt, args.model)

    # ── Parse and validate JSON ────────────────────────────────────────────
    # Strip any accidental markdown fences the model might have added
    cleaned = raw_response
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        # Drop first and last fence lines
        inner = []
        in_block = False
        for line in lines:
            if line.startswith("```") and not in_block:
                in_block = True
                continue
            if line.startswith("```") and in_block:
                break
            if in_block:
                inner.append(line)
        cleaned = "\n".join(inner)

    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        print("[!] LLM returned invalid JSON. Raw response saved to llm_error.txt", file=sys.stderr)
        Path("llm_error.txt").write_text(raw_response)
        sys.exit(f"JSON parse error: {exc}")

    # ── Write output ───────────────────────────────────────────────────────
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2))
    print(f"[✓] Written to {output_path}", file=sys.stderr)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
