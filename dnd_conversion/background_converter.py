"""
background_converter.py

Fetches background data from the Open5e API and transforms it into the
BackgroundDefinition format used by the TTRPG engine.

Usage:
    python background_converter.py [--output-dir OUTPUT_DIR]
                                   [--local-file FILE]
                                   [--include-source SOURCE [SOURCE ...]]

If --local-file is provided, reads from that JSON file instead of hitting the API.
--include-source filters content to only the given document slugs (e.g., wotc-srd).
"""

import argparse
import json
import re
import requests
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from bitzantium_schemas.data import BackgroundDefinition

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OPEN5E_API_BASE = "https://api.open5e.com/v1"
BACKGROUND_ENDPOINT = f"{OPEN5E_API_BASE}/backgrounds/"

KNOWN_SKILLS = {
    "acrobatics", "animal handling", "arcana", "athletics", "culture",
    "deception", "engineering", "history", "insight", "intimidation",
    "investigation", "medicine", "nature", "perception", "performance",
    "persuasion", "religion", "sleight of hand", "stealth", "survival",
}

# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_open5e_backgrounds() -> List[Dict]:
    """Fetch all background data from the Open5e API (paginated)."""
    results = []
    url = f"{BACKGROUND_ENDPOINT}?limit=100"
    while url:
        print(f"Fetching {url}")
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        results.extend(data.get("results", []))
        url = data.get("next")
    return results


def load_local_backgrounds(file_path: Path) -> List[Dict]:
    """Load background data from a local JSON file."""
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


def parse_skill_proficiencies(skill_str: str) -> List[Dict]:
    """
    Parse skill proficiency strings into proficiency_grants.

    Patterns found in Open5e data:
    - "Insight, Religion"                          → two fixed skills
    - "Deception, and either Culture, Insight, or Sleight of Hand."  → one fixed + choose 1
    - "Two of your choice."                        → choose 2 from all
    - "Stealth, and either Deception or Intimidation."  → one fixed + choose 1
    - "Insight plus one of your choice from among Intimidation or Persuasion" → one fixed + choose 1
    """
    if not skill_str:
        return []

    grants = []
    text = skill_str.strip().rstrip('.')

    # Pattern: "Two of your choice" / "Your choice of two from ..."
    choose_any_match = re.match(
        r'(?:your\s+choice\s+of\s+)?(?:two|three|four)\s+(?:of\s+your\s+choice|from\s+among\s+(.+))',
        text, re.IGNORECASE
    )
    if choose_any_match:
        word = re.match(r'(?:your\s+choice\s+of\s+)?(\w+)', text, re.IGNORECASE).group(1).lower()
        count_map = {"two": 2, "three": 3, "four": 4}
        choose = count_map.get(word, 2)
        options_str = choose_any_match.group(1)
        if options_str:
            options = _extract_skills(options_str)
            grants.append({"type": f"skill:{'|'.join(options)}", "choose": choose})
        else:
            grants.append({"type": "skill:any", "choose": choose})
        return grants

    # Pattern: "X, and either Y, Z, or W" / "X, and either Y or Z"
    either_match = re.search(
        r'(?:and\s+)?either\s+(.+)',
        text, re.IGNORECASE
    )

    if either_match:
        # Fixed skills are everything before "and either" / "either"
        before = text[:either_match.start()].strip().rstrip(',').strip()
        fixed_skills = _extract_skills(before)
        for skill in fixed_skills:
            grants.append({"type": f"skill:{skill}", "choose": 0})

        # Choice skills from the "either ... or ..." part
        choice_str = either_match.group(1)
        choice_skills = _extract_skills(choice_str)
        if choice_skills:
            grants.append({"type": f"skill:{'|'.join(choice_skills)}", "choose": 1})
        return grants

    # Pattern: "X plus one of your choice from among Y or Z"
    plus_match = re.search(
        r'plus\s+one\s+of\s+your\s+choice\s+from\s+among\s+(.+)',
        text, re.IGNORECASE
    )
    if plus_match:
        before = text[:plus_match.start()].strip().rstrip(',').strip()
        fixed_skills = _extract_skills(before)
        for skill in fixed_skills:
            grants.append({"type": f"skill:{skill}", "choose": 0})
        choice_skills = _extract_skills(plus_match.group(1))
        if choice_skills:
            grants.append({"type": f"skill:{'|'.join(choice_skills)}", "choose": 1})
        return grants

    # Pattern: "X plus your choice of one between Y or Z"
    plus_between_match = re.search(
        r'plus\s+your\s+choice\s+of\s+one\s+between\s+(.+)',
        text, re.IGNORECASE
    )
    if plus_between_match:
        before = text[:plus_between_match.start()].strip().rstrip(',').strip()
        fixed_skills = _extract_skills(before)
        for skill in fixed_skills:
            grants.append({"type": f"skill:{skill}", "choose": 0})
        choice_skills = _extract_skills(plus_between_match.group(1))
        if choice_skills:
            grants.append({"type": f"skill:{'|'.join(choice_skills)}", "choose": 1})
        return grants

    # Simple comma-separated list: "Insight, Religion"
    skills = _extract_skills(text)
    for skill in skills:
        grants.append({"type": f"skill:{skill}", "choose": 0})

    return grants


def _extract_skills(text: str) -> List[str]:
    """Extract known skill names from a text string."""
    # Normalize separators
    text = text.lower()
    text = re.sub(r'\band\b', ',', text)
    text = re.sub(r'\bor\b', ',', text)
    text = re.sub(r'\bplus\b', ',', text)

    skills = []
    for part in text.split(','):
        part = part.strip().rstrip('.')
        if part in KNOWN_SKILLS:
            skills.append(part.replace(' ', '_'))
    return skills


def parse_tool_proficiencies(tool_str: str) -> List[Dict]:
    """Parse tool proficiency strings into proficiency_grants."""
    if not tool_str or tool_str.lower() in ("none", "no additional tool proficiencies"):
        return []

    grants = []
    text = tool_str.strip().rstrip('.')

    # "Two of your choice"
    choose_match = re.match(r'(?:two|three)\s+of\s+your\s+choice', text, re.IGNORECASE)
    if choose_match:
        word = re.match(r'(\w+)', text, re.IGNORECASE).group(1).lower()
        count = {"two": 2, "three": 3}.get(word, 1)
        grants.append({"type": "tool:any", "choose": count})
        return grants

    # "One type of gaming set" or "One type of artisan's tools or one type of musical instrument"
    choose_one_match = re.search(r'one\s+(?:type\s+of\s+)?(.+)', text, re.IGNORECASE)
    if choose_one_match and ("your choice" in text.lower() or "one type" in text.lower()):
        options_raw = choose_one_match.group(1)
        options_raw = re.sub(r'\bone\s+(?:type\s+of\s+)?', '', options_raw, flags=re.IGNORECASE)
        options = [_slugify(o.strip()) for o in re.split(r',|or', options_raw) if o.strip()]
        if options:
            grants.append({"type": f"tool:{'|'.join(options)}", "choose": 1})
        return grants

    # Comma-separated list of specific tools
    tools = [t.strip() for t in re.split(r',', text) if t.strip()]
    for tool in tools:
        grants.append({"type": f"tool:{_slugify(tool)}", "choose": 0})

    return grants


def parse_languages(lang_str: str) -> Tuple[List[str], int]:
    """
    Parse languages string into (fixed_languages, bonus_languages_count).
    E.g., "Two of your choice" → ([], 2)
          "Sylvan" → (["sylvan"], 0)
          "One of your choice" → ([], 1)
    """
    if not lang_str or lang_str.lower() in ("none", "no additional languages"):
        return [], 0

    text = lang_str.strip().rstrip('.')

    # "Two of your choice", "One of your choice"
    choose_match = re.match(r'(one|two|three|four)\s+of\s+your\s+choice', text, re.IGNORECASE)
    if choose_match:
        word = choose_match.group(1).lower()
        count = {"one": 1, "two": 2, "three": 3, "four": 4}.get(word, 1)
        return [], count

    # Might be a mix: "Thieves' Cant" or "Sylvan"
    fixed = []
    bonus = 0
    for part in re.split(r',|;', text):
        part = part.strip()
        if not part:
            continue
        if re.match(r'(?:one|two|three)\s+(?:additional\s+)?(?:language|of\s+your\s+choice)', part, re.IGNORECASE):
            word = re.match(r'(\w+)', part).group(1).lower()
            bonus += {"one": 1, "two": 2, "three": 3}.get(word, 1)
        elif "your choice" in part.lower():
            bonus += 1
        else:
            fixed.append(part.lower().replace("'", "").replace(" ", "_"))

    return fixed, bonus


def parse_equipment(equip_str: str) -> Tuple[List[str], int]:
    """
    Parse equipment string into (item_ids, starting_gp).
    The equipment field is a comma-separated description with a gold amount at the end.
    """
    if not equip_str:
        return [], 0

    items = []
    gp = 0

    # Extract GP: "a pouch containing 15 gp" or "and 10 gp"
    gp_match = re.search(r'(\d+)\s*gp', equip_str)
    if gp_match:
        gp = int(gp_match.group(1))

    # Split by comma and "and"
    # Remove the GP clause first
    clean = re.sub(r'(?:a\s+)?(?:belt\s+)?pouch\s+containing\s+\d+\s*gp', '', equip_str)
    clean = re.sub(r'and\s+\d+\s*gp', '', clean)

    # Split on ", and " then ","
    parts = re.split(r',\s*(?:and\s+)?|\s+and\s+', clean)

    for part in parts:
        part = part.strip().rstrip('.')
        if not part:
            continue
        # Remove leading articles and quantities
        item = re.sub(r'^(?:a\s+set\s+of\s+|a\s+|an\s+|one\s+|\d+\s+(?:sticks?\s+of\s+)?)', '', part, flags=re.IGNORECASE)
        item = item.strip()
        if not item or len(item) < 2:
            continue
        # Remove parenthetical descriptions
        item = re.sub(r'\([^)]*\)', '', item).strip()
        if item:
            items.append(_slugify(item))

    return items, gp


def convert_background(bg: Dict) -> Dict:
    """Transform a single Open5e background into engine BackgroundDefinition format."""
    name = bg.get("name", "")
    bg_id = _slugify(name)

    # Proficiency grants
    prof_grants = []
    prof_grants.extend(parse_skill_proficiencies(bg.get("skill_proficiencies", "")))
    prof_grants.extend(parse_tool_proficiencies(bg.get("tool_proficiencies", "")))

    # Languages
    _fixed_langs, bonus_languages = parse_languages(bg.get("languages", ""))
    # If there are fixed languages, add them as proficiency grants
    for lang in _fixed_langs:
        prof_grants.append({"type": f"language:{lang}", "choose": 0})

    # Equipment
    starting_equipment, starting_gp = parse_equipment(bg.get("equipment", ""))

    # Feature
    features = []
    feature_name = bg.get("feature", "")
    feature_desc = bg.get("feature_desc", "")
    if feature_name:
        features.append({
            "feature_id": _slugify(feature_name),
            "name": feature_name,
            "source": bg_id,
            "description": feature_desc or "",
        })

    return {
        "background_id": bg_id,
        "name": name,
        "description": (bg.get("desc", "") or "").strip(),
        "proficiency_grants": prof_grants,
        "bonus_languages": bonus_languages,
        "starting_equipment": starting_equipment,
        "starting_currency_gp": starting_gp,
        "features": features,
    }


def main():
    parser = argparse.ArgumentParser(description="Convert Open5e background data to TTRPG engine format.")
    parser.add_argument("--output-dir", default="backgrounds", help="Directory to save background JSON files")
    parser.add_argument("--local-file", help="Local JSON file containing Open5e background data (instead of API)")
    parser.add_argument("--include-source", nargs="+", metavar="SOURCE",
                        help="Only include backgrounds from these document slugs (e.g., wotc-srd).")
    args = parser.parse_args()

    if args.local_file:
        backgrounds = load_local_backgrounds(Path(args.local_file))
    else:
        print("Fetching backgrounds from Open5e API...")
        backgrounds = fetch_open5e_backgrounds()
        print(f"Fetched {len(backgrounds)} backgrounds.")

    if args.include_source:
        backgrounds = [b for b in backgrounds if b.get("document__slug", "") in args.include_source]
        print(f"Filtered to {len(backgrounds)} backgrounds matching sources: {args.include_source}")

    output_path = Path(args.output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    errors = 0

    for bg in backgrounds:
        try:
            converted = convert_background(bg)
            validated = BackgroundDefinition.model_validate(converted)
            filename = f"{converted['background_id']}.json"
            filepath = output_path / filename

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(validated.model_dump(mode="json"), f, indent=2, ensure_ascii=False)
            print(f"Saved: {filepath}")

        except Exception as e:
            errors += 1
            print(f"Error converting background {bg.get('name', 'unknown')}: {e}")

    print(f"Conversion complete. {len(backgrounds) - errors} succeeded, {errors} errors.")


if __name__ == "__main__":
    main()
