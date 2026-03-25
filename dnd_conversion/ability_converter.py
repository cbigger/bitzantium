"""
ability_converter.py

Fetches spell/ability data from the Open5e API and transforms it into the
AbilityDefinition format used by the TTRPG engine.

Usage:
    python ability_converter.py [--output-dir OUTPUT_DIR]
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

from bitzantium_schemas.data import AbilityDefinition

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OPEN5E_API_BASE = "https://api.open5e.com/v1"
SPELL_ENDPOINT = f"{OPEN5E_API_BASE}/spells/"

# All 5e conditions (lowercase)
CONDITIONS = {
    "blinded", "charmed", "deafened", "exhaustion", "frightened",
    "grappled", "incapacitated", "invisible", "paralyzed", "petrified",
    "poisoned", "prone", "restrained", "stunned", "unconscious",
}

# All 5e damage types (lowercase)
DAMAGE_TYPES = {
    "acid", "bludgeoning", "cold", "fire", "force", "lightning",
    "necrotic", "piercing", "poison", "psychic", "radiant",
    "slashing", "thunder",
}

# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_open5e_spells() -> List[Dict]:
    """Fetch all spell data from the Open5e API (paginated)."""
    results = []
    url = f"{SPELL_ENDPOINT}?limit=500"
    while url:
        print(f"Fetching {url}")
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        results.extend(data.get("results", []))
        url = data.get("next")
    return results


def load_local_spells(file_path: Path) -> List[Dict]:
    """Load spell data from a local JSON file."""
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "results" in data:
        return data["results"]
    if isinstance(data, list):
        return data
    return [data]

# ---------------------------------------------------------------------------
# Text parsing helpers
# ---------------------------------------------------------------------------

def _slugify(text: str) -> str:
    return re.sub(r'[^a-z0-9]+', '_', text.lower()).strip('_')


def parse_action_cost(casting_time: str) -> str:
    """Convert '1 action', '1 bonus action', '1 reaction, ...' to engine action_cost."""
    ct = casting_time.lower()
    if "bonus action" in ct:
        return "bonus_action"
    if "reaction" in ct:
        return "reaction"
    if "action" in ct:
        return "action"
    # Longer casting times (1 minute, etc.) are still "action" mechanically
    return "action"


def parse_range(range_str: str) -> Tuple[Optional[int], Optional[int]]:
    """
    Parse range string into (range_ft, range_max_ft).
    Examples: '60 feet', 'Touch', 'Self', '150 feet', 'Self (10-foot radius)'
    """
    if not range_str:
        return None, None
    r = range_str.lower()
    if r in ("self", "touch"):
        return None, None
    # "30/120 feet" style (rare, but possible)
    m = re.match(r'(\d+)/(\d+)\s*(?:feet|ft)', r)
    if m:
        return int(m.group(1)), int(m.group(2))
    # "60 feet"
    m = re.search(r'(\d+)\s*(?:feet|ft)', r)
    if m:
        return int(m.group(1)), None
    # "1 mile" etc. — convert
    m = re.search(r'(\d+)\s*mile', r)
    if m:
        return int(m.group(1)) * 5280, None
    return None, None


def parse_target_type(range_str: str, desc: str) -> str:
    """
    Determine target_type from range and description text.
    Returns one of: self, touch, single, multiple, area_sphere, area_cube,
    area_cone, area_line, area_cylinder.
    """
    r = range_str.lower() if range_str else ""
    d = desc.lower()

    # Area effects — check description for area shape keywords
    area_patterns = [
        (r'(\d+)[- ]foot[- ]radius(?:\s+sphere)?', "area_sphere"),
        (r'(\d+)[- ]foot(?:[- ]radius)?\s+cylinder', "area_cylinder"),
        (r'(\d+)[- ]foot\s+cube', "area_cube"),
        (r'(\d+)[- ]foot\s+cone', "area_cone"),
        (r'(\d+)[- ]foot[- ](?:long\s+)?line', "area_line"),
    ]
    for pattern, target_type in area_patterns:
        if re.search(pattern, d):
            return target_type

    # Self range with area (e.g., "Self (15-foot cone)")
    if "self" in r:
        for pattern, target_type in area_patterns:
            if re.search(pattern, r):
                return target_type
        return "self"

    if r == "touch":
        return "touch"

    # Multiple targets
    multi_patterns = [
        r'choose\s+(?:up\s+to\s+)?\w+\s+creatures?',
        r'each\s+creature',
        r'all\s+creatures?',
        r'up\s+to\s+\w+\s+(?:willing\s+)?creatures?',
    ]
    for pat in multi_patterns:
        if re.search(pat, d):
            return "multiple"

    # Single target (default for ranged spells)
    if re.search(r'a\s+(?:creature|target|humanoid|beast)', d):
        return "single"

    return "single"


def parse_area_ft(range_str: str, desc: str) -> Optional[int]:
    """Extract area size in feet from description or range string."""
    text = f"{range_str or ''} {desc}"
    patterns = [
        r'(\d+)[- ]foot[- ]radius',
        r'(\d+)[- ]foot\s+cube',
        r'(\d+)[- ]foot\s+cone',
        r'(\d+)[- ]foot[- ](?:long\s+)?line',
        r'(\d+)[- ]foot(?:[- ]radius)?\s+cylinder',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return int(m.group(1))
    return None


def parse_spell_components(spell: Dict) -> Dict:
    """Build spell_components dict from Open5e spell data."""
    material = spell.get("material", "") or ""
    has_material = spell.get("requires_material_components", False)

    # Try to extract GP cost from material description
    cost_gp = None
    consumed = False
    if material:
        cost_match = re.search(r'(\d+)\s*gp', material, re.IGNORECASE)
        if cost_match:
            cost_gp = int(cost_match.group(1))
        if re.search(r'consume[sd]?|which the spell consumes', material, re.IGNORECASE):
            consumed = True

    return {
        "verbal": spell.get("requires_verbal_components", False),
        "somatic": spell.get("requires_somatic_components", False),
        "material": has_material,
        "material_description": material if material else None,
        "material_cost_gp": cost_gp,
        "consumed_on_cast": consumed,
    }


def parse_reaction_trigger(casting_time: str) -> Optional[str]:
    """Extract reaction trigger from casting_time string."""
    if "reaction" not in casting_time.lower():
        return None
    # Pattern: "1 reaction, which you take when ..."
    m = re.search(r'reaction,?\s*(?:which you take\s+)?(.+)', casting_time, re.IGNORECASE)
    if m:
        trigger = m.group(1).strip()
        if trigger:
            return trigger[0].upper() + trigger[1:]
    return "Triggered as a reaction."

# ---------------------------------------------------------------------------
# Effect parsing — the core NLP section
# ---------------------------------------------------------------------------

# Damage dice in desc: "3d10 necrotic damage", "takes 8d6 fire damage"
_DAMAGE_IN_DESC_RE = re.compile(
    r'(?:takes?\s+|deals?\s+|suffer(?:s|ing)?\s+)?'
    r'(?P<count>\d+)d(?P<die>\d+)'
    r'(?:\s*\+\s*(?P<bonus>\d+))?'
    r'\s+(?P<type>' + '|'.join(DAMAGE_TYPES) + r')\s+damage',
    re.IGNORECASE
)

# Healing in desc: "regains Xd8 hit points", "regains hit points equal to Xd8 + ..."
_HEAL_IN_DESC_RE = re.compile(
    r'regains?\s+(?:a\s+number\s+of\s+)?(?:hit\s+points\s+equal\s+to\s+)?'
    r'(?P<count>\d+)d(?P<die>\d+)'
    r'(?:\s*\+\s*(?P<bonus>.+?))?\s*(?:hit\s+points)?',
    re.IGNORECASE
)

# Save in desc: "must succeed on a Dexterity saving throw", "must make a DC X Ability saving throw"
_SAVE_RE = re.compile(
    r'(?:succeed\s+on|make)\s+a\s+(?:DC\s+\d+\s+)?'
    r'(?P<ability>strength|dexterity|constitution|intelligence|wisdom|charisma)\s+'
    r'saving\s+throw',
    re.IGNORECASE
)

# Half damage on save: "half as much damage on a successful one", "or half as much"
_HALF_ON_SAVE_RE = re.compile(
    r'half\s+(?:as\s+much|damage|that\s+damage)',
    re.IGNORECASE
)

# Condition application: "is/becomes/be X" where X is a condition
_CONDITION_RE = re.compile(
    r'(?:is|becomes?|be)\s+(?P<cond>' + '|'.join(CONDITIONS) + r')',
    re.IGNORECASE
)

# Duration of condition in turns/rounds: "for X rounds" or "for the duration"
_COND_DURATION_RE = re.compile(
    r'for\s+(?P<dur>\d+)\s+(?:round|turn|minute)',
    re.IGNORECASE
)

# Stat modification: "+X bonus to AC", "base AC becomes X + ..."
_STAT_MOD_RE = re.compile(
    r'\+(?P<mod>\d+)\s+bonus\s+to\s+(?P<stat>AC|attack\s+rolls?|saving\s+throws?)',
    re.IGNORECASE
)

# Higher level scaling: "the damage increases by XdY for each slot level above Nth"
_UPCAST_DAMAGE_RE = re.compile(
    r'(?:damage|healing)\s+increases?\s+by\s+'
    r'(?P<count>\d+)d(?P<die>\d+)\s+'
    r'for\s+(?:each|every(?:\s+two)?)\s+slot\s+levels?\s+above\s+'
    r'(?P<base_level>\d+)(?:st|nd|rd|th)',
    re.IGNORECASE
)

# "every two slot levels" variant
_UPCAST_EVERY_TWO_RE = re.compile(
    r'increases?\s+by\s+(?P<count>\d+)d(?P<die>\d+)\s+'
    r'for\s+every\s+two\s+slot\s+levels?\s+above\s+'
    r'(?:the\s+)?(?P<base_level>\d+)(?:st|nd|rd|th)',
    re.IGNORECASE
)

# Cantrip scaling: "damage increases by XdY when you reach 5th level (XdY), 11th level ..."
_CANTRIP_SCALE_RE = re.compile(
    r'(?:damage|this spell.s damage)\s+increases?\s+by\s+'
    r'(?P<count>\d+)d(?P<die>\d+)\s+'
    r'when\s+you\s+reach\s+5th\s+level',
    re.IGNORECASE
)


def _parse_base_effects(desc: str) -> List[Dict]:
    """
    Extract structured effects from a spell description.
    Returns a list of effect dicts matching the engine schema.
    """
    effects = []

    # Check for save info (shared across effect types)
    save_match = _SAVE_RE.search(desc)
    save_ability = save_match.group("ability").lower() if save_match else None
    half_on_save = bool(_HALF_ON_SAVE_RE.search(desc))

    # Damage effects
    for dmg in _DAMAGE_IN_DESC_RE.finditer(desc):
        eff = {
            "effect_type": "damage",
            "dice": f"{dmg.group('count')}d{dmg.group('die')}",
            "damage_type": dmg.group("type").lower(),
            "save_ability": save_ability,
            "save_dc": None,
            "half_on_save": half_on_save if save_ability else False,
        }
        effects.append(eff)

    # Healing effects
    for heal in _HEAL_IN_DESC_RE.finditer(desc):
        bonus_str = heal.group("bonus")
        bonus = 0
        if bonus_str and bonus_str.strip().isdigit():
            bonus = int(bonus_str.strip())
        eff = {
            "effect_type": "heal",
            "dice": f"{heal.group('count')}d{heal.group('die')}",
            "bonus": bonus,
        }
        effects.append(eff)

    # Condition effects
    for cond_match in _CONDITION_RE.finditer(desc):
        cond = cond_match.group("cond").lower()
        dur_match = _COND_DURATION_RE.search(desc)
        duration_turns = int(dur_match.group("dur")) if dur_match else None
        eff = {
            "effect_type": "apply_condition",
            "condition": cond,
            "duration_turns": duration_turns,
            "save_ability": save_ability,
            "save_dc": None,
        }
        # Don't duplicate if we already have this condition
        if not any(e.get("condition") == cond for e in effects):
            effects.append(eff)

    # Stat modification effects (e.g., Shield's +5 AC)
    for stat_match in _STAT_MOD_RE.finditer(desc):
        stat = stat_match.group("stat").lower()
        if "ac" in stat:
            stat = "ac"
        elif "attack" in stat:
            stat = "attack_rolls"
        elif "saving" in stat:
            stat = "saving_throws"
        eff = {
            "effect_type": "modify_stat",
            "stat": stat,
            "modifier": int(stat_match.group("mod")),
            "set_value": None,
        }
        effects.append(eff)

    # If no structured effects were found but there's a description, we still
    # return empty — the description field carries the detail
    return effects


def build_effects_by_level(spell: Dict, base_effects: List[Dict]) -> List[Dict]:
    """
    Build effects_by_level list, including upcast scaling where applicable.
    For cantrips, builds character-level tiers.
    """
    spell_level = spell.get("spell_level", 0)
    higher_level = spell.get("higher_level", "") or ""
    desc = spell.get("desc", "") or ""

    if not base_effects:
        # Even without parsed effects, create one entry at spell level
        return [{"slot_level": spell_level, "effects": []}]

    entries = [{"slot_level": spell_level, "effects": base_effects}]

    # --- Cantrip scaling (character level tiers) ---
    if spell_level == 0:
        cantrip_match = _CANTRIP_SCALE_RE.search(higher_level or desc)
        if cantrip_match:
            inc_count = int(cantrip_match.group("count"))
            inc_die = int(cantrip_match.group("die"))
            # Find the base damage effect to scale
            base_dmg = next((e for e in base_effects if e.get("effect_type") == "damage"), None)
            if base_dmg:
                base_dice = base_dmg["dice"]
                base_match = re.match(r'(\d+)d(\d+)', base_dice)
                if base_match:
                    base_count = int(base_match.group(1))
                    die_val = int(base_match.group(2))
                    # Standard cantrip tiers: 5th, 11th, 17th
                    for tier, multiplier in [(5, 1), (11, 2), (17, 3)]:
                        new_count = base_count + inc_count * multiplier
                        scaled_effects = []
                        for e in base_effects:
                            if e.get("effect_type") == "damage" and e.get("dice") == base_dice:
                                e_copy = dict(e)
                                e_copy["dice"] = f"{new_count}d{die_val}"
                                scaled_effects.append(e_copy)
                            else:
                                scaled_effects.append(e)
                        entries.append({"slot_level": tier, "effects": scaled_effects})
        return entries

    # --- Spell upcast scaling ---
    # "increases by XdY for each slot level above Nth"
    upcast_match = _UPCAST_DAMAGE_RE.search(higher_level)
    every_two = False
    if not upcast_match:
        upcast_match = _UPCAST_EVERY_TWO_RE.search(higher_level)
        every_two = True if upcast_match else False

    if upcast_match:
        inc_count = int(upcast_match.group("count"))
        inc_die = int(upcast_match.group("die"))
        base_level = int(upcast_match.group("base_level"))

        # Find which effect(s) to scale (damage or healing)
        scalable_types = ("damage", "heal")
        base_scalable = [e for e in base_effects if e.get("effect_type") in scalable_types]

        for slot_level in range(spell_level + 1, 10):
            levels_above = slot_level - base_level
            if levels_above <= 0:
                continue
            if every_two:
                scale_factor = levels_above // 2
            else:
                scale_factor = levels_above
            if scale_factor <= 0:
                continue

            scaled_effects = []
            for e in base_effects:
                if e.get("effect_type") in scalable_types and e.get("dice"):
                    e_copy = dict(e)
                    dice_match = re.match(r'(\d+)d(\d+)', e["dice"])
                    if dice_match:
                        orig_count = int(dice_match.group(1))
                        die_val = int(dice_match.group(2))
                        new_count = orig_count + inc_count * scale_factor
                        e_copy["dice"] = f"{new_count}d{die_val}"
                    scaled_effects.append(e_copy)
                else:
                    scaled_effects.append(e)
            entries.append({"slot_level": slot_level, "effects": scaled_effects})

    return entries

# ---------------------------------------------------------------------------
# Main converter
# ---------------------------------------------------------------------------

def convert_spell(spell: Dict) -> Dict:
    """Transform a single Open5e spell into engine AbilityDefinition format."""
    name = spell.get("name", "")
    desc = spell.get("desc", "") or ""
    spell_level = spell.get("spell_level", 0)
    range_str = spell.get("range", "") or ""
    casting_time = spell.get("casting_time", "") or ""

    range_ft, range_max_ft = parse_range(range_str)
    target_type = parse_target_type(range_str, desc)
    area_ft = parse_area_ft(range_str, desc)

    base_effects = _parse_base_effects(desc)
    effects_by_level = build_effects_by_level(spell, base_effects)

    school = (spell.get("school", "") or "").lower()

    # Spell lists
    spell_lists = spell.get("spell_lists", []) or []

    # Duration
    duration = spell.get("duration", "Instantaneous") or "Instantaneous"
    # Normalize: lowercase
    duration_lower = duration.lower()

    return {
        "ability_id": _slugify(name),
        "name": name,
        "description": desc,
        "ability_type": "spell",
        "action_cost": parse_action_cost(casting_time),
        "target_type": target_type,
        "range_ft": range_ft,
        "range_max_ft": range_max_ft,
        "area_ft": area_ft,
        "duration": duration_lower,
        "concentration": spell.get("requires_concentration", False),
        "effects_by_level": effects_by_level,
        "spell_school": school,
        "spell_components": parse_spell_components(spell),
        "ritual": spell.get("can_be_cast_as_ritual", False),
        "reaction_trigger": parse_reaction_trigger(casting_time),
        "spell_lists": spell_lists,
    }


def main():
    parser = argparse.ArgumentParser(description="Convert Open5e spell data to TTRPG engine ability format.")
    parser.add_argument("--output-dir", default="abilities", help="Directory to save ability JSON files")
    parser.add_argument("--local-file", help="Local JSON file containing Open5e spell data (instead of API)")
    parser.add_argument("--include-source", nargs="+", metavar="SOURCE",
                        help="Only include spells from these document slugs (e.g., wotc-srd).")
    args = parser.parse_args()

    if args.local_file:
        spells = load_local_spells(Path(args.local_file))
    else:
        print("Fetching spells from Open5e API...")
        spells = fetch_open5e_spells()
        print(f"Fetched {len(spells)} spells.")

    if args.include_source:
        spells = [s for s in spells if s.get("document__slug", "") in args.include_source]
        print(f"Filtered to {len(spells)} spells matching sources: {args.include_source}")

    output_path = Path(args.output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    errors = 0

    for spell in spells:
        try:
            converted = convert_spell(spell)
            validated = AbilityDefinition.model_validate(converted)
            filename = f"{converted['ability_id']}.json"
            filepath = output_path / filename

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(validated.model_dump(mode="json"), f, indent=2, ensure_ascii=False)
            print(f"Saved: {filepath}")

        except Exception as e:
            errors += 1
            print(f"Error converting spell {spell.get('name', 'unknown')}: {e}")

    print(f"Conversion complete. {len(spells) - errors} succeeded, {errors} errors.")


if __name__ == "__main__":
    main()
