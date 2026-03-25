"""
creature_converter.py

Fetches creature data from the Open5e API and transforms it into the
CreatureDefinition format used by the TTRPG engine.

Usage:
    python creature_converter.py [--output-dir OUTPUT_DIR]
                                 [--local-file FILE]
                                 [--include-source SOURCE [SOURCE ...]]
                                 [--group-by-source]

If --local-file is provided, reads from that JSON file instead of hitting the API.
--include-source filters content to only the given document slugs (e.g., wotc-srd).
--group-by-source saves creatures into subdirectories named after their source slug.
"""

import argparse
import json
import re
import requests
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

from bitzantium_schemas.data import CreatureDefinition

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OPEN5E_API_BASE = "https://api.open5e.com"
MONSTER_ENDPOINT = f"{OPEN5E_API_BASE}/monsters/"

# Standard 5e CR → XP table
XP_BY_CR = {
    0: 0, 0.125: 25, 0.25: 50, 0.5: 100,
    1: 200, 2: 450, 3: 700, 4: 1100, 5: 1800,
    6: 2300, 7: 2900, 8: 3900, 9: 5000, 10: 5900,
    11: 7200, 12: 8400, 13: 10000, 14: 11500, 15: 13000,
    16: 15000, 17: 18000, 18: 20000, 19: 22000, 20: 25000,
    21: 33000, 22: 41000, 23: 50000, 24: 62000, 25: 75000,
    26: 90000, 27: 105000, 28: 120000, 29: 135000, 30: 155000,
}

# ---------------------------------------------------------------------------
# Helper: safe list (Open5e returns None for empty lists)
# ---------------------------------------------------------------------------

def _list(val):
    """Return val if it's a list, else empty list. Handles None from API."""
    return val if isinstance(val, list) else []

# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_open5e_creatures() -> List[Dict]:
    """Fetch all creature data from the Open5e API (paginated)."""
    results = []
    url = MONSTER_ENDPOINT
    while url:
        print(f"Fetching {url}")
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        results.extend(data.get("results", []))
        url = data.get("next")
    return results


def load_local_creatures(file_path: Path) -> List[Dict]:
    """Load creature data from a local JSON file (expects Open5e format)."""
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "results" in data:
        return data["results"]
    if isinstance(data, list):
        return data
    return [data]

# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def parse_cr(cr_str: str) -> float:
    """Parse CR string like '1/4' or '7' into a float."""
    if not cr_str:
        return 0.0
    if '/' in cr_str:
        num, den = cr_str.split('/')
        return float(num) / float(den)
    return float(cr_str)


def proficiency_bonus_from_cr(cr: float) -> int:
    """Standard 5e proficiency bonus from CR."""
    if cr < 5:
        return 2
    if cr < 9:
        return 3
    if cr < 13:
        return 4
    if cr < 17:
        return 5
    if cr < 21:
        return 6
    if cr < 25:
        return 7
    if cr < 29:
        return 8
    return 9


def parse_hit_dice(hit_dice_str: str) -> Tuple[int, int, int]:
    """Parse '10d12+50' into (count, die, flat_bonus)."""
    if not hit_dice_str:
        return 0, 0, 0
    match = re.match(r'(\d+)d(\d+)(?:\s*([+-]\s*\d+))?', hit_dice_str)
    if match:
        count = int(match.group(1))
        die = int(match.group(2))
        flat = int(match.group(3).replace(' ', '')) if match.group(3) else 0
        return count, die, flat
    return 0, 0, 0


def parse_senses(senses_str: str) -> Dict[str, int]:
    """Parse senses string into structured dict."""
    senses = {
        "passive_perception": 0,
        "darkvision_ft": 0,
        "blindsight_ft": 0,
        "tremorsense_ft": 0,
        "truesight_ft": 0,
    }
    if not senses_str:
        return senses
    mapping = {
        "passive perception": "passive_perception",
        "darkvision": "darkvision_ft",
        "blindsight": "blindsight_ft",
        "tremorsense": "tremorsense_ft",
        "truesight": "truesight_ft",
    }
    for part in senses_str.split(','):
        part_lower = part.strip().lower()
        for keyword, key in mapping.items():
            if keyword in part_lower:
                m = re.search(r'(\d+)', part)
                if m:
                    senses[key] = int(m.group(1))
                break
    return senses


def parse_damage_list(damage_str: str) -> List[str]:
    """Parse 'acid; bludgeoning, piercing, and slashing from nonmagical attacks' into list."""
    if not damage_str:
        return []
    items = []
    for part in damage_str.split(';'):
        part = part.strip()
        for sub in part.split(','):
            sub = re.sub(r'\band\b', '', sub).strip()
            if sub and sub not in items:
                items.append(sub)
    return items


def parse_condition_immunities(cond_str: str) -> List[str]:
    if not cond_str:
        return []
    return [c.strip() for c in cond_str.split(',') if c.strip()]


def parse_legendary_action_count(legendary_desc: str) -> int:
    if not legendary_desc:
        return 0
    match = re.search(r'(\d+)\s+legendary actions', legendary_desc, re.IGNORECASE)
    return int(match.group(1)) if match else 0

# ---------------------------------------------------------------------------
# Action description parser — extracts structured attack data from 5e text
# ---------------------------------------------------------------------------

# Pattern: "Melee Weapon Attack: +5 to hit, reach 5 ft., one target. Hit: 7 (1d8 + 3) slashing damage"
# Pattern: "Ranged Weapon Attack: +5 to hit, range 80/320 ft., one target. Hit: ..."
# Pattern: "Melee or Ranged Weapon Attack: +5 to hit, reach 5 ft. or range 20/60 ft., one target."
_ATTACK_RE = re.compile(
    r'(?P<attack_type>Melee|Ranged|Melee or Ranged)\s+(?:Weapon|Spell)\s+Attack:\s*'
    r'\+?(?P<attack_bonus>-?\d+)\s+to hit,\s*'
    r'(?:reach\s+(?P<reach>\d+)\s*ft\.?)?'
    r'(?:\s*(?:or\s+)?range\s+(?P<range_normal>\d+)/(?P<range_max>\d+)\s*ft\.?)?'
    r',?\s*(?P<target_desc>[^.]*?)\.',
    re.IGNORECASE
)

# Pattern: "Hit: 7 (1d8 + 3) slashing damage" — may repeat for extra damage
# Also handles: "plus 7 (2d6) fire damage"
_DAMAGE_RE = re.compile(
    r'(?:Hit:\s*\d+\s*|plus\s+\d+\s*)'
    r'\((?P<dice_count>\d+)d(?P<die>\d+)'
    r'(?:\s*\+\s*(?P<bonus>\d+))?\)\s*'
    r'(?P<damage_type>\w+)\s+damage',
    re.IGNORECASE
)

# Simpler fallback for "Hit: X damage" without dice (flat damage)
_FLAT_DAMAGE_RE = re.compile(
    r'Hit:\s*(?P<flat>\d+)\s+(?P<damage_type>\w+)\s+damage',
    re.IGNORECASE
)

# DC-based actions: "must make a DC 15 Dexterity saving throw"
_SAVE_RE = re.compile(
    r'DC\s+(?P<dc>\d+)\s+(?P<ability>\w+)\s+saving throw',
    re.IGNORECASE
)


def _slugify(text: str) -> str:
    """Convert text to snake_case slug."""
    return re.sub(r'[^a-z0-9]+', '_', text.lower()).strip('_')


def _parse_target_count(target_desc: str) -> int:
    """Extract target count from strings like 'one target', 'two targets'."""
    target_desc = target_desc.lower()
    word_map = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
    for word, num in word_map.items():
        if word in target_desc:
            return num
    m = re.search(r'(\d+)\s+target', target_desc)
    if m:
        return int(m.group(1))
    return 1


def parse_action(raw: Dict, creature_id: str, action_cost: str) -> Dict:
    """
    Parse an Open5e action dict into engine action format.

    Handles weapon attacks, spell attacks, save-based actions, and
    non-attack actions (like Multiattack descriptions).
    """
    name = raw.get("name", "")
    desc = raw.get("desc", "")
    action_id = f"{creature_id}_{_slugify(name)}"

    result = {
        "action_id": action_id,
        "name": name,
        "action_cost": action_cost,
        "attack_type": None,
        "reach_ft": None,
        "range_normal_ft": None,
        "range_max_ft": None,
        "target_count": 0,
        "attack_bonus": None,
        "save_ability": None,
        "save_dc": None,
        "on_hit": [],
        "description": desc,
    }

    # Try to match an attack line
    atk_match = _ATTACK_RE.search(desc)
    if atk_match:
        atk_type_raw = atk_match.group("attack_type").lower()
        if "spell" in desc.lower().split("attack")[0]:
            if "ranged" in atk_type_raw:
                result["attack_type"] = "ranged_spell"
            else:
                result["attack_type"] = "melee_spell"
        else:
            if "melee or ranged" in atk_type_raw:
                result["attack_type"] = "melee_weapon"  # primary; range also set
            elif "ranged" in atk_type_raw:
                result["attack_type"] = "ranged_weapon"
            else:
                result["attack_type"] = "melee_weapon"

        result["attack_bonus"] = int(atk_match.group("attack_bonus"))

        if atk_match.group("reach"):
            result["reach_ft"] = int(atk_match.group("reach"))
        if atk_match.group("range_normal"):
            result["range_normal_ft"] = int(atk_match.group("range_normal"))
            result["range_max_ft"] = int(atk_match.group("range_max"))

        target_desc = atk_match.group("target_desc") or ""
        result["target_count"] = _parse_target_count(target_desc)

    # Parse damage entries
    on_hit = []
    for dmg in _DAMAGE_RE.finditer(desc):
        on_hit.append({
            "damage_dice_count": int(dmg.group("dice_count")),
            "damage_die": int(dmg.group("die")),
            "damage_flat_bonus": int(dmg.group("bonus")) if dmg.group("bonus") else 0,
            "damage_type": dmg.group("damage_type").lower(),
        })

    # Fallback: flat damage with no dice (e.g., some special abilities)
    if not on_hit:
        flat_match = _FLAT_DAMAGE_RE.search(desc)
        if flat_match:
            on_hit.append({
                "damage_dice_count": 0,
                "damage_die": 0,
                "damage_flat_bonus": int(flat_match.group("flat")),
                "damage_type": flat_match.group("damage_type").lower(),
            })

    result["on_hit"] = on_hit

    # Check for saving throw DC
    save_match = _SAVE_RE.search(desc)
    if save_match:
        result["save_dc"] = int(save_match.group("dc"))
        result["save_ability"] = save_match.group("ability").lower()

    return result


def _extract_trigger(desc: str) -> str:
    """Extract the trigger condition from a reaction description.

    D&D 5e reactions typically state their trigger after phrases like
    "when", "if", or "which the creature can see". We try to pull out
    a concise trigger clause; if nothing matches, we fall back to the
    full description.
    """
    # Common pattern: "When <condition>, the creature ..."
    # e.g. "When a creature the knight can see attacks a target ..."
    m = re.match(
        r'[^.]*?\b(when\b[^,.]+(?:,|\.|\band\b))',
        desc,
        re.IGNORECASE,
    )
    if m:
        trigger = m.group(1).rstrip('.,').strip()
        if len(trigger) > 10:
            return trigger

    # Pattern: sentence starting with "If ..."
    m = re.match(r'(If\b[^,.]+)', desc, re.IGNORECASE)
    if m:
        trigger = m.group(1).strip()
        if len(trigger) > 10:
            return trigger

    # Fallback: take the first sentence as the trigger
    first_sentence = re.split(r'\.(?:\s|$)', desc, maxsplit=1)[0].strip()
    if first_sentence:
        return first_sentence

    return desc


def parse_reaction(raw: Dict, creature_id: str) -> Dict:
    """Parse an Open5e reaction dict into engine CreatureReaction format.

    Unlike parse_action, reactions have a 'trigger' field instead of
    'action_cost', 'attack_type', 'reach_ft', etc.
    """
    name = raw.get("name", "")
    desc = raw.get("desc", "")
    action_id = f"{creature_id}_{_slugify(name)}"

    trigger = _extract_trigger(desc)

    result = {
        "action_id": action_id,
        "name": name,
        "trigger": trigger,
        "attack_bonus": None,
        "save_ability": None,
        "save_dc": None,
        "on_hit": [],
        "description": desc,
    }

    # Parse damage entries
    on_hit = []
    for dmg in _DAMAGE_RE.finditer(desc):
        on_hit.append({
            "damage_dice_count": int(dmg.group("dice_count")),
            "damage_die": int(dmg.group("die")),
            "damage_flat_bonus": int(dmg.group("bonus")) if dmg.group("bonus") else 0,
            "damage_type": dmg.group("damage_type").lower(),
        })
    result["on_hit"] = on_hit

    # Check for attack bonus
    atk_match = _ATTACK_RE.search(desc)
    if atk_match:
        result["attack_bonus"] = int(atk_match.group("attack_bonus"))

    # Check for saving throw DC
    save_match = _SAVE_RE.search(desc)
    if save_match:
        result["save_dc"] = int(save_match.group("dc"))
        result["save_ability"] = save_match.group("ability").lower()

    return result


# ---------------------------------------------------------------------------
# Main converter
# ---------------------------------------------------------------------------

def convert_creature(c: Dict) -> Dict:
    """Transform a single Open5e creature into engine CreatureDefinition format."""
    creature_id = c.get("slug", "")
    cr_str = c.get("challenge_rating", "0")
    cr_float = parse_cr(cr_str)

    hp_count, hp_die, hp_flat = parse_hit_dice(c.get("hit_dice", ""))

    speed_dict = c.get("speed", {}) or {}
    speed = {
        "walk": speed_dict.get("walk", 0) or 0,
        "swim": speed_dict.get("swim", 0) or 0,
        "climb": speed_dict.get("climb", 0) or 0,
        "fly": speed_dict.get("fly", 0) or 0,
        "burrow": speed_dict.get("burrow", 0) or 0,
    }

    # Traits from special_abilities
    traits = []
    for t in _list(c.get("special_abilities")):
        trait_name = t.get("name", "")
        traits.append({
            "feature_id": f"{creature_id}_{_slugify(trait_name)}",
            "name": trait_name,
            "source": creature_id,
            "description": t.get("desc", ""),
        })

    # Parse structured actions
    actions = [parse_action(a, creature_id, "action") for a in _list(c.get("actions"))]
    bonus_actions = [parse_action(a, creature_id, "bonus_action") for a in _list(c.get("bonus_actions"))]
    reactions = [parse_reaction(a, creature_id) for a in _list(c.get("reactions"))]
    legendary_actions = [parse_action(a, creature_id, "legendary") for a in _list(c.get("legendary_actions"))]

    return {
        "creature_id": creature_id,
        "name": c.get("name", ""),
        "description": c.get("desc", "") or "",
        "creature_type": (c.get("type", "") or "").lower(),
        "size": (c.get("size", "") or "").lower(),
        "ability_scores": {
            "strength": c.get("strength", 0) or 0,
            "dexterity": c.get("dexterity", 0) or 0,
            "constitution": c.get("constitution", 0) or 0,
            "intelligence": c.get("intelligence", 0) or 0,
            "wisdom": c.get("wisdom", 0) or 0,
            "charisma": c.get("charisma", 0) or 0,
        },
        "armor_class": c.get("armor_class", 0) or 0,
        "armor_type": c.get("armor_desc", "") or "",
        "hp_dice_count": hp_count,
        "hp_die": hp_die,
        "hp_flat_bonus": hp_flat,
        "hp_average": c.get("hit_points", 0) or 0,
        "speed": speed,
        "senses": parse_senses(c.get("senses", "")),
        "resistances": parse_damage_list(c.get("damage_resistances", "")),
        "immunities": parse_damage_list(c.get("damage_immunities", "")),
        "vulnerabilities": parse_damage_list(c.get("damage_vulnerabilities", "")),
        "condition_immunities": parse_condition_immunities(c.get("condition_immunities", "")),
        "proficiency_bonus": proficiency_bonus_from_cr(cr_float),
        "traits": traits,
        "actions": actions,
        "bonus_actions": bonus_actions,
        "reactions": reactions,
        "legendary_actions": legendary_actions,
        "legendary_action_count": parse_legendary_action_count(c.get("legendary_desc", "")),
        "xp_value": XP_BY_CR.get(cr_float, 0),
        "challenge_rating": cr_str,
    }


def main():
    parser = argparse.ArgumentParser(description="Convert Open5e creature data to TTRPG engine format.")
    parser.add_argument("--output-dir", default="creatures", help="Directory to save creature JSON files")
    parser.add_argument("--local-file", help="Local JSON file containing Open5e creature data (instead of API)")
    parser.add_argument("--include-source", nargs="+", metavar="SOURCE",
                        help="Only include creatures from these document slugs (e.g., wotc-srd).")
    parser.add_argument("--group-by-source", action="store_true",
                        help="Save creatures into subdirectories named after their source slug")
    args = parser.parse_args()

    if args.local_file:
        creatures = load_local_creatures(Path(args.local_file))
    else:
        print("Fetching creatures from Open5e API (this may take a while)...")
        creatures = fetch_open5e_creatures()
        print(f"Fetched {len(creatures)} creatures.")

    if args.include_source:
        creatures = [c for c in creatures if c.get("document__slug", "") in args.include_source]
        print(f"Filtered to {len(creatures)} creatures matching sources: {args.include_source}")

    output_path = Path(args.output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    errors = 0

    for creature in creatures:
        try:
            converted = convert_creature(creature)
            validated = CreatureDefinition.model_validate(converted)
            filename = f"{converted['creature_id']}.json"

            if args.group_by_source:
                source_slug = creature.get("document__slug", "unknown")
                source_dir = output_path / source_slug
                source_dir.mkdir(parents=True, exist_ok=True)
                filepath = source_dir / filename
            else:
                filepath = output_path / filename

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(validated.model_dump(mode="json"), f, indent=2, ensure_ascii=False)
            print(f"Saved: {filepath}")

        except Exception as e:
            errors += 1
            print(f"Error converting creature {creature.get('name', 'unknown')}: {e}")

    print(f"Conversion complete. {len(creatures) - errors} succeeded, {errors} errors.")


if __name__ == "__main__":
    main()
