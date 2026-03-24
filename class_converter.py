"""
class_converter.py

Fetches class data from the Open5e API and transforms it into the
ClassDefinition and SubclassDefinition formats used by the TTRPG engine.

Usage:
    python class_converter.py [--output-dir OUTPUT_DIR]
                              [--local-file FILE]
                              [--include-source SOURCE [SOURCE ...]]
                              [--save-subclasses]
"""

import argparse
import json
import re
import requests
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from data import ClassDefinition, SubclassDefinition

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OPEN5E_API_BASE = "https://api.open5e.com/v1"
CLASS_ENDPOINT = f"{OPEN5E_API_BASE}/classes/"

KNOWN_SKILLS = {
    "acrobatics", "animal handling", "arcana", "athletics", "deception",
    "history", "insight", "intimidation", "investigation", "medicine",
    "nature", "perception", "performance", "persuasion", "religion",
    "sleight of hand", "stealth", "survival",
}

# Standard proficiency bonus by level
PB_BY_LEVEL = {
    1: 2, 2: 2, 3: 2, 4: 2,
    5: 3, 6: 3, 7: 3, 8: 3,
    9: 4, 10: 4, 11: 4, 12: 4,
    13: 5, 14: 5, 15: 5, 16: 5,
    17: 6, 18: 6, 19: 6, 20: 6,
}

# Spell prepare style by class (known from 5e rules, not in API)
SPELL_STYLE = {
    "bard": "known",
    "cleric": "prepared",
    "druid": "prepared",
    "paladin": "prepared",
    "ranger": "known",
    "sorcerer": "known",
    "warlock": "known",
    "wizard": "prepared",
    "eldritch_knight": "known",
    "arcane_trickster": "known",
}

# Subclass level by class (not always in the API)
SUBCLASS_LEVELS = {
    "barbarian": 3, "bard": 3, "cleric": 1, "druid": 2,
    "fighter": 3, "monk": 3, "paladin": 3, "ranger": 3,
    "rogue": 3, "sorcerer": 1, "warlock": 1, "wizard": 2,
}

# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_open5e_classes() -> List[Dict]:
    """Fetch all class data from the Open5e API."""
    response = requests.get(CLASS_ENDPOINT)
    response.raise_for_status()
    data = response.json()
    return data.get("results", [])


def load_local_classes(file_path: Path) -> List[Dict]:
    """Load class data from a local JSON file."""
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


def parse_hit_die(hit_dice_str: str) -> int:
    """Extract die size from '1d10' → 10."""
    m = re.search(r'd(\d+)', hit_dice_str or "")
    return int(m.group(1)) if m else 8


def parse_proficiency_grants(cls: Dict) -> List[Dict]:
    """Build proficiency_grants list from Open5e class fields."""
    grants = []

    # Armor
    armor_str = cls.get("prof_armor", "") or ""
    if armor_str.lower() not in ("none", ""):
        armor_map = {
            "all armor": ["armor:light", "armor:medium", "armor:heavy"],
            "light armor": ["armor:light"],
            "medium armor": ["armor:medium"],
            "heavy armor": ["armor:heavy"],
            "shields": ["armor:shields"],
        }
        for part in [p.strip().lower() for p in armor_str.split(',')]:
            if part in armor_map:
                for a in armor_map[part]:
                    grants.append({"type": a, "choose": 0})
            elif part:
                grants.append({"type": f"armor:{_slugify(part)}", "choose": 0})

    # Weapons
    weapon_str = cls.get("prof_weapons", "") or ""
    if weapon_str.lower() not in ("none", ""):
        weapon_map = {
            "simple weapons": "weapons:simple",
            "martial weapons": "weapons:martial",
        }
        for part in [p.strip().lower() for p in weapon_str.split(',')]:
            mapped = weapon_map.get(part)
            if mapped:
                grants.append({"type": mapped, "choose": 0})
            elif part:
                grants.append({"type": f"weapons:{_slugify(part)}", "choose": 0})

    # Saving throws
    saves_str = cls.get("prof_saving_throws", "") or ""
    if saves_str:
        for part in [p.strip().lower() for p in saves_str.split(',')]:
            if part:
                grants.append({"type": f"saving_throw:{part}", "choose": 0})

    # Skills
    skills_str = cls.get("prof_skills", "") or ""
    if skills_str:
        # Pattern: "Choose two from X, Y, Z" or "Choose two skills from..."
        m = re.match(
            r'Choose\s+(\w+)\s+(?:skills?\s+)?from\s+(.*)',
            skills_str, re.IGNORECASE
        )
        if m:
            word = m.group(1).lower()
            choose = {"one": 1, "two": 2, "three": 3, "four": 4}.get(word, 2)
            skills_text = m.group(2)
            # Extract skill names
            skills_text = re.sub(r'\band\b', ',', skills_text)
            skills = []
            for s in skills_text.split(','):
                s = s.strip().rstrip('.').lower()
                if s in KNOWN_SKILLS:
                    skills.append(s.replace(' ', '_'))
            if skills:
                grants.append({
                    "type": f"skill:{'|'.join(skills)}",
                    "choose": choose,
                })

    # Tools
    tools_str = cls.get("prof_tools", "") or ""
    if tools_str.lower() not in ("none", ""):
        for part in [p.strip() for p in tools_str.split(',')]:
            if part:
                grants.append({"type": f"tool:{_slugify(part)}", "choose": 0})

    return grants


def parse_starting_equipment(equip_str: str) -> List[str]:
    """Parse equipment markdown into item ID list (best-effort from bullet points)."""
    items = []
    if not equip_str:
        return items
    for line in equip_str.split('\n'):
        line = line.strip()
        if not line.startswith('*') and not line.startswith('-'):
            continue
        # Remove bullet
        item = re.sub(r'^[\*\-\s]+', '', line)
        # Take first option from (a)/(b) choices
        # Pattern: "(*a*) chain mail or (*b*) leather armor, longbow, and 20 arrows"
        if '(*a*)' in item or '(*b*)' in item:
            # Extract option (a)
            a_match = re.search(r'\(\*a\*\)\s*(.+?)(?:\s+or\s+\(\*b\*\)|$)', item)
            if a_match:
                item = a_match.group(1).strip()
            else:
                item = re.sub(r'\(\*[ab]\*\)\s*', '', item)
                if ' or ' in item:
                    item = item.split(' or ')[0]
        # Split on "and" for compound items like "leather armor, longbow, and 20 arrows"
        sub_items = re.split(r',\s*(?:and\s+)?|\s+and\s+', item)
        for si in sub_items:
            si = si.strip().rstrip('.')
            if not si:
                continue
            # Remove leading articles and quantities
            si = re.sub(r'^(?:a\s+|an\s+|\d+\s+)', '', si, flags=re.IGNORECASE)
            if si and len(si) > 1:
                items.append(_slugify(si))
    return items

# ---------------------------------------------------------------------------
# Table parsing — level progression
# ---------------------------------------------------------------------------

def parse_class_table(table_str: str) -> List[Dict]:
    """
    Parse the markdown class table into raw row data.
    Returns list of dicts with column headers as keys.
    """
    if not table_str:
        return []

    lines = [l.strip() for l in table_str.split('\n') if l.strip()]
    if len(lines) < 3:
        return []

    # First non-separator line with '|' is the header
    header_line = None
    data_start = 0
    for i, line in enumerate(lines):
        if '|' in line and not re.match(r'^[\s|:-]+$', line):
            header_line = line
            data_start = i + 1
            break
    if not header_line:
        return []

    headers = [h.strip() for h in header_line.split('|') if h.strip()]

    # Skip separator
    while data_start < len(lines) and re.match(r'^[\s|:-]+$', lines[data_start]):
        data_start += 1

    rows = []
    for line in lines[data_start:]:
        if '|' not in line:
            continue
        cells = [c.strip() for c in line.split('|') if c.strip() != '']
        # Pad if needed
        while len(cells) < len(headers):
            cells.append('')
        row = {headers[i]: cells[i] if i < len(cells) else '' for i in range(len(headers))}
        rows.append(row)

    return rows


def build_level_progression(cls: Dict) -> List[Dict]:
    """
    Build the 20-level progression from the class table.
    Parses features, proficiency bonus, spell slots, cantrips, spells known.
    """
    table_str = cls.get("table", "")
    class_slug = _slugify(cls.get("name", ""))
    desc_text = cls.get("desc", "") or ""

    rows = parse_class_table(table_str)

    # Build a map of feature descriptions from the desc markdown
    feature_descs = parse_feature_descriptions(desc_text, class_slug)

    progression = []
    for row in rows:
        # Level
        level_str = row.get("Level", "")
        level_match = re.match(r'(\d+)', level_str)
        if not level_match:
            continue
        level = int(level_match.group(1))

        # Proficiency bonus
        pb_str = row.get("Proficiency Bonus", "")
        pb_match = re.search(r'\+?(\d+)', pb_str)
        pb = int(pb_match.group(1)) if pb_match else PB_BY_LEVEL.get(level, 2)

        # Features column
        feat_col = None
        for col_name in ("Features", "Feature"):
            if col_name in row:
                feat_col = row[col_name]
                break

        features = []
        if feat_col and feat_col.strip() not in ('-', '—', ''):
            for feat_name in [f.strip() for f in feat_col.split(',')]:
                if not feat_name or feat_name in ('-', '—'):
                    continue
                feat_id = f"{class_slug}_{_slugify(feat_name)}"
                # Look up description
                desc = feature_descs.get(feat_name, "")
                if not desc:
                    # Try fuzzy match
                    clean_name = re.sub(r'\s*\(.*?\)', '', feat_name).strip()
                    desc = feature_descs.get(clean_name, "")
                features.append({
                    "feature_id": feat_id,
                    "name": feat_name,
                    "source": f"{class_slug}_{level}",
                    "description": desc,
                })

        # Spell slots
        spell_slots = {}
        for slot_level in range(1, 10):
            col = f"{slot_level}st" if slot_level == 1 else \
                  f"{slot_level}nd" if slot_level == 2 else \
                  f"{slot_level}rd" if slot_level == 3 else \
                  f"{slot_level}th"
            val = row.get(col, "").strip()
            if val and val not in ('-', '—', ''):
                try:
                    spell_slots[str(slot_level)] = int(val)
                except ValueError:
                    pass

        # Cantrips Known
        cantrips = None
        for col_name in ("Cantrips Known", "Cantrips"):
            val = row.get(col_name, "").strip()
            if val and val not in ('-', '—', ''):
                try:
                    cantrips = int(val)
                except ValueError:
                    pass
                break

        # Spells Known
        spells_known = None
        for col_name in ("Spells Known", "Spells"):
            val = row.get(col_name, "").strip()
            if val and val not in ('-', '—', ''):
                try:
                    spells_known = int(val)
                except ValueError:
                    pass
                break

        progression.append({
            "level": level,
            "proficiency_bonus": pb,
            "features": features,
            "resource_grants": [],
            "resource_updates": [],
            "spell_slots": spell_slots if spell_slots else None,
            "cantrips_knowable": cantrips,
            "spells_knowable": spells_known,
        })

    # Fill missing levels up to 20
    existing_levels = {p["level"] for p in progression}
    for lvl in range(1, 21):
        if lvl not in existing_levels:
            progression.append({
                "level": lvl,
                "proficiency_bonus": PB_BY_LEVEL.get(lvl, 2),
                "features": [],
                "resource_grants": [],
                "resource_updates": [],
                "spell_slots": None,
                "cantrips_knowable": None,
                "spells_knowable": None,
            })

    progression.sort(key=lambda p: p["level"])
    return progression


def parse_feature_descriptions(desc: str, class_slug: str) -> Dict[str, str]:
    """
    Parse the class desc markdown into a map of feature_name → description.
    Features are marked with ### or ##### headers.
    """
    if not desc:
        return {}

    features = {}
    # Split on markdown headers
    parts = re.split(r'^(#{2,5})\s+(.+?)$', desc, flags=re.MULTILINE)

    # parts comes as: [preamble, '#level', 'name', 'body', '#level', 'name', 'body', ...]
    i = 1
    while i < len(parts) - 2:
        _hashes = parts[i]
        name = parts[i + 1].strip()
        body = parts[i + 2].strip() if i + 2 < len(parts) else ""
        # Clean markdown
        body = re.sub(r'\*+_?|_?\*+', '', body).strip()
        features[name] = body
        i += 3

    return features

# ---------------------------------------------------------------------------
# Subclass converter
# ---------------------------------------------------------------------------

# Regex to extract level from subclass feature text
_LEVEL_RE = re.compile(
    r'(?:at|starting at|beginning (?:at|when)|also starting at)\s+'
    r'(\d+)(?:st|nd|rd|th)\s+level',
    re.IGNORECASE
)


def parse_subclass_features(desc: str, subclass_slug: str) -> List[Tuple[int, str, str]]:
    """
    Parse a subclass description into (level, name, description) tuples.
    Features are under ##### headers with level references in the text.
    """
    if not desc:
        return []

    features_raw = []
    # Split on ##### headers
    parts = re.split(r'^#{3,5}\s+(.+?)$', desc, flags=re.MULTILINE)

    # parts: [preamble, name1, body1, name2, body2, ...]
    i = 1
    while i < len(parts) - 1:
        name = parts[i].strip()
        body = parts[i + 1].strip() if i + 1 < len(parts) else ""
        # Clean markdown
        body = re.sub(r'\*+_?|_?\*+', '', body).strip()
        features_raw.append((name, body))
        i += 2

    # Now determine level for each feature
    results = []
    for name, body in features_raw:
        # Try to find level in the body text
        level_match = _LEVEL_RE.search(body)
        if level_match:
            level = int(level_match.group(1))
        else:
            # Check for table headers like "Cleric Level | Spells"
            # This is a domain spell table, assign to level 1
            if re.search(r'level\s*\|\s*spells', body, re.IGNORECASE):
                level = 1
            else:
                level = 0  # Unknown; will need manual placement
        results.append((level, name, body))

    return results


def convert_subclass(archetype: Dict, parent_class_slug: str,
                     subclass_level: int) -> Dict:
    """Convert an Open5e archetype into engine SubclassDefinition."""
    name = archetype.get("name", "")
    subclass_id = _slugify(name)
    desc_full = archetype.get("desc", "") or ""

    # Split into intro description and features
    # The intro is everything before the first ##### header
    intro_match = re.match(r'(.*?)(?=#{3,5}\s+|\Z)', desc_full, re.DOTALL)
    intro = intro_match.group(1).strip() if intro_match else ""
    intro = re.sub(r'\*+_?|_?\*+', '', intro).strip()
    # Remove table content from intro
    intro = re.sub(r'\|.*?\|(?:\n\|.*?\|)*', '', intro, flags=re.DOTALL).strip()
    # Clean up blank lines
    intro = re.sub(r'\n{3,}', '\n\n', intro).strip()

    # Parse features with levels
    features_raw = parse_subclass_features(desc_full, subclass_id)

    # Group by level
    level_features: Dict[int, List[Dict]] = {}
    for level, feat_name, feat_desc in features_raw:
        if level == 0:
            level = subclass_level  # Default to subclass grant level
        if level not in level_features:
            level_features[level] = []
        level_features[level].append({
            "feature_id": f"{subclass_id}_{_slugify(feat_name)}",
            "name": feat_name,
            "source": f"{subclass_id}_{level}",
            "description": feat_desc,
        })

    # Build level_progression (only levels with features)
    progression = []
    for level in sorted(level_features.keys()):
        progression.append({
            "level": level,
            "proficiency_bonus": PB_BY_LEVEL.get(level, 2),
            "features": level_features[level],
            "resource_grants": [],
            "resource_updates": [],
            "spell_slots": None,
            "cantrips_knowable": None,
            "spells_knowable": None,
        })

    return {
        "subclass_id": subclass_id,
        "name": name,
        "parent_class_id": parent_class_slug,
        "granted_at_level": subclass_level,
        "description": intro,
        "spellcasting_ability": "",
        "spell_prepare_style": "",
        "level_progression": progression,
    }

# ---------------------------------------------------------------------------
# Main class converter
# ---------------------------------------------------------------------------

def convert_class(cls: Dict) -> Dict:
    """Transform a single Open5e class into engine ClassDefinition format."""
    name = cls.get("name", "")
    class_slug = _slugify(name)

    hit_die = parse_hit_die(cls.get("hit_dice", ""))
    prof_grants = parse_proficiency_grants(cls)
    starting_equipment = parse_starting_equipment(cls.get("equipment", ""))

    spellcasting_ability = (cls.get("spellcasting_ability", "") or "").lower()
    spell_style = SPELL_STYLE.get(class_slug, "")
    if spellcasting_ability and not spell_style:
        spell_style = "prepared"  # Default for unknown casters

    level_progression = build_level_progression(cls)
    subclass_level = SUBCLASS_LEVELS.get(class_slug, 3)

    # Clean description — use first paragraph from desc, before feature details
    desc = cls.get("desc", "") or ""
    # The desc starts with feature details (### headers), not a class overview
    # Use hp_at_1st_level context or a generic description
    # Actually check if there's text before the first ### header
    pre_header = re.match(r'(.*?)(?=###|\Z)', desc, re.DOTALL)
    class_desc = pre_header.group(1).strip() if pre_header else ""
    class_desc = re.sub(r'\*+_?|_?\*+', '', class_desc).strip()
    if not class_desc:
        class_desc = f"The {name} class."

    return {
        "class_id": class_slug,
        "name": name,
        "description": class_desc,
        "hit_die": hit_die,
        "proficiency_grants": prof_grants,
        "starting_equipment": starting_equipment,
        "starting_equipment_currency_gp": 0,
        "spellcasting_ability": spellcasting_ability,
        "spell_prepare_style": spell_style,
        "level_progression": level_progression,
        "subclass_level": subclass_level,
    }

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Convert Open5e class data to TTRPG engine format."
    )
    parser.add_argument("--output-dir", default="classes",
                        help="Directory to save class JSON files")
    parser.add_argument("--local-file",
                        help="Local JSON file containing Open5e class data (instead of API)")
    parser.add_argument("--include-source", nargs="+", metavar="SOURCE",
                        help="Only include content from these document slugs (e.g., wotc-srd).")
    parser.add_argument("--save-subclasses", action="store_true",
                        help="Also save subclass definitions")
    args = parser.parse_args()

    if args.local_file:
        classes = load_local_classes(Path(args.local_file))
    else:
        print("Fetching classes from Open5e API...")
        classes = fetch_open5e_classes()
        print(f"Fetched {len(classes)} classes.")

    if args.include_source:
        classes = [c for c in classes if c.get("document__slug", "") in args.include_source]
        print(f"Filtered to {len(classes)} classes matching sources: {args.include_source}")

    output_path = Path(args.output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    errors = 0

    for cls in classes:
        try:
            converted = convert_class(cls)
            validated = ClassDefinition.model_validate(converted)
            class_id = converted["class_id"]
            filepath = output_path / f"{class_id}.json"

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(validated.model_dump(mode="json"), f, indent=2, ensure_ascii=False)
            print(f"Saved class: {filepath}")

            # Subclasses
            if args.save_subclasses:
                subclass_dir = output_path / f"{class_id}_subclasses"
                subclass_level = converted["subclass_level"]

                for arch in (cls.get("archetypes", []) or []):
                    # Filter by source
                    if args.include_source:
                        if arch.get("document__slug", "") not in args.include_source:
                            continue
                    try:
                        sub = convert_subclass(arch, class_id, subclass_level)
                        validated_sub = SubclassDefinition.model_validate(sub)
                        subclass_dir.mkdir(parents=True, exist_ok=True)
                        sub_path = subclass_dir / f"{validated_sub.subclass_id}.json"
                        with open(sub_path, "w", encoding="utf-8") as f:
                            json.dump(validated_sub.model_dump(mode="json"), f, indent=2, ensure_ascii=False)
                        print(f"Saved subclass: {sub_path}")
                    except Exception as e:
                        errors += 1
                        print(f"Error converting subclass {arch.get('name', 'unknown')}: {e}")

        except Exception as e:
            errors += 1
            print(f"Error converting class {cls.get('name', 'unknown')}: {e}")

    total = sum(1 for _ in output_path.rglob("*.json"))
    print(f"Conversion complete. {total} files written, {errors} errors.")


if __name__ == "__main__":
    main()
