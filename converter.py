"""
transform_open5e_classes.py

Fetches class data from the Open5e API and transforms it into the
ClassDefinition format used by the TTRPG engine.

Usage:
    python transform_open5e_classes.py [--output-dir OUTPUT_DIR]
                                       [--local-file FILE]
                                       [--include-source SOURCE [SOURCE ...]]
                                       [--save-subclasses]

If --local-file is provided, reads from that JSON file instead of hitting the API.
--include-source filters content to only the given document slugs (e.g., wotc-srd).
--save-subclasses writes subclass definitions into per‑class subdirectories.
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
CLASS_ENDPOINT = f"{OPEN5E_API_BASE}/classes/"

# Mapping from Open5e proficiency strings to our internal type strings
PROFICIENCY_MAP = {
    "light armor": "armor:light",
    "medium armor": "armor:medium",
    "heavy armor": "armor:heavy",
    "shields": "armor:shields",
    "simple weapons": "weapons:simple",
    "martial weapons": "weapons:martial",
    "strength": "saving_throw:strength",
    "dexterity": "saving_throw:dexterity",
    "constitution": "saving_throw:constitution",
    "intelligence": "saving_throw:intelligence",
    "wisdom": "saving_throw:wisdom",
    "charisma": "saving_throw:charisma",
}

# Known skill names (lowercase) – expand as needed
KNOWN_SKILLS = {
    "acrobatics", "animal handling", "arcana", "athletics", "deception",
    "history", "insight", "intimidation", "investigation", "medicine",
    "nature", "perception", "performance", "persuasion", "religion",
    "sleight of hand", "stealth", "survival"
}

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def fetch_open5e_classes() -> List[Dict]:
    """Fetch all class data from the Open5e API."""
    response = requests.get(CLASS_ENDPOINT)
    response.raise_for_status()
    data = response.json()
    return data.get("results", [])

def load_local_classes(file_path: Path) -> List[Dict]:
    """Load class data from a local JSON file (expects Open5e format)."""
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # The Open5e API returns a dict with a "results" key when fetching all classes
    if isinstance(data, dict) and "results" in data:
        return data["results"]
    if isinstance(data, list):
        return data
    return [data]  # single class object

def parse_table(table_str: str) -> List[Dict[str, str]]:
    """
    Parse a markdown table string into a list of row dictionaries.
    Assumes first row is headers, second is separator (|---|...|).
    Returns list of dicts with header keys.
    """
    lines = [line.strip() for line in table_str.split('\n') if line.strip()]
    if not lines:
        return []

    # Find first line that contains '|' – that's the header
    header_line = None
    data_start = 0
    for i, line in enumerate(lines):
        if '|' in line and not re.match(r'^[\s\|:-]+$', line):  # not a separator line
            header_line = line
            data_start = i + 1
            break
    if not header_line:
        return []

    headers = [h.strip() for h in header_line.split('|') if h.strip()]

    # Skip separator line (if present) – it often follows header
    while data_start < len(lines) and re.match(r'^[\s\|:-]+$', lines[data_start]):
        data_start += 1

    rows = []
    for line in lines[data_start:]:
        if '|' not in line:
            continue
        cells = [cell.strip() for cell in line.split('|') if cell.strip()]
        if len(cells) < len(headers):
            # Might have missing leading/trailing empty cells; pad with empty strings
            cells = cells + [''] * (len(headers) - len(cells))
        elif len(cells) > len(headers):
            # Some tables have extra cells; truncate
            cells = cells[:len(headers)]
        row = {headers[i]: cells[i] for i in range(len(headers))}
        rows.append(row)
    return rows

def parse_features_from_cell(cell: str, class_slug: str, level: int) -> List[Dict]:
    """
    Convert a features column string into a list of Feature dicts.
    Features may be separated by commas, sometimes with spaces.
    """
    if not cell or cell == '-' or cell == '—':
        return []
    # Some entries have multiple features separated by commas
    parts = [p.strip() for p in cell.split(',')]
    features = []
    for part in parts:
        # Remove trailing "feature" or "Feature" sometimes?
        # Create a feature_id: class_level_feature_slug
        feat_slug = part.lower().replace(' ', '_').replace(',', '').replace('(', '').replace(')', '')
        # Ensure it's not empty
        if feat_slug:
            features.append({
                "feature_id": f"{class_slug}_{level}_{feat_slug}",
                "name": part,
                "source": class_slug,
                "description": ""   # Placeholder; could be fetched later
            })
    return features

def map_proficiencies(open5e_class: Dict) -> List[Dict]:
    """
    Convert Open5e's proficiency representation to our ProficiencyGrant list.
    """
    grants = []

    # Armor, weapons, tools, saving throws are separate fields
    armor_profs = open5e_class.get("prof_armor", "")
    if armor_profs and armor_profs != "None":
        for prof in [p.strip() for p in armor_profs.split(',')]:
            mapped = PROFICIENCY_MAP.get(prof.lower())
            if mapped:
                grants.append({"type": mapped, "choose": 0})
            else:
                grants.append({"type": prof.lower(), "choose": 0})

    weapon_profs = open5e_class.get("prof_weapons", "")
    if weapon_profs and weapon_profs != "None":
        for prof in [p.strip() for p in weapon_profs.split(',')]:
            mapped = PROFICIENCY_MAP.get(prof.lower())
            if mapped:
                grants.append({"type": mapped, "choose": 0})
            else:
                grants.append({"type": prof.lower(), "choose": 0})

    tool_profs = open5e_class.get("prof_tools", "")
    if tool_profs and tool_profs != "None":
        for prof in [p.strip() for p in tool_profs.split(',')]:
            # Tools are not in PROFICIENCY_MAP; keep as is
            grants.append({"type": prof.lower(), "choose": 0})

    save_profs = open5e_class.get("prof_saving_throws", "")
    if save_profs and save_profs != "None":
        for prof in [p.strip() for p in save_profs.split(',')]:
            mapped = PROFICIENCY_MAP.get(prof.lower())
            if mapped:
                grants.append({"type": mapped, "choose": 0})
            else:
                grants.append({"type": prof.lower(), "choose": 0})

    # Skill choices
    skill_desc = open5e_class.get("prof_skills", "")
    if skill_desc and skill_desc != "None":
        # Format: "Choose two from Acrobatics, Animal Handling, ..."
        match = re.match(r"Choose (\d+) from (.*)", skill_desc)
        if match:
            choose = int(match.group(1))
            skills_str = match.group(2)
            skills = [s.strip().lower() for s in skills_str.split(',')]
            # Filter only known skills (should all be valid)
            valid_skills = [s for s in skills if s in KNOWN_SKILLS]
            if valid_skills:
                grants.append({
                    "type": f"skill:{'|'.join(valid_skills)}",
                    "choose": choose
                })
            else:
                # Fallback: store as raw string
                grants.append({"type": skill_desc, "choose": choose})
        else:
            # Could be a simple list like "Choose any three"
            grants.append({"type": skill_desc, "choose": 0})

    return grants

def map_starting_equipment(open5e_class: Dict) -> List[str]:
    """
    Parse the equipment field into a list of item slugs.
    The equipment field is a markdown bullet list with item names.
    We'll extract the raw names (e.g., "chain mail") and use them as item IDs.
    For better matching, you might want to map to known item IDs later.
    """
    equipment_str = open5e_class.get("equipment", "")
    items = []
    if equipment_str:
        # Extract lines that start with * or - (bullet points)
        for line in equipment_str.split('\n'):
            line = line.strip()
            if line.startswith('*') or line.startswith('-'):
                # Remove leading bullet and whitespace
                item = re.sub(r'^[\*\-\s]+', '', line)
                # Sometimes there are options like (*a*) a greataxe or (*b*) any martial weapon
                # We'll just take the first option for simplicity
                if '(' in item and ')' in item:
                    # Extract after the option, e.g., "a greataxe"
                    item = item.split(')')[-1].strip()
                # Remove trailing "or (*b*) ..." if present (keep only first)
                if ' or ' in item:
                    item = item.split(' or ')[0].strip()
                items.append(item.lower().replace(' ', '_'))
    return items

def parse_level_progression(open5e_class: Dict) -> List[Dict]:
    """
    Build level_progression from the `table` markdown field.
    Returns a list of 20 level entries.
    """
    table_str = open5e_class.get("table", "")
    if not table_str:
        # Fallback: generate empty progression with just proficiency bonus?
        return []

    rows = parse_table(table_str)
    class_slug = open5e_class.get("slug", "unknown")
    progression = []

    for row in rows:
        # Row might contain 'Level' as a string like "1st" or "1"
        level_str = row.get("Level", "")
        if not level_str:
            continue
        # Extract number: "1st" -> 1
        level_match = re.match(r'(\d+)', level_str)
        if not level_match:
            continue
        level = int(level_match.group(1))

        # Proficiency bonus column may be named "Proficiency Bonus" or "PB"
        prof_col = next((col for col in ["Proficiency Bonus", "PB"] if col in row), None)
        if prof_col:
            prof_str = row[prof_col]
            prof_bonus = int(prof_str) if prof_str else 0
        else:
            prof_bonus = 0

        # Features column – might be "Features" or "Feature"
        feat_col = next((col for col in ["Features", "Feature"] if col in row), None)
        if feat_col:
            features = parse_features_from_cell(row[feat_col], class_slug, level)
        else:
            features = []

        # Spell slots: look for columns "1st", "2nd", ..., "9th"
        spell_slots = {}
        for slot_level in range(1, 10):
            col_name = f"{slot_level}st" if slot_level == 1 else f"{slot_level}nd" if slot_level == 2 else f"{slot_level}rd" if slot_level == 3 else f"{slot_level}th"
            if col_name in row and row[col_name].strip() not in ('-', '—', ''):
                try:
                    slot_val = int(row[col_name].strip())
                    spell_slots[slot_level] = slot_val
                except ValueError:
                    pass

        # Cantrips known
        cantrips_col = next((col for col in ["Cantrips Known", "Cantrips"] if col in row), None)
        cantrips_knowable = None
        if cantrips_col and row[cantrips_col].strip() not in ('-', '—', ''):
            try:
                cantrips_knowable = int(row[cantrips_col].strip())
            except ValueError:
                pass

        # Spells known
        spells_col = next((col for col in ["Spells Known", "Spells"] if col in row), None)
        spells_knowable = None
        if spells_col and row[spells_col].strip() not in ('-', '—', ''):
            try:
                spells_knowable = int(row[spells_col].strip())
            except ValueError:
                pass

        progression.append({
            "level": level,
            "proficiency_bonus": prof_bonus,
            "features": features,
            "resource_grants": [],   # Not extracted from Open5e
            "resource_updates": [],  # Not extracted
            "spell_slots": spell_slots if spell_slots else None,
            "cantrips_knowable": cantrips_knowable,
            "spells_knowable": spells_knowable
        })

    # Ensure 20 levels (fill missing levels with empty entries)
    full_progression = []
    for lvl in range(1, 21):
        existing = next((p for p in progression if p["level"] == lvl), None)
        if existing:
            full_progression.append(existing)
        else:
            # Create a blank level
            full_progression.append({
                "level": lvl,
                "proficiency_bonus": 0,
                "features": [],
                "resource_grants": [],
                "resource_updates": [],
                "spell_slots": None,
                "cantrips_knowable": None,
                "spells_knowable": None
            })
    return full_progression

def convert_class(open5e_class: Dict) -> Dict:
    """
    Transform a single Open5e class into our ClassDefinition format.
    """
    hit_dice_str = open5e_class.get("hit_dice", "")
    # Extract number from "1d10"
    hit_die = 0
    if hit_dice_str:
        match = re.match(r'(\d+)d\d+', hit_dice_str)
        if match:
            hit_die = int(match.group(1))

    class_def = {
        "class_id": open5e_class.get("slug", ""),
        "name": open5e_class.get("name", ""),
        "description": open5e_class.get("desc", "").strip(),
        "hit_die": hit_die,
        "proficiency_grants": map_proficiencies(open5e_class),
        "starting_equipment": map_starting_equipment(open5e_class),
        "starting_equipment_currency_gp": 0,
        "spellcasting_ability": open5e_class.get("spellcasting_ability", ""),
        "spell_prepare_style": "",   # Not provided; can be determined by class
        "level_progression": parse_level_progression(open5e_class),
        "subclass_level": 3,         # Default; can be adjusted
    }
    return class_def

def save_subclasses(open5e_class: Dict, output_dir: Path, include_sources: Optional[List[str]] = None):
    """
    Generate SubclassDefinition files for each archetype.
    Saves them into a subdirectory named <class_id>_subclasses under output_dir.
    If include_sources is given, only archetypes whose document__slug is in that list are saved.
    """
    archetypes = open5e_class.get("archetypes", [])
    if not archetypes:
        return

    class_slug = open5e_class.get("slug", "")
    subclass_dir = output_dir / f"{class_slug}_subclasses"
    subclass_dir.mkdir(parents=True, exist_ok=True)

    for arch in archetypes:
        # Filter by source if requested
        if include_sources is not None:
            arch_source = arch.get("document__slug", "")
            if arch_source not in include_sources:
                continue

        subclass_id = arch.get("slug", "")
        if not subclass_id:
            continue

        subclass = {
            "subclass_id": subclass_id,
            "name": arch.get("name", ""),
            "parent_class_id": class_slug,
            "granted_at_level": 3,  # Could be extracted from description
            "description": arch.get("desc", "").strip(),
            "spellcasting_ability": open5e_class.get("spellcasting_ability", ""),
            "spell_prepare_style": "",
            "level_progression": []  # Subclass progression not in Open5e; you may need to create manually
        }
        with open(subclass_dir / f"{subclass_id}.json", "w", encoding="utf-8") as f:
            json.dump(subclass, f, indent=2, ensure_ascii=False)
        print(f"Saved subclass: {subclass_dir / f'{subclass_id}.json'}")

def main():
    parser = argparse.ArgumentParser(description="Convert Open5e class data to TTRPG engine format.")
    parser.add_argument("--output-dir", default="classes", help="Directory to save class JSON files")
    parser.add_argument("--local-file", help="Local JSON file containing Open5e class data (instead of API)")
    parser.add_argument("--save-subclasses", action="store_true", help="Also save subclass definitions")
    parser.add_argument("--include-source", nargs="+", metavar="SOURCE",
                        help="Only include content from these document slugs (e.g., wotc-srd). If omitted, all sources are included.")
    args = parser.parse_args()

    if args.local_file:
        classes = load_local_classes(Path(args.local_file))
    else:
        print("Fetching classes from Open5e API...")
        classes = fetch_open5e_classes()
        print(f"Fetched {len(classes)} classes.")

    # Filter classes by source if requested
    if args.include_source:
        filtered_classes = []
        for cls in classes:
            cls_source = cls.get("document__slug", "")
            if cls_source in args.include_source:
                filtered_classes.append(cls)
        classes = filtered_classes
        print(f"Filtered to {len(classes)} classes matching sources: {args.include_source}")

    output_path = Path(args.output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    for cls in classes:
        try:
            converted = convert_class(cls)
            file_name = f"{converted['class_id']}.json"
            with open(output_path / file_name, "w", encoding="utf-8") as f:
                json.dump(converted, f, indent=2, ensure_ascii=False)
            print(f"Saved class: {file_name}")

            if args.save_subclasses:
                save_subclasses(cls, output_path, args.include_source)

        except Exception as e:
            print(f"Error converting class {cls.get('name', 'unknown')}: {e}")

    print("Conversion complete.")

if __name__ == "__main__":
    main()
