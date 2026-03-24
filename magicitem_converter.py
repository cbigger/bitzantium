"""
magicitem_converter.py

Fetches magic item data from the Open5e API and transforms it into the
ItemDefinition format used by the TTRPG engine.

Usage:
    python magicitem_converter.py [--output-dir OUTPUT_DIR]
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

from data import ItemBase

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OPEN5E_API_BASE = "https://api.open5e.com/v1"
MAGICITEM_ENDPOINT = f"{OPEN5E_API_BASE}/magicitems/"

# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_open5e_magicitems() -> List[Dict]:
    """Fetch all magic item data from the Open5e API (paginated)."""
    results = []
    url = f"{MAGICITEM_ENDPOINT}?limit=500"
    while url:
        print(f"Fetching {url}")
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        results.extend(data.get("results", []))
        url = data.get("next")
    return results


def load_local_magicitems(file_path: Path) -> List[Dict]:
    """Load magic item data from a local JSON file."""
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


def parse_item_type(type_str: str) -> str:
    """
    Convert Open5e type string to engine item_type.
    E.g., "Wondrous item" → "wondrous", "Weapon (any sword)" → "weapon",
    "Armor (plate)" → "armor", "Ring" → "ring", "Potion" → "potion"
    """
    t = type_str.lower().strip()
    if t.startswith("weapon"):
        return "weapon"
    if t.startswith("armor"):
        # Check for shield specifically
        if "shield" in t:
            return "shield"
        return "armor"
    if t.startswith("potion"):
        return "potion"
    if t.startswith("scroll"):
        return "scroll"
    if t.startswith("wand"):
        return "wand"
    if t.startswith("rod"):
        return "rod"
    if t.startswith("staff"):
        return "staff"
    if t.startswith("ring"):
        return "ring"
    if t.startswith("wondrous"):
        return "wondrous"
    return "wondrous"


def parse_rarity(rarity_str: str) -> str:
    """Normalize rarity string to engine enum."""
    r = rarity_str.lower().strip()
    if "artifact" in r:
        return "artifact"
    if "legendary" in r:
        return "legendary"
    if "very rare" in r:
        return "very_rare"
    if "rare" in r:
        return "rare"
    if "uncommon" in r:
        return "uncommon"
    if "common" in r:
        return "common"
    # "varies" and complex rarity strings
    if "varies" in r:
        return "uncommon"  # sensible default
    return "uncommon"


def parse_attunement(attune_str: str) -> Tuple[bool, Optional[str]]:
    """
    Parse attunement string.
    Returns (requires_attunement: bool, requirements: str|None).
    """
    if not attune_str:
        return False, None
    if "requires attunement" not in attune_str.lower():
        return False, None

    # Extract requirements after "requires attunement"
    m = re.match(r'requires attunement\s*(?:by\s+)?(.+)?', attune_str, re.IGNORECASE)
    if m and m.group(1):
        req = m.group(1).strip()
        if req:
            return True, req
    return True, None


# ---------------------------------------------------------------------------
# Charge and effect parsing from descriptions
# ---------------------------------------------------------------------------

# "has X charges" or "starts with X charges"
_CHARGES_RE = re.compile(
    r'(?:has|starts?\s+with|contains)\s+(?P<charges>\d+)\s+charges',
    re.IGNORECASE
)

# "regains XdY expended charges" or "regains XdY+Z charges"
_RECHARGE_RE = re.compile(
    r'regains?\s+(?P<amount>\d+d\d+(?:\s*\+\s*\d+)?|\d+)\s+'
    r'(?:expended\s+)?charges?\s+'
    r'(?:daily\s+)?(?:at\s+)?(?P<when>dawn|dusk|midnight|sunrise|sunset)',
    re.IGNORECASE
)

# "regains all expended charges daily at dawn"
_RECHARGE_ALL_RE = re.compile(
    r'regains?\s+all\s+(?:expended\s+)?charges?\s+'
    r'(?:daily\s+)?(?:at\s+)?(?P<when>dawn|dusk|midnight)',
    re.IGNORECASE
)

# Recharge on rest: "regains charges after a long rest"
_RECHARGE_REST_RE = re.compile(
    r'regains?\s+(?:all\s+)?(?:\d+d\d+(?:\+\d+)?\s+)?(?:expended\s+)?charges?\s+'
    r'(?:(?:daily\s+)?at\s+|after\s+a\s+)?(?P<when>long\s+rest|short\s+rest|short\s+or\s+long\s+rest)',
    re.IGNORECASE
)

# Activation: "you can use an action to ...", "as a bonus action"
_ACTIVATION_RE = re.compile(
    r'(?:use\s+(?:an?\s+)?|as\s+a\s+)(?P<type>action|bonus\s+action|reaction)',
    re.IGNORECASE
)

# Bonus to AC: "+X bonus to AC"
_AC_BONUS_RE = re.compile(
    r'\+(?P<bonus>\d+)\s+bonus\s+to\s+AC',
    re.IGNORECASE
)

# Bonus to attack and damage: "+X bonus to attack and damage rolls"
_ATTACK_DAMAGE_BONUS_RE = re.compile(
    r'\+(?P<bonus>\d+)\s+bonus\s+to\s+attack\s+and\s+damage\s+rolls',
    re.IGNORECASE
)

# Bonus to saving throws: "+X bonus to saving throws"
_SAVE_BONUS_RE = re.compile(
    r'\+(?P<bonus>\d+)\s+bonus\s+to\s+saving\s+throws',
    re.IGNORECASE
)

# Bonus to spell attack: "+X bonus to spell attack rolls"
_SPELL_ATTACK_BONUS_RE = re.compile(
    r'\+(?P<bonus>\d+)\s+bonus\s+to\s+spell\s+attack\s+rolls',
    re.IGNORECASE
)

# Extra damage dice: "deals an extra XdY TYPE damage"
_EXTRA_DAMAGE_RE = re.compile(
    r'(?:deals?\s+an?\s+extra|additional)\s+'
    r'(?P<count>\d+)d(?P<die>\d+)\s+'
    r'(?P<type>\w+)\s+damage',
    re.IGNORECASE
)

# Cast spell: "cast X" where X is a known spell pattern
# Open5e uses _spell name_ (markdown italics) or just plain text
_CAST_SPELL_RE = re.compile(
    r'cast\s+(?:the\s+)?_?(?P<spell>[a-zA-Z][a-zA-Z\s\']+?)_?'
    r'(?:\s+spell|\s+\(|\s+at\s+|\s+from\s+|\s+using|\s+without|\.\s*|\,\s*|\s+on\b)',
    re.IGNORECASE
)

# Resistance: "resistance to X damage"
_RESISTANCE_RE = re.compile(
    r'resistance\s+to\s+(?P<type>\w+)\s+damage',
    re.IGNORECASE
)

# Immunity: "immune to X damage" or "immunity to X"
_IMMUNITY_RE = re.compile(
    r'(?:immune|immunity)\s+to\s+(?P<type>\w+)(?:\s+damage)?',
    re.IGNORECASE
)

# Potion healing: "regain XdY hit points" or "regains XdY + Z hit points"
_POTION_HEAL_RE = re.compile(
    r'regains?\s+(?P<count>\d+)d(?P<die>\d+)'
    r'(?:\s*\+\s*(?P<bonus>\d+))?\s+hit\s+points',
    re.IGNORECASE
)

# Destroy on last charge: "crumbles", "destroyed", "is destroyed"
_DESTROY_LAST_RE = re.compile(
    r'(?:crumble|destroy|is\s+destroyed|ceases\s+to\s+function)',
    re.IGNORECASE
)


def parse_magic_properties(desc: str, item_type: str) -> Optional[Dict]:
    """
    Parse magic item description into magic_properties dict.
    Returns None for items where magic_properties doesn't apply.
    """
    desc_lower = desc.lower()

    # Determine activation type
    act_match = _ACTIVATION_RE.search(desc)
    activation_type = "action"
    if act_match:
        act_raw = act_match.group("type").lower()
        if "bonus" in act_raw:
            activation_type = "bonus_action"
        elif "reaction" in act_raw:
            activation_type = "reaction"

    # Charges
    charges_match = _CHARGES_RE.search(desc)
    charges = int(charges_match.group("charges")) if charges_match else 0
    max_charges = charges

    # Recharge
    recharge_on = "none"
    recharge_amount = None
    recharge_all = _RECHARGE_ALL_RE.search(desc)
    recharge_match = _RECHARGE_RE.search(desc)
    recharge_rest = _RECHARGE_REST_RE.search(desc)

    if recharge_all:
        recharge_on = recharge_all.group("when").lower()
        recharge_amount = f"{charges}"
    elif recharge_match:
        recharge_on = recharge_match.group("when").lower()
        recharge_amount = recharge_match.group("amount").replace(' ', '')
    elif recharge_rest:
        rest_type = recharge_rest.group("when").lower()
        if "short" in rest_type and "long" in rest_type:
            recharge_on = "short_rest"
        elif "short" in rest_type:
            recharge_on = "short_rest"
        else:
            recharge_on = "long_rest"

    # Destroy on last charge
    destroy = False
    if charges > 0 and _DESTROY_LAST_RE.search(desc):
        # Only if it mentions the last charge context
        if re.search(r'last\s+charge|expend\s+the\s+last', desc_lower):
            destroy = True

    # Effects on equip
    effects_on_equip = []

    ac_match = _AC_BONUS_RE.search(desc)
    if ac_match:
        effects_on_equip.append({
            "effect_type": "modify_stat",
            "stat": "ac",
            "modifier": int(ac_match.group("bonus")),
        })

    save_match = _SAVE_BONUS_RE.search(desc)
    if save_match:
        effects_on_equip.append({
            "effect_type": "modify_stat",
            "stat": "saving_throws",
            "modifier": int(save_match.group("bonus")),
        })

    resist_match = _RESISTANCE_RE.search(desc)
    if resist_match:
        effects_on_equip.append({
            "effect_type": "grant_resistance",
            "damage_type": resist_match.group("type").lower(),
        })

    immune_match = _IMMUNITY_RE.search(desc)
    if immune_match:
        effects_on_equip.append({
            "effect_type": "grant_immunity",
            "damage_type": immune_match.group("type").lower(),
        })

    # Effects on use
    effects_on_use = []

    # Spell casting from item
    for spell_match in _CAST_SPELL_RE.finditer(desc):
        spell_name = spell_match.group("spell").strip().lower()
        spell_id = _slugify(spell_name)
        if len(spell_id) > 2 and spell_id not in ("the", "its", "this", "your", "that", "a"):
            effects_on_use.append({
                "effect_type": "cast_spell",
                "spell_id": spell_id,
                "cast_at_level": 0,
                "dc_override": None,
            })

    # Potion healing — inline dice or table-based
    heal_match = _POTION_HEAL_RE.search(desc)
    if heal_match:
        bonus = int(heal_match.group("bonus")) if heal_match.group("bonus") else 0
        effects_on_use.append({
            "effect_type": "heal",
            "dice": f"{heal_match.group('count')}d{heal_match.group('die')}",
            "bonus": bonus,
        })
    elif item_type == "potion" and "hit points" in desc_lower:
        # Try to extract from a markdown table row: "| Healing | Common | 2d4 + 2 |"
        table_heal = re.search(r'(\d+)d(\d+)\s*(?:\+\s*(\d+))?', desc)
        if table_heal:
            bonus = int(table_heal.group(3)) if table_heal.group(3) else 0
            effects_on_use.append({
                "effect_type": "heal",
                "dice": f"{table_heal.group(1)}d{table_heal.group(2)}",
                "bonus": bonus,
            })

    # Extra damage (on equip for weapons, e.g., flame tongue)
    extra_dmg = _EXTRA_DAMAGE_RE.search(desc)
    if extra_dmg:
        effects_on_equip.append({
            "effect_type": "damage",
            "dice": f"{extra_dmg.group('count')}d{extra_dmg.group('die')}",
            "damage_type": extra_dmg.group("type").lower(),
        })

    # Concentration (rare but some items require it)
    concentration = bool(re.search(r'concentration', desc_lower))

    # Only return magic_properties if there's actually something magical
    if (charges > 0 or effects_on_equip or effects_on_use or
            item_type in ("wand", "rod", "staff", "ring", "wondrous", "potion", "scroll")):
        return {
            "activation_type": activation_type,
            "charges": charges,
            "charges_max": max_charges,
            "recharge_on": recharge_on,
            "recharge_amount": recharge_amount,
            "destroy_on_last_charge": destroy,
            "effects_on_equip": effects_on_equip,
            "effects_on_use": effects_on_use,
            "concentration_required": concentration,
        }
    return None


def parse_weapon_properties(type_str: str, desc: str) -> Optional[Dict]:
    """
    For weapon-type magic items, extract weapon properties from description.
    """
    if not type_str.lower().startswith("weapon"):
        return None

    # Extract the base weapon from parenthetical: "Weapon (longsword)" → "longsword"
    base_match = re.search(r'weapon\s*\(([^)]+)\)', type_str, re.IGNORECASE)
    base_weapon = base_match.group(1).strip().lower() if base_match else "any"

    # Attack/damage bonus
    bonus_match = _ATTACK_DAMAGE_BONUS_RE.search(desc)
    bonus = int(bonus_match.group("bonus")) if bonus_match else 0

    # Map known base weapons to their damage dice and type
    _WEAPON_STATS = {
        "longsword":   ("1d8", "slashing"),
        "shortsword":  ("1d6", "piercing"),
        "greatsword":  ("2d6", "slashing"),
        "rapier":      ("1d8", "piercing"),
        "scimitar":    ("1d6", "slashing"),
        "dagger":      ("1d4", "piercing"),
        "handaxe":     ("1d6", "slashing"),
        "battleaxe":   ("1d8", "slashing"),
        "greataxe":    ("1d12", "slashing"),
        "warhammer":   ("1d8", "bludgeoning"),
        "maul":        ("2d6", "bludgeoning"),
        "mace":        ("1d6", "bludgeoning"),
        "morningstar": ("1d8", "piercing"),
        "flail":       ("1d8", "bludgeoning"),
        "glaive":      ("1d10", "slashing"),
        "halberd":     ("1d10", "slashing"),
        "lance":       ("1d12", "piercing"),
        "pike":        ("1d10", "piercing"),
        "trident":     ("1d6", "piercing"),
        "war pick":    ("1d8", "piercing"),
        "whip":        ("1d4", "slashing"),
        "club":        ("1d4", "bludgeoning"),
        "greatclub":   ("1d8", "bludgeoning"),
        "javelin":     ("1d6", "piercing"),
        "light hammer":("1d4", "bludgeoning"),
        "quarterstaff":("1d6", "bludgeoning"),
        "sickle":      ("1d4", "slashing"),
        "spear":       ("1d6", "piercing"),
        "longbow":     ("1d8", "piercing"),
        "shortbow":    ("1d6", "piercing"),
        "light crossbow": ("1d8", "piercing"),
        "heavy crossbow":  ("2d4", "piercing"),
        "hand crossbow":   ("1d6", "piercing"),
        "dart":        ("1d4", "piercing"),
        "sling":       ("1d4", "bludgeoning"),
        "blowgun":     ("1", "piercing"),
        "net":         ("0", "bludgeoning"),
    }

    damage_dice = None
    damage_type = None
    if base_weapon in _WEAPON_STATS:
        damage_dice, damage_type = _WEAPON_STATS[base_weapon]
    else:
        # Try partial match for compound names like "any sword"
        for weapon_name, stats in _WEAPON_STATS.items():
            if weapon_name in base_weapon or base_weapon in weapon_name:
                damage_dice, damage_type = stats
                break

    # If we can't determine the weapon stats (e.g., "any" weapon), don't emit
    # weapon_properties since damage_dice and damage_type are required fields.
    if damage_dice is None or damage_type is None:
        return None

    return {
        "category": "martial",
        "weapon_type": "melee",
        "damage_dice": damage_dice,
        "damage_type": damage_type,
        "versatile_damage": None,
        "attack_bonus": bonus,
        "damage_bonus": bonus,
        "range_normal": None,
        "range_max": None,
        "properties": [],
        "silvered": False,
        "adamantine": False,
    }


def parse_armor_properties(type_str: str, desc: str) -> Optional[Dict]:
    """For armor-type magic items, extract armor properties."""
    t = type_str.lower()
    if not t.startswith("armor"):
        return None
    if "shield" in t:
        return None  # Handled as shield item_type

    # Determine armor type from parenthetical
    base_match = re.search(r'armor\s*\(([^)]+)\)', type_str, re.IGNORECASE)
    base = base_match.group(1).strip().lower() if base_match else "medium"

    armor_type = "medium"
    if any(x in base for x in ("plate", "chain mail", "splint", "half plate")):
        armor_type = "heavy"
    elif any(x in base for x in ("leather", "padded", "studded")):
        armor_type = "light"
    elif any(x in base for x in ("heavy",)):
        armor_type = "heavy"
    elif any(x in base for x in ("light",)):
        armor_type = "light"

    ac_match = _AC_BONUS_RE.search(desc)
    ac_bonus = int(ac_match.group("bonus")) if ac_match else 0

    return {
        "armor_type": armor_type,
        "base_ac": 0,
        "ac_bonus": ac_bonus,
        "max_dex_bonus": None,
        "strength_requirement": 0,
        "stealth_disadvantage": False,
        "base_armor": base,
    }


def parse_consumable_properties(item_type: str, desc: str) -> Optional[Dict]:
    """For potions and similar consumables."""
    if item_type != "potion":
        return None

    effects = []
    heal_match = _POTION_HEAL_RE.search(desc)
    if heal_match:
        bonus = int(heal_match.group("bonus")) if heal_match.group("bonus") else 0
        effects.append({
            "effect_type": "heal",
            "dice": f"{heal_match.group('count')}d{heal_match.group('die')}",
            "bonus": bonus,
        })
    elif "hit points" in desc.lower():
        # Fallback: extract dice from table or other patterns
        table_heal = re.search(r'(\d+)d(\d+)\s*(?:\+\s*(\d+))?', desc)
        if table_heal:
            bonus = int(table_heal.group(3)) if table_heal.group(3) else 0
            effects.append({
                "effect_type": "heal",
                "dice": f"{table_heal.group(1)}d{table_heal.group(2)}",
                "bonus": bonus,
            })

    return {
        "consumption_type": "drink",
        "charges": 1,
        "effects": effects,
    }


def generate_tags(desc: str, item_type: str) -> List[str]:
    """Generate relevant tags from the item description and type."""
    tags = []
    desc_lower = desc.lower()

    if item_type in ("weapon",):
        tags.append("weapon")
    if item_type in ("armor", "shield"):
        tags.append("armor")
    if item_type in ("wand", "rod", "staff"):
        tags.append("arcane")
    if "ranged" in desc_lower or "range" in desc_lower:
        tags.append("ranged")
    if "curse" in desc_lower:
        tags.append("cursed")
    if "sentient" in desc_lower or "sentience" in desc_lower:
        tags.append("sentient")

    return tags

# ---------------------------------------------------------------------------
# Main converter
# ---------------------------------------------------------------------------

def convert_magicitem(item: Dict) -> Dict:
    """Transform a single Open5e magic item into engine ItemDefinition format."""
    name = item.get("name", "")
    desc = item.get("desc", "") or ""
    type_str = item.get("type", "") or ""
    item_type = parse_item_type(type_str)
    rarity = parse_rarity(item.get("rarity", ""))
    requires_attune, attune_req = parse_attunement(item.get("requires_attunement", ""))

    # Build type-specific properties
    weapon_props = parse_weapon_properties(type_str, desc)
    armor_props = parse_armor_properties(type_str, desc)
    consumable_props = parse_consumable_properties(item_type, desc)
    magic_props = parse_magic_properties(desc, item_type)

    return {
        "item_id": _slugify(name),
        "name": name,
        "item_type": item_type,
        "description": desc.strip(),
        "weight": 0.0,
        "value_gp": 0.0,
        "rarity": rarity,
        "magical": True,
        "requires_attunement": requires_attune,
        "attunement_requirements": attune_req,
        "tags": generate_tags(desc, item_type),
        "weapon_properties": weapon_props,
        "armor_properties": armor_props,
        "ammunition_properties": None,
        "consumable_properties": consumable_props,
        "scroll_properties": None,
        "tool_properties": None,
        "instrument_properties": None,
        "container_properties": None,
        "magic_properties": magic_props,
    }


def main():
    parser = argparse.ArgumentParser(description="Convert Open5e magic item data to TTRPG engine item format.")
    parser.add_argument("--output-dir", default="magicitems", help="Directory to save item JSON files")
    parser.add_argument("--local-file", help="Local JSON file containing Open5e magic item data (instead of API)")
    parser.add_argument("--include-source", nargs="+", metavar="SOURCE",
                        help="Only include items from these document slugs (e.g., wotc-srd).")
    args = parser.parse_args()

    if args.local_file:
        items = load_local_magicitems(Path(args.local_file))
    else:
        print("Fetching magic items from Open5e API...")
        items = fetch_open5e_magicitems()
        print(f"Fetched {len(items)} magic items.")

    if args.include_source:
        items = [i for i in items if i.get("document__slug", "") in args.include_source]
        print(f"Filtered to {len(items)} items matching sources: {args.include_source}")

    output_path = Path(args.output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    errors = 0

    for item in items:
        try:
            converted = convert_magicitem(item)
            validated = ItemBase.model_validate(converted)
            filename = f"{converted['item_id']}.json"
            filepath = output_path / filename

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(validated.model_dump(mode="json"), f, indent=2, ensure_ascii=False)
            print(f"Saved: {filepath}")

        except Exception as e:
            errors += 1
            print(f"Error converting item {item.get('name', 'unknown')}: {e}")

    print(f"Conversion complete. {len(items) - errors} succeeded, {errors} errors.")


if __name__ == "__main__":
    main()
