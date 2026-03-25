"""
mundaneitem_converter.py

Fetches weapons and armor from the Open5e API and generates mundane item
JSON files.  Adventuring gear, tools, instruments, packs, and other
non-weapon/non-armor items are defined in a static table (Open5e has no
general equipment endpoint).

Usage:
    python mundaneitem_converter.py [--output-dir OUTPUT_DIR]
                                    [--include-source SOURCE [SOURCE ...]]
"""

import argparse
import json
import re
import requests
from pathlib import Path
from typing import Dict, List, Optional

from bitzantium_schemas.data import ItemBase

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OPEN5E_API_BASE = "https://api.open5e.com/v1"
WEAPONS_ENDPOINT = f"{OPEN5E_API_BASE}/weapons/"
ARMOR_ENDPOINT = f"{OPEN5E_API_BASE}/armor/"

# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def _fetch_paginated(url: str) -> List[Dict]:
    results = []
    while url:
        print(f"  Fetching {url}")
        resp = requests.get(url)
        resp.raise_for_status()
        data = resp.json()
        results.extend(data.get("results", []))
        url = data.get("next")
    return results


def fetch_weapons(source_filter: Optional[List[str]] = None) -> List[Dict]:
    items = _fetch_paginated(f"{WEAPONS_ENDPOINT}?limit=100")
    if source_filter:
        items = [i for i in items if i.get("document__slug") in source_filter]
    return items


def fetch_armor(source_filter: Optional[List[str]] = None) -> List[Dict]:
    items = _fetch_paginated(f"{ARMOR_ENDPOINT}?limit=100")
    if source_filter:
        items = [i for i in items if i.get("document__slug") in source_filter]
    return items


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _parse_cost(cost_str: str) -> float:
    """Parse '15 gp' or '5 sp' into gp value."""
    if not cost_str:
        return 0.0
    m = re.match(r"([\d,]+)\s*(cp|sp|ep|gp|pp)", cost_str.strip(), re.IGNORECASE)
    if not m:
        return 0.0
    amount = float(m.group(1).replace(",", ""))
    unit = m.group(2).lower()
    rates = {"cp": 0.01, "sp": 0.1, "ep": 0.5, "gp": 1.0, "pp": 10.0}
    return amount * rates.get(unit, 1.0)


def _parse_weight(weight_str: str) -> float:
    if not weight_str:
        return 0.0
    m = re.search(r"([\d.]+)", weight_str)
    return float(m.group(1)) if m else 0.0


# ---------------------------------------------------------------------------
# Weapon conversion
# ---------------------------------------------------------------------------

_DAMAGE_TYPE_MAP = {
    "bludgeoning": "bludgeoning", "piercing": "piercing", "slashing": "slashing",
}


def convert_weapon(w: Dict) -> Dict:
    slug = _slugify(w["name"])
    category_raw = (w.get("category") or "").lower()
    is_martial = "martial" in category_raw
    is_ranged = "ranged" in category_raw

    props_raw = w.get("properties") or []
    props = [p.lower().strip() for p in props_raw]

    # Extract versatile damage
    versatile_damage = None
    clean_props = []
    for p in props:
        vm = re.match(r"versatile\s*\((\d+d\d+)\)", p)
        if vm:
            versatile_damage = vm.group(1)
            clean_props.append("versatile")
        else:
            clean_props.append(p)

    # Extract range for ranged/thrown
    range_normal = range_max = None
    final_props = []
    for p in clean_props:
        rm = re.match(r"(?:ammunition|thrown)\s*\(range\s+(\d+)/(\d+)\)", p)
        if rm:
            range_normal = int(rm.group(1))
            range_max = int(rm.group(2))
            tag = "ammunition" if p.startswith("ammunition") else "thrown"
            final_props.append(tag)
        else:
            final_props.append(p)

    damage_type = _DAMAGE_TYPE_MAP.get((w.get("damage_type") or "").lower(), "bludgeoning")

    return {
        "item_id": slug,
        "name": w["name"],
        "item_type": "weapon",
        "description": "",
        "weight": _parse_weight(w.get("weight", "")),
        "value_gp": _parse_cost(w.get("cost", "")),
        "rarity": "mundane",
        "magical": False,
        "requires_attunement": False,
        "attunement_requirements": None,
        "tags": final_props,
        "weapon_properties": {
            "category": "martial" if is_martial else "simple",
            "weapon_type": "ranged" if is_ranged else "melee",
            "damage_dice": w.get("damage_dice") or "1d4",
            "damage_type": damage_type,
            "versatile_damage": versatile_damage,
            "attack_bonus": 0,
            "damage_bonus": 0,
            "range_normal": range_normal,
            "range_max": range_max,
            "properties": final_props,
            "silvered": False,
            "adamantine": False,
        },
    }


# ---------------------------------------------------------------------------
# Armor conversion
# ---------------------------------------------------------------------------

_ARMOR_CATEGORY_MAP = {
    "light armor": "light",
    "medium armor": "medium",
    "heavy armor": "heavy",
    "shield": "shield",
}


def convert_armor(a: Dict) -> Optional[Dict]:
    category_raw = (a.get("category") or "").lower()
    armor_type = _ARMOR_CATEGORY_MAP.get(category_raw)
    if not armor_type:
        return None  # skip non-armor entries like "Draconic Resilience"

    slug = _slugify(a["name"])

    max_dex = None
    if a.get("plus_dex_mod"):
        max_dex = a.get("plus_max") if a.get("plus_max") else None

    item_type = "shield" if armor_type == "shield" else "armor"

    return {
        "item_id": slug,
        "name": a["name"],
        "item_type": item_type,
        "description": "",
        "weight": _parse_weight(a.get("weight", "")),
        "value_gp": _parse_cost(a.get("cost", "")),
        "rarity": "mundane",
        "magical": False,
        "requires_attunement": False,
        "attunement_requirements": None,
        "tags": [],
        "armor_properties": {
            "armor_type": armor_type,
            "base_ac": a.get("base_ac", 10),
            "ac_bonus": a.get("plus_flat_mod", 0),
            "max_dex_bonus": max_dex,
            "strength_requirement": a.get("strength_requirement") or 0,
            "stealth_disadvantage": bool(a.get("stealth_disadvantage")),
            "don_time_minutes": 1 if armor_type == "light" else 5 if armor_type == "medium" else 10,
            "doff_time_minutes": 1 if armor_type in ("light", "shield") else 5,
        },
    }


# ---------------------------------------------------------------------------
# Static adventuring gear / tools / packs / misc
# Open5e has no general equipment endpoint, so these are defined here.
# Minimal entries: name is enough for the DM to infer details.
# ---------------------------------------------------------------------------

def _gear(item_id: str, name: str, weight: float = 0.0, value_gp: float = 0.0,
          item_type: str = "adventuring_gear", **extra) -> Dict:
    d = {
        "item_id": item_id,
        "name": name,
        "item_type": item_type,
        "description": "",
        "weight": weight,
        "value_gp": value_gp,
        "rarity": "mundane",
        "magical": False,
        "requires_attunement": False,
        "attunement_requirements": None,
        "tags": [],
    }
    d.update(extra)
    return d


def _tool(item_id: str, name: str, category: str, weight: float = 0.0,
          value_gp: float = 0.0, skills: Optional[List[str]] = None) -> Dict:
    return _gear(item_id, name, weight, value_gp, item_type="tool",
                 tool_properties={"tool_category": category,
                                  "associated_skills": skills or []})


def _instrument(item_id: str, name: str, weight: float = 0.0,
                value_gp: float = 0.0) -> Dict:
    return _gear(item_id, name, weight, value_gp, item_type="instrument",
                 instrument_properties={"instrument_type": item_id,
                                        "associated_skills": ["performance"]})


STATIC_ITEMS: List[Dict] = [
    # ── Packs (containers with contents — modelled as single gear items) ──
    _gear("a_a_scholar_s_pack", "Scholar's Pack", 5.0, 40.0, item_type="container"),
    _gear("b_a_dungeoneer_s_pack", "Dungeoneer's Pack", 12.0, 12.0, item_type="container"),
    _gear("dungeoneer_s_pack", "Dungeoneer's Pack", 12.0, 12.0, item_type="container"),
    _gear("explorer_s_pack", "Explorer's Pack", 10.0, 10.0, item_type="container"),
    _gear("diplomat_s_pack", "Diplomat's Pack", 8.0, 39.0, item_type="container"),
    _gear("burglar_s_pack", "Burglar's Pack", 11.0, 16.0, item_type="container"),
    _gear("priest_s_pack", "Priest's Pack", 7.0, 19.0, item_type="container"),

    # ── Spellcasting focuses and components ──
    _gear("component_pouch", "Component Pouch", 2.0, 25.0),
    _gear("a_a_component_pouch", "Component Pouch", 2.0, 25.0),
    _gear("spellbook", "Spellbook", 3.0, 50.0),
    _gear("holy_symbol", "Holy Symbol", 0.0, 5.0),
    _gear("druidic_focus", "Druidic Focus", 0.0, 5.0),
    _gear("or_prayer_beads", "Prayer Beads", 0.0, 5.0),

    # ── Tools ──
    _tool("thieves_tools", "Thieves' Tools", "thieves_tools", 1.0, 25.0,
          skills=["sleight_of_hand"]),
    _tool("disguise_kit", "Disguise Kit", "disguise_kit", 3.0, 25.0,
          skills=["deception"]),
    _tool("forgery_kit", "Forgery Kit", "forgery_kit", 5.0, 15.0,
          skills=["deception"]),
    _tool("herbalism_kit", "Herbalism Kit", "herbalism_kit", 3.0, 5.0,
          skills=["medicine", "nature"]),
    _tool("navigator_s_tools", "Navigator's Tools", "navigators_tools", 2.0, 25.0,
          skills=["survival"]),
    _tool("healer_s_kit", "Healer's Kit", "other", 3.0, 5.0,
          skills=["medicine"]),
    _tool("healer_s_satchel", "Healer's Satchel", "other", 3.0, 5.0,
          skills=["medicine"]),
    _tool("set_of_artisan_s_tools", "Artisan's Tools", "artisan", 5.0, 10.0),
    _tool("any_artisan_s_tools_except_alchemist_s_supplies",
          "Artisan's Tools", "artisan", 5.0, 10.0),
    _tool("set_of_artisan_s_tools_or_one_instrument",
          "Artisan's Tools or Instrument", "artisan", 5.0, 10.0),

    # ── Gaming sets ──
    _tool("dice_set", "Dice Set", "gaming_set", 0.0, 0.1),
    _tool("bone_dice_set", "Bone Dice Set", "gaming_set", 0.0, 0.1),
    _tool("playing_card_set", "Playing Card Set", "gaming_set", 0.0, 0.5),

    # ── Instruments ──
    _instrument("lute", "Lute", 2.0, 35.0),
    _instrument("lute_or_other_musical_instrument", "Musical Instrument", 2.0, 30.0),

    # ── Clothing ──
    _gear("common_clothes", "Common Clothes", 3.0, 0.5),
    _gear("fine_clothes", "Fine Clothes", 6.0, 15.0),
    _gear("traveler_s_clothes", "Traveler's Clothes", 4.0, 2.0),
    _gear("costume", "Costume", 4.0, 5.0),
    _gear("dark_cloak", "Dark Cloak", 1.0, 0.5),
    _gear("vestments", "Vestments", 4.0, 1.0),

    # ── Misc adventuring gear ──
    _gear("pouch", "Pouch", 1.0, 0.5, item_type="container"),
    _gear("waterskin", "Waterskin", 5.0, 0.2),
    _gear("feet_of_rope", "50 Feet of Rope", 10.0, 1.0),
    _gear("tent", "Tent", 20.0, 2.0),
    _gear("shovel", "Shovel", 5.0, 2.0),
    _gear("supply", "Supplies", 1.0, 0.1),
    _gear("days_rations", "Rations (1 day)", 2.0, 0.5),
    _gear("bottle_of_ink", "Bottle of Ink", 0.0, 10.0),
    _gear("pen", "Pen", 0.0, 0.02),
    _gear("sheets_of_parchment", "Sheets of Parchment", 0.0, 1.0),
    _gear("incense", "Incense", 0.0, 0.01),
    _gear("prayer_book", "Prayer Book", 3.0, 5.0),
    _gear("prayer_book_or_prayer_wheel", "Prayer Book or Prayer Wheel", 3.0, 5.0),
    _gear("prayer_wheel", "Prayer Wheel", 2.0, 5.0),
    _gear("abacus", "Abacus", 2.0, 2.0),
    _gear("merchant_s_scale", "Merchant's Scale", 3.0, 5.0),
    _gear("signal_whistle", "Signal Whistle", 0.0, 0.05),
    _gear("trophy_from_fallen_enemy", "Trophy from Fallen Enemy", 0.0, 0.0),
    _gear("insignia_of_rank", "Insignia of Rank", 0.0, 0.0),
    _gear("guild_badge", "Guild Badge", 0.0, 0.0),
    _gear("mule_with_saddlebags", "Mule with Saddlebags", 0.0, 12.0),

    # ── Ammunition ──
    _gear("bolts", "Crossbow Bolts (20)", 1.5, 1.0, item_type="ammunition",
          ammunition_properties={
              "compatible_weapon_types": ["light_crossbow", "heavy_crossbow", "hand_crossbow"],
              "attack_bonus": 0, "damage_bonus": 0, "silvered": False,
              "quantity_per_purchase": 20}),
    _gear("quiver_of_20_arrows", "Quiver of 20 Arrows", 1.0, 1.0, item_type="ammunition",
          ammunition_properties={
              "compatible_weapon_types": ["shortbow", "longbow"],
              "attack_bonus": 0, "damage_bonus": 0, "silvered": False,
              "quantity_per_purchase": 20}),

    # ── Alias items referenced with choice prefixes in starting equipment ──
    _gear("a_a_quarterstaff", "Quarterstaff", 4.0, 0.2, item_type="weapon",
          weapon_properties={
              "category": "simple", "weapon_type": "melee",
              "damage_dice": "1d6", "damage_type": "bludgeoning",
              "versatile_damage": "1d8",
              "attack_bonus": 0, "damage_bonus": 0,
              "range_normal": None, "range_max": None,
              "properties": ["versatile"], "silvered": False, "adamantine": False}),
    _gear("light_crossbow", "Light Crossbow", 5.0, 25.0, item_type="weapon",
          weapon_properties={
              "category": "simple", "weapon_type": "ranged",
              "damage_dice": "1d8", "damage_type": "piercing",
              "versatile_damage": None,
              "attack_bonus": 0, "damage_bonus": 0,
              "range_normal": 80, "range_max": 320,
              "properties": ["ammunition", "loading", "two-handed"],
              "silvered": False, "adamantine": False}),
    _gear("a_a_light_crossbow", "Light Crossbow", 5.0, 25.0, item_type="weapon",
          weapon_properties={
              "category": "simple", "weapon_type": "ranged",
              "damage_dice": "1d8", "damage_type": "piercing",
              "versatile_damage": None,
              "attack_bonus": 0, "damage_bonus": 0,
              "range_normal": 80, "range_max": 320,
              "properties": ["ammunition", "loading", "two-handed"],
              "silvered": False, "adamantine": False}),
    _gear("b_a_longsword", "Longsword", 3.0, 15.0, item_type="weapon",
          weapon_properties={
              "category": "martial", "weapon_type": "melee",
              "damage_dice": "1d8", "damage_type": "slashing",
              "versatile_damage": "1d10",
              "attack_bonus": 0, "damage_bonus": 0,
              "range_normal": None, "range_max": None,
              "properties": ["versatile"], "silvered": False, "adamantine": False}),
    _gear("b_leather_armor", "Leather Armor", 10.0, 10.0, item_type="armor",
          armor_properties={
              "armor_type": "light", "base_ac": 11, "ac_bonus": 0,
              "max_dex_bonus": None, "strength_requirement": 0,
              "stealth_disadvantage": False,
              "don_time_minutes": 1, "doff_time_minutes": 1}),

    # ── Multi-item references (quantity variants) ──
    _gear("two_daggers", "Dagger", 1.0, 2.0, item_type="weapon",
          weapon_properties={
              "category": "simple", "weapon_type": "melee",
              "damage_dice": "1d4", "damage_type": "piercing",
              "versatile_damage": None,
              "attack_bonus": 0, "damage_bonus": 0,
              "range_normal": 20, "range_max": 60,
              "properties": ["finesse", "light", "thrown"],
              "silvered": False, "adamantine": False}),
    _gear("two_handaxes", "Handaxe", 2.0, 5.0, item_type="weapon",
          weapon_properties={
              "category": "simple", "weapon_type": "melee",
              "damage_dice": "1d6", "damage_type": "slashing",
              "versatile_damage": None,
              "attack_bonus": 0, "damage_bonus": 0,
              "range_normal": 20, "range_max": 60,
              "properties": ["light", "thrown"],
              "silvered": False, "adamantine": False}),
    _gear("two_shortswords", "Shortsword", 2.0, 10.0, item_type="weapon",
          weapon_properties={
              "category": "martial", "weapon_type": "melee",
              "damage_dice": "1d6", "damage_type": "piercing",
              "versatile_damage": None,
              "attack_bonus": 0, "damage_bonus": 0,
              "range_normal": None, "range_max": None,
              "properties": ["finesse", "light"],
              "silvered": False, "adamantine": False}),
    _gear("four_javelins", "Javelin", 2.0, 0.5, item_type="weapon",
          weapon_properties={
              "category": "simple", "weapon_type": "melee",
              "damage_dice": "1d6", "damage_type": "piercing",
              "versatile_damage": None,
              "attack_bonus": 0, "damage_bonus": 0,
              "range_normal": 30, "range_max": 120,
              "properties": ["thrown"],
              "silvered": False, "adamantine": False}),
    _gear("five_javelins", "Javelin", 2.0, 0.5, item_type="weapon",
          weapon_properties={
              "category": "simple", "weapon_type": "melee",
              "damage_dice": "1d6", "damage_type": "piercing",
              "versatile_damage": None,
              "attack_bonus": 0, "damage_bonus": 0,
              "range_normal": 30, "range_max": 120,
              "properties": ["thrown"],
              "silvered": False, "adamantine": False}),
    _gear("darts", "Dart", 0.25, 0.05, item_type="weapon",
          weapon_properties={
              "category": "simple", "weapon_type": "ranged",
              "damage_dice": "1d4", "damage_type": "piercing",
              "versatile_damage": None,
              "attack_bonus": 0, "damage_bonus": 0,
              "range_normal": 20, "range_max": 60,
              "properties": ["finesse", "thrown"],
              "silvered": False, "adamantine": False}),

    # ── Generic choice placeholders ──
    _gear("any_simple_weapon", "Simple Weapon", 2.0, 1.0, item_type="weapon",
          weapon_properties={
              "category": "simple", "weapon_type": "melee",
              "damage_dice": "1d6", "damage_type": "bludgeoning",
              "versatile_damage": None,
              "attack_bonus": 0, "damage_bonus": 0,
              "range_normal": None, "range_max": None,
              "properties": [], "silvered": False, "adamantine": False}),
    _gear("or_c_any_simple_weapon", "Simple Weapon", 2.0, 1.0, item_type="weapon",
          weapon_properties={
              "category": "simple", "weapon_type": "melee",
              "damage_dice": "1d6", "damage_type": "bludgeoning",
              "versatile_damage": None,
              "attack_bonus": 0, "damage_bonus": 0,
              "range_normal": None, "range_max": None,
              "properties": [], "silvered": False, "adamantine": False}),
    _gear("martial_weapon", "Martial Weapon", 3.0, 15.0, item_type="weapon",
          weapon_properties={
              "category": "martial", "weapon_type": "melee",
              "damage_dice": "1d8", "damage_type": "slashing",
              "versatile_damage": None,
              "attack_bonus": 0, "damage_bonus": 0,
              "range_normal": None, "range_max": None,
              "properties": [], "silvered": False, "adamantine": False}),
    _gear("or_c_chain_mail_if_proficient", "Chain Mail", 55.0, 75.0, item_type="armor",
          armor_properties={
              "armor_type": "heavy", "base_ac": 16, "ac_bonus": 0,
              "max_dex_bonus": 0, "strength_requirement": 13,
              "stealth_disadvantage": True,
              "don_time_minutes": 10, "doff_time_minutes": 5}),
    _gear("or_c_an_explorer_s_pack", "Explorer's Pack", 10.0, 10.0, item_type="container"),
]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def save_item(item_dict: Dict, output_dir: Path) -> bool:
    """Validate against ItemBase and write to JSON file. Returns True on success."""
    try:
        validated = ItemBase.model_validate(item_dict)
    except Exception as e:
        print(f"  WARN: validation failed for {item_dict.get('item_id', '?')}: {e}")
        return False
    path = output_dir / f"{validated.item_id}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(validated.model_dump(mode="json"), f, indent=2, ensure_ascii=False)
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Generate mundane item JSON files from Open5e weapons/armor + static gear table."
    )
    parser.add_argument("--output-dir", default="Realms/dnd/items/mundane",
                        help="Directory to save mundane item JSON files")
    parser.add_argument("--include-source", nargs="+", metavar="SOURCE",
                        default=["wotc-srd"],
                        help="Only include items from these document slugs (default: wotc-srd)")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    saved = errors = 0

    # Weapons from API
    print("Fetching weapons from Open5e...")
    weapons = fetch_weapons(args.include_source)
    print(f"  Got {len(weapons)} weapons.")
    for w in weapons:
        item = convert_weapon(w)
        if save_item(item, output_dir):
            saved += 1
        else:
            errors += 1

    # Armor from API
    print("Fetching armor from Open5e...")
    armor_list = fetch_armor(args.include_source)
    print(f"  Got {len(armor_list)} armor entries.")
    for a in armor_list:
        item = convert_armor(a)
        if item is None:
            continue  # non-armor entry (e.g., class feature)
        if save_item(item, output_dir):
            saved += 1
        else:
            errors += 1

    # Static gear/tools/packs
    print("Writing static adventuring gear, tools, and packs...")
    for item in STATIC_ITEMS:
        if save_item(item, output_dir):
            saved += 1
        else:
            errors += 1

    print(f"\nDone. {saved} items saved to {output_dir}, {errors} errors.")


if __name__ == "__main__":
    main()
