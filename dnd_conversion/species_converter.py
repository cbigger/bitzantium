"""
species_converter.py

Fetches race/subrace data from the Open5e API and transforms it into the
engine's Creature (species) and Race (variant/subrace) formats.

D&D "race" → engine Creature file (the species)
D&D "subrace" → engine Race file (optional overlay on the creature)

Usage:
    python species_converter.py [--output-dir OUTPUT_DIR]
                                [--local-file FILE]
                                [--include-source SOURCE [SOURCE ...]]
"""

import argparse
import json
import re
import requests
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from bitzantium_schemas.data import CreatureDefinition, RaceDefinition

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OPEN5E_API_BASE = "https://api.open5e.com/v1"
RACE_ENDPOINT = f"{OPEN5E_API_BASE}/races/"

ABILITY_MAP = {
    "strength": "strength",
    "dexterity": "dexterity",
    "constitution": "constitution",
    "intelligence": "intelligence",
    "wisdom": "wisdom",
    "charisma": "charisma",
    "str": "strength",
    "dex": "dexterity",
    "con": "constitution",
    "int": "intelligence",
    "wis": "wisdom",
    "cha": "charisma",
}

# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_open5e_races() -> List[Dict]:
    """Fetch all race data from the Open5e API (paginated)."""
    results = []
    url = f"{RACE_ENDPOINT}?limit=100"
    while url:
        print(f"Fetching {url}")
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        results.extend(data.get("results", []))
        url = data.get("next")
    return results


def load_local_races(file_path: Path) -> List[Dict]:
    """Load race data from a local JSON file."""
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "results" in data:
        return data["results"]
    if isinstance(data, list):
        return data
    return [data]

# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _slugify(text: str) -> str:
    return re.sub(r'[^a-z0-9]+', '_', text.lower()).strip('_')


def parse_asi(asi_list: List[Dict]) -> Dict[str, int]:
    """
    Convert Open5e asi list like [{"attributes": ["Constitution"], "value": 2}]
    into a dict of {ability: bonus}.
    Handles "Other" and "Choose" as best-effort.
    """
    result = {}
    for entry in (asi_list or []):
        value = entry.get("value", 0)
        for attr in entry.get("attributes", []):
            key = ABILITY_MAP.get(attr.lower(), attr.lower())
            if key in ("other", "choose", "any"):
                # Player's choice — store as "choice"
                result.setdefault("choice", 0)
                result["choice"] += value
            else:
                result[key] = result.get(key, 0) + value
    return result


def build_ability_scores(asi: Dict[str, int]) -> Dict[str, int]:
    """
    Build creature ability_scores from ASI dict.
    Base 10 + racial bonus for each ability.
    """
    scores = {
        "strength": 10,
        "dexterity": 10,
        "constitution": 10,
        "intelligence": 10,
        "wisdom": 10,
        "charisma": 10,
    }
    for ability, bonus in asi.items():
        if ability in scores:
            scores[ability] += bonus
    return scores


def build_ability_score_increases(asi: Dict[str, int]) -> List[Dict]:
    """
    Build race ability_score_increases list from ASI dict.
    """
    increases = []
    for ability, bonus in asi.items():
        if ability == "choice":
            # Represent player choice
            for _ in range(bonus):
                increases.append({"ability": "choice", "amount": 1})
        else:
            increases.append({"ability": ability, "amount": bonus})
    return increases


def parse_size(size_raw: str) -> str:
    """Normalize size string."""
    if not size_raw:
        return "medium"
    s = size_raw.strip().lower()
    valid = {"tiny", "small", "medium", "large", "huge", "gargantuan"}
    if s in valid:
        return s
    # Try to extract from longer text
    for v in valid:
        if v in s:
            return v
    return "medium"


def parse_speed(speed_dict: Dict) -> Dict[str, int]:
    """Build speed dict from Open5e speed field."""
    if not speed_dict or not isinstance(speed_dict, dict):
        return {"walk": 30, "swim": 0, "climb": 0, "fly": 0, "burrow": 0}
    return {
        "walk": speed_dict.get("walk", 30) or 0,
        "swim": speed_dict.get("swim", 0) or 0,
        "climb": speed_dict.get("climb", 0) or 0,
        "fly": speed_dict.get("fly", 0) or 0,
        "burrow": speed_dict.get("burrow", 0) or 0,
    }


def parse_darkvision(vision_str: str) -> int:
    """Extract darkvision distance from vision description text."""
    if not vision_str:
        return 0
    m = re.search(r'(\d+)\s*feet', vision_str, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return 0


def parse_traits_text(traits_str: str, source_id: str) -> List[Dict]:
    """
    Parse the markdown traits text into structured trait dicts.
    Open5e format: **_Trait Name._** Description text.\n\n**_Next Trait._** ...
    Also handles: **Trait Name** Description... (without underscores/periods)
    """
    if not traits_str:
        return []

    raw_traits = []
    # Split on bold markers that start a new trait
    parts = re.split(r'(?=\*\*_?[A-Z])', traits_str)

    for part in parts:
        part = part.strip()
        if not part:
            continue

        # Extract name and description
        # Try: **_Name._** Description
        m = re.match(r'\*\*_?(.+?)_?\.\*\*\s*(.*)', part, re.DOTALL)
        if not m:
            # Try: **Name** Description
            m = re.match(r'\*\*(.+?)\*\*\s*(.*)', part, re.DOTALL)
        if not m:
            continue

        name = m.group(1).strip().rstrip('.')
        # Clean markdown artifacts from name
        name = re.sub(r'[_*]+', '', name).strip().rstrip('.')
        desc = m.group(2).strip()

        # Clean up markdown from description
        desc = re.sub(r'\*+', '', desc)
        desc = re.sub(r'_([^_]+)_', r'\1', desc)
        desc = desc.strip()

        if name:
            raw_traits.append((name, desc))

    # Merge duplicate trait names (e.g., table + description for same trait)
    merged = {}
    order = []
    for name, desc in raw_traits:
        if name in merged:
            # Append description, separated by newline
            merged[name] = (merged[name] + "\n" + desc).strip()
        else:
            merged[name] = desc
            order.append(name)

    traits = []
    for name in order:
        traits.append({
            "feature_id": f"{source_id}_{_slugify(name)}",
            "name": name,
            "source": source_id,
            "description": merged[name],
        })

    return traits


def parse_resistances_from_traits(traits: List[Dict]) -> List[str]:
    """Extract damage resistances mentioned in trait descriptions."""
    resistances = []
    damage_types = {
        "acid", "bludgeoning", "cold", "fire", "force", "lightning",
        "necrotic", "piercing", "poison", "psychic", "radiant",
        "slashing", "thunder",
    }
    for trait in traits:
        desc = trait.get("description", "").lower()
        if "resistance" in desc:
            for dt in damage_types:
                if dt in desc:
                    if dt not in resistances:
                        resistances.append(dt)
    return resistances


def parse_languages_from_text(lang_str: str) -> Tuple[List[str], int]:
    """
    Parse languages description text into (fixed_languages, bonus_count).
    E.g., "You can speak, read, and write Common and Dwarvish." → (["common","dwarvish"], 0)

    Only parses the first sentence (up to the first period followed by a space
    or end-of-string) to avoid picking up language names from flavor text.
    """
    if not lang_str:
        return [], 0

    # Strip markdown
    text = re.sub(r'\*+_?|_?\*+', '', lang_str)
    text = re.sub(r'^Languages?\.\s*', '', text, flags=re.IGNORECASE)

    # Only use the first sentence — flavor text after the first period
    # contains language names used in non-language contexts (e.g., "Orc curses,
    # Elvish musical expressions, Dwarvish military phrases").
    first_sentence_match = re.match(r'([^.]*\.)', text)
    if first_sentence_match:
        text = first_sentence_match.group(1)

    # Extract "one extra language" / "one other language"
    bonus = 0
    choice_match = re.search(r'(one|two|three)\s+(?:extra|other|additional)\s+language', text, re.IGNORECASE)
    if choice_match:
        word = choice_match.group(1).lower()
        bonus = {"one": 1, "two": 2, "three": 3}.get(word, 1)

    # Extract named languages
    known_languages = {
        "common", "dwarvish", "elvish", "giant", "gnomish", "goblin",
        "halfling", "orc", "abyssal", "celestial", "draconic", "deep speech",
        "infernal", "primordial", "sylvan", "undercommon", "druidic",
        "thieves' cant", "auran", "aquan", "ignan", "terran",
    }

    found = []
    text_lower = text.lower()
    for lang in known_languages:
        if lang in text_lower:
            found.append(lang.replace(" ", "_").replace("'", ""))

    return found, bonus

# ---------------------------------------------------------------------------
# Converters
# ---------------------------------------------------------------------------

def convert_species(race_data: Dict) -> Dict:
    """
    Convert a D&D race (Open5e) into an engine Creature (species) file.
    The creature has the racial ASI baked into ability_scores,
    all species traits, speed, senses, resistances, etc.
    """
    slug = race_data.get("slug", "")
    creature_id = _slugify(race_data.get("name", slug))
    name = race_data.get("name", "")

    # ASI → baked into ability scores
    asi = parse_asi(race_data.get("asi", []))
    ability_scores = build_ability_scores(asi)

    # Speed
    speed = parse_speed(race_data.get("speed", {}))

    # Size
    size = parse_size(race_data.get("size_raw", ""))

    # Senses — darkvision from vision text
    darkvision = parse_darkvision(race_data.get("vision", ""))
    senses = {
        "passive_perception": 10,
        "darkvision_ft": darkvision,
        "blindsight_ft": 0,
        "tremorsense_ft": 0,
        "truesight_ft": 0,
    }

    # Traits from the traits markdown text
    traits = parse_traits_text(race_data.get("traits", ""), creature_id)

    # Languages — populate as structured fields, not a trait
    languages, bonus_langs = parse_languages_from_text(race_data.get("languages", ""))

    # Resistances extracted from trait descriptions
    resistances = parse_resistances_from_traits(traits)

    # Description — combine desc + age + alignment if available
    desc = race_data.get("desc", "") or ""
    # Strip markdown headers
    desc = re.sub(r'#+\s+.*?\n', '', desc).strip()
    # Strip bold/italic markdown
    desc = re.sub(r'\*+_?|_?\*+', '', desc).strip()

    return {
        "creature_id": creature_id,
        "name": name,
        "description": desc,
        "creature_type": "humanoid",
        "size": size,
        "ability_scores": ability_scores,
        "armor_class": 10,
        "armor_type": "none",
        "hp_dice_count": 1,
        "hp_die": 8,
        "hp_flat_bonus": 0,
        "hp_average": 5,
        "speed": speed,
        "senses": senses,
        "languages": languages,
        "bonus_languages": bonus_langs,
        "resistances": resistances,
        "immunities": [],
        "vulnerabilities": [],
        "condition_immunities": [],
        "proficiency_bonus": 2,
        "traits": traits,
        "actions": [],
        "bonus_actions": [],
        "reactions": [],
        "legendary_actions": [],
        "legendary_action_count": 0,
        "xp_value": 0,
        "challenge_rating": "0",
    }


def convert_subrace(subrace_data: Dict, parent_creature_id: str,
                    parent_languages: List[str], parent_bonus_langs: int) -> Dict:
    """
    Convert a D&D subrace (Open5e) into an engine Race file.
    Only contains the subrace's own additions — NOT the parent's data.
    """
    slug = subrace_data.get("slug", "")
    race_id = _slugify(subrace_data.get("name", slug))
    name = subrace_data.get("name", "")

    # Subrace ASI only (not parent's)
    asi = parse_asi(subrace_data.get("asi", []))
    ability_score_increases = build_ability_score_increases(asi)

    # Subrace-specific traits
    traits = parse_traits_text(subrace_data.get("traits", ""), race_id)

    # Senses override — only if subrace changes them
    senses_override = {}

    # Speed override — only if subrace changes it
    speed_override = {}

    # Description
    desc = subrace_data.get("desc", "") or ""
    desc = re.sub(r'\*+_?|_?\*+', '', desc).strip()

    return {
        "race_id": race_id,
        "name": name,
        "parent_creature_id": parent_creature_id,
        "description": desc,
        "ability_score_increases": ability_score_increases,
        "senses_override": senses_override,
        "speed_override": speed_override,
        "traits": traits,
        "proficiency_grants": [],
        "languages": parent_languages,
        "bonus_languages": parent_bonus_langs,
        "resistances": [],
        "immunities": [],
    }

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Convert Open5e race/subrace data to TTRPG engine creature/race format."
    )
    parser.add_argument("--output-dir", default="species",
                        help="Base directory to save output files")
    parser.add_argument("--local-file",
                        help="Local JSON file containing Open5e race data (instead of API)")
    parser.add_argument("--include-source", nargs="+", metavar="SOURCE",
                        help="Only include races from these document slugs (e.g., wotc-srd).")
    args = parser.parse_args()

    if args.local_file:
        races = load_local_races(Path(args.local_file))
    else:
        print("Fetching races from Open5e API...")
        races = fetch_open5e_races()
        print(f"Fetched {len(races)} races.")

    if args.include_source:
        races = [r for r in races if r.get("document__slug", "") in args.include_source]
        print(f"Filtered to {len(races)} races matching sources: {args.include_source}")

    output_base = Path(args.output_dir)
    output_base.mkdir(parents=True, exist_ok=True)
    errors = 0

    for race_data in races:
        try:
            # Convert the species (creature file)
            creature = convert_species(race_data)
            validated_creature = CreatureDefinition.model_validate(creature)
            creature_id = creature["creature_id"]

            # Create species directory
            species_dir = output_base / creature_id
            species_dir.mkdir(parents=True, exist_ok=True)

            # Write creature file
            creature_path = species_dir / f"{creature_id}.json"
            with open(creature_path, "w", encoding="utf-8") as f:
                json.dump(validated_creature.model_dump(mode="json"), f, indent=2, ensure_ascii=False)
            print(f"Saved creature: {creature_path}")

            # Parse parent languages for subraces
            parent_langs, parent_bonus = parse_languages_from_text(
                race_data.get("languages", "")
            )

            # Convert subraces (race files)
            subraces = race_data.get("subraces", []) or []
            if subraces:
                races_dir = species_dir / "races"
                races_dir.mkdir(parents=True, exist_ok=True)

                for sub in subraces:
                    # Filter subraces by source if requested
                    if args.include_source:
                        sub_source = sub.get("document__slug", "")
                        if sub_source not in args.include_source:
                            continue

                    try:
                        race_file = convert_subrace(
                            sub, creature_id, parent_langs, parent_bonus
                        )
                        validated_race = RaceDefinition.model_validate(race_file)
                        race_path = races_dir / f"{validated_race.race_id}.json"
                        with open(race_path, "w", encoding="utf-8") as f:
                            json.dump(validated_race.model_dump(mode="json"), f, indent=2, ensure_ascii=False)
                        print(f"Saved race: {race_path}")
                    except Exception as e:
                        errors += 1
                        print(f"Error converting subrace {sub.get('name', 'unknown')}: {e}")

        except Exception as e:
            errors += 1
            print(f"Error converting race {race_data.get('name', 'unknown')}: {e}")

    total = sum(1 for _ in output_base.rglob("*.json"))
    print(f"Conversion complete. {total} files written, {errors} errors.")


if __name__ == "__main__":
    main()
