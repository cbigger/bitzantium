"""
transform_open5e_creatures.py

Fetches creature data from the Open5e API and transforms it into the
CreatureDefinition format used by the TTRPG engine.

Usage:
    python transform_open5e_creatures.py [--output-dir OUTPUT_DIR]
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

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OPEN5E_API_BASE = "https://api.open5e.com"
MONSTER_ENDPOINT = f"{OPEN5E_API_BASE}/monsters/"

# Mapping of CR to proficiency bonus (for CR > 0)
# Source: 5e DMG / standard table
CR_TO_PB = {
    (0, 0): 2,
    (0.125, 0.125): 2,
    (0.25, 0.25): 2,
    (0.5, 0.5): 2,
    (1, 3): 2,
    (4, 4): 2,
    (5, 8): 3,
    (9, 12): 4,
    (13, 16): 5,
    (17, 20): 6,
    (21, 24): 7,
    (25, 28): 8,
    (29, 30): 9,
}

# ---------------------------------------------------------------------------
# Helper functions
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
    # The Open5e API returns a dict with a "results" key when fetching all creatures
    if isinstance(data, dict) and "results" in data:
        return data["results"]
    if isinstance(data, list):
        return data
    return [data]  # single creature object

def parse_hit_dice(hit_dice_str: str) -> Tuple[int, int, int]:
    """
    Parse a hit dice string like "10d12+50" or "13d12+52" into (count, die, flat_bonus).
    Returns (0,0,0) if parsing fails.
    """
    match = re.match(r'(\d+)d(\d+)(?:\+(\d+))?', hit_dice_str)
    if match:
        count = int(match.group(1))
        die = int(match.group(2))
        flat = int(match.group(3)) if match.group(3) else 0
        return count, die, flat
    return 0, 0, 0

def parse_senses(senses_str: str) -> Dict[str, int]:
    """
    Parse senses string like "darkvision 60 ft., tremorsense 30 ft., passive Perception 15"
    into a dict with keys: passive_perception, darkvision_ft, blindsight_ft, tremorsense_ft, truesight_ft.
    """
    senses = {
        "passive_perception": 0,
        "darkvision_ft": 0,
        "blindsight_ft": 0,
        "tremorsense_ft": 0,
        "truesight_ft": 0,
    }
    if not senses_str:
        return senses
    # Split by comma
    parts = [p.strip() for p in senses_str.split(',')]
    for part in parts:
        part_lower = part.lower()
        if "passive perception" in part_lower:
            match = re.search(r'(\d+)', part)
            if match:
                senses["passive_perception"] = int(match.group(1))
        elif "darkvision" in part_lower:
            match = re.search(r'(\d+)', part)
            if match:
                senses["darkvision_ft"] = int(match.group(1))
        elif "blindsight" in part_lower:
            match = re.search(r'(\d+)', part)
            if match:
                senses["blindsight_ft"] = int(match.group(1))
        elif "tremorsense" in part_lower:
            match = re.search(r'(\d+)', part)
            if match:
                senses["tremorsense_ft"] = int(match.group(1))
        elif "truesight" in part_lower:
            match = re.search(r'(\d+)', part)
            if match:
                senses["truesight_ft"] = int(match.group(1))
    return senses

def parse_damage_list(damage_str: str) -> List[str]:
    """
    Convert a string like "acid; bludgeoning, piercing, and slashing from nonmagical attacks"
    into a list of cleaned damage types.
    """
    if not damage_str or damage_str == "":
        return []
    # Split by semicolon first, then commas
    items = []
    for part in damage_str.split(';'):
        part = part.strip()
        if ',' in part:
            # Handle comma-separated list
            for sub in part.split(','):
                sub = sub.strip()
                if sub and sub not in items:
                    items.append(sub)
        else:
            if part and part not in items:
                items.append(part)
    # Clean up common phrases like "and" etc.
    cleaned = []
    for item in items:
        # Remove "and" and similar
        item = re.sub(r'\band\b', '', item).strip()
        if item:
            cleaned.append(item)
    return cleaned

def parse_condition_immunities(cond_str: str) -> List[str]:
    """
    Parse condition immunities string like "charmed, frightened" into a list.
    """
    if not cond_str:
        return []
    # Split by commas
    items = [c.strip() for c in cond_str.split(',') if c.strip()]
    return items

def compute_proficiency_bonus(cr_str: str) -> int:
    """
    Compute proficiency bonus from challenge rating string (e.g., "7" or "1/2").
    Uses standard 5e table.
    """
    # Parse CR string into a float
    if '/' in cr_str:
        num, den = cr_str.split('/')
        cr = float(num) / float(den)
    else:
        cr = float(cr_str)

    # Handle fractional CR less than 1 (0.125, 0.25, 0.5)
    if cr < 1:
        if cr <= 0.125:
            return 2
        elif cr <= 0.25:
            return 2
        elif cr <= 0.5:
            return 2
        else:
            return 2

    # For integer-like CRs, round to nearest integer (5e uses whole numbers for PB)
    cr_int = int(cr)
    for (low, high), pb in CR_TO_PB.items():
        if low <= cr_int <= high:
            return pb
    # Fallback (should not happen)
    return 2

def parse_legendary_action_count(legendary_desc: str) -> int:
    """
    Extract the number of legendary actions from the description, e.g.,
    "The aatxe can take 3 legendary actions, choosing from the options below."
    Returns 0 if not found.
    """
    if not legendary_desc:
        return 0
    match = re.search(r'(\d+)\s+legendary actions', legendary_desc, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return 0

def compute_xp(cr: float) -> int:
    """
    Compute XP from CR based on standard 5e table.
    This is optional; you can leave as 0 if not needed.
    """
    # Simplified mapping; you can expand as needed
    xp_map = {
        0: 0,
        0.125: 25,
        0.25: 50,
        0.5: 100,
        1: 200,
        2: 450,
        3: 700,
        4: 1100,
        5: 1800,
        6: 2300,
        7: 2900,
        8: 3900,
        9: 5000,
        10: 5900,
        11: 7200,
        12: 8400,
        13: 10000,
        14: 11500,
        15: 13000,
        16: 15000,
        17: 18000,
        18: 20000,
        19: 22000,
        20: 25000,
        21: 33000,
        22: 41000,
        23: 50000,
        24: 62000,
        25: 75000,
        26: 90000,
        27: 105000,
        28: 120000,
        29: 135000,
        30: 155000,
    }
    return xp_map.get(cr, 0)

def convert_creature(open5e_creature: Dict) -> Dict:
    """
    Transform a single Open5e creature into our CreatureDefinition format.
    """
    # Basic fields
    creature_id = open5e_creature.get("slug", "")
    name = open5e_creature.get("name", "")
    description = open5e_creature.get("desc", "")
    creature_type = open5e_creature.get("type", "")
    size = open5e_creature.get("size", "")

    # Ability scores
    ability_scores = {
        "strength": open5e_creature.get("strength", 0),
        "dexterity": open5e_creature.get("dexterity", 0),
        "constitution": open5e_creature.get("constitution", 0),
        "intelligence": open5e_creature.get("intelligence", 0),
        "wisdom": open5e_creature.get("wisdom", 0),
        "charisma": open5e_creature.get("charisma", 0),
    }

    armor_class = open5e_creature.get("armor_class", 0)
    armor_type = open5e_creature.get("armor_desc", "")

    # Hit dice
    hit_dice_str = open5e_creature.get("hit_dice", "")
    hp_count, hp_die, hp_flat = parse_hit_dice(hit_dice_str)
    hp_average = open5e_creature.get("hit_points", 0)

    # Speed
    speed_dict = open5e_creature.get("speed", {})
    speed = {
        "walk": speed_dict.get("walk", 0),
        "swim": speed_dict.get("swim", 0),
        "climb": speed_dict.get("climb", 0),
        "fly": speed_dict.get("fly", 0),
        "burrow": speed_dict.get("burrow", 0),
    }

    # Senses
    senses_str = open5e_creature.get("senses", "")
    senses = parse_senses(senses_str)

    # Resistances, immunities, vulnerabilities
    resistances = parse_damage_list(open5e_creature.get("damage_resistances", ""))
    immunities = parse_damage_list(open5e_creature.get("damage_immunities", ""))
    vulnerabilities = parse_damage_list(open5e_creature.get("damage_vulnerabilities", ""))
    condition_immunities = parse_condition_immunities(open5e_creature.get("condition_immunities", ""))

    # Proficiency bonus
    cr_str = open5e_creature.get("challenge_rating", "0")
    proficiency_bonus = compute_proficiency_bonus(cr_str)

    # Traits (special_abilities)
    special_abilities = open5e_creature.get("special_abilities", [])
    traits = []
    for trait in special_abilities:
        trait_name = trait.get("name", "")
        trait_desc = trait.get("desc", "")
        traits.append({
            "feature_id": f"{creature_id}_{trait_name.lower().replace(' ', '_')}",
            "name": trait_name,
            "source": creature_id,
            "description": trait_desc,
        })

    # Actions, bonus actions, reactions, legendary actions
    # These are already in the format expected by CreatureDefinition (list of dicts)
    actions = open5e_creature.get("actions", [])
    bonus_actions = open5e_creature.get("bonus_actions", [])
    reactions = open5e_creature.get("reactions", [])
    legendary_actions = open5e_creature.get("legendary_actions", [])
    legendary_action_count = parse_legendary_action_count(open5e_creature.get("legendary_desc", ""))

    # XP value
    cr_float = open5e_creature.get("cr", 0.0)
    xp_value = compute_xp(cr_float)

    # Build final creature dict
    creature = {
        "creature_id": creature_id,
        "name": name,
        "description": description,
        "creature_type": creature_type,
        "size": size,
        "ability_scores": ability_scores,
        "armor_class": armor_class,
        "armor_type": armor_type,
        "hp_dice_count": hp_count,
        "hp_die": hp_die,
        "hp_flat_bonus": hp_flat,
        "hp_average": hp_average,
        "speed": speed,
        "senses": senses,
        "resistances": resistances,
        "immunities": immunities,
        "vulnerabilities": vulnerabilities,
        "condition_immunities": condition_immunities,
        "proficiency_bonus": proficiency_bonus,
        "traits": traits,
        "actions": actions,
        "bonus_actions": bonus_actions,
        "reactions": reactions,
        "legendary_actions": legendary_actions,
        "legendary_action_count": legendary_action_count,
        "xp_value": xp_value,
        "challenge_rating": cr_str,
    }
    return creature

def main():
    parser = argparse.ArgumentParser(description="Convert Open5e creature data to TTRPG engine format.")
    parser.add_argument("--output-dir", default="creatures", help="Directory to save creature JSON files")
    parser.add_argument("--local-file", help="Local JSON file containing Open5e creature data (instead of API)")
    parser.add_argument("--include-source", nargs="+", metavar="SOURCE",
                        help="Only include creatures from these document slugs (e.g., wotc-srd). If omitted, all sources are included.")
    parser.add_argument("--group-by-source", action="store_true",
                        help="Save creatures into subdirectories named after their source slug")
    args = parser.parse_args()

    if args.local_file:
        creatures = load_local_creatures(Path(args.local_file))
    else:
        print("Fetching creatures from Open5e API (this may take a while)...")
        creatures = fetch_open5e_creatures()
        print(f"Fetched {len(creatures)} creatures.")

    # Filter by source if requested
    if args.include_source:
        filtered = []
        for c in creatures:
            src = c.get("document__slug", "")
            if src in args.include_source:
                filtered.append(c)
        creatures = filtered
        print(f"Filtered to {len(creatures)} creatures matching sources: {args.include_source}")

    output_path = Path(args.output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    for creature in creatures:
        try:
            converted = convert_creature(creature)
            filename = f"{converted['creature_id']}.json"

            if args.group_by_source:
                source_slug = creature.get("document__slug", "unknown")
                source_dir = output_path / source_slug
                source_dir.mkdir(parents=True, exist_ok=True)
                filepath = source_dir / filename
            else:
                filepath = output_path / filename

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(converted, f, indent=2, ensure_ascii=False)
            print(f"Saved: {filepath}")

        except Exception as e:
            print(f"Error converting creature {creature.get('name', 'unknown')}: {e}")

    print("Conversion complete.")

if __name__ == "__main__":
    main()
