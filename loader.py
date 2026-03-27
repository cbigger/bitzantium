"""
loader.py

Populates in-memory registries from the realm_objects table in the database.

Usage:
    from loader import load_all, get_class, get_ability, get_item, ...

    load_all()  # reads from DB, populates registries

For loading realm data from a directory into the DB, see load_realm.py.
For filesystem-based validation, see validate.py.
"""

import json
import logging
from pathlib import Path
from typing import Optional

from bitzantium_schemas.data import (
    AbilityDefinition,
    BackgroundDefinition,
    ClassDefinition,
    CreatureDefinition,
    ItemBase,
    RaceDefinition,
    SubclassDefinition,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Registries
# ---------------------------------------------------------------------------

_abilities:    dict[str, AbilityDefinition]    = {}
_classes:      dict[str, ClassDefinition]      = {}
_subclasses:   dict[str, SubclassDefinition]   = {}
_backgrounds:  dict[str, BackgroundDefinition] = {}
_creatures:            dict[str, CreatureDefinition]   = {}
_sapient_creature_ids: set[str]                        = set()
_races:                dict[str, RaceDefinition]       = {}
_items:                dict[str, ItemBase]             = {}


# ---------------------------------------------------------------------------
# Category → (registry, model, id_field)
# ---------------------------------------------------------------------------

_CATEGORY_MAP: dict[str, tuple[dict, type, str]] = {
    "ability":    (_abilities,    AbilityDefinition,    "ability_id"),
    "class":      (_classes,      ClassDefinition,      "class_id"),
    "subclass":   (_subclasses,   SubclassDefinition,   "subclass_id"),
    "background": (_backgrounds,  BackgroundDefinition, "background_id"),
    "creature":   (_creatures,    CreatureDefinition,   "creature_id"),
    "race":       (_races,        RaceDefinition,       "race_id"),
    "item":       (_items,        ItemBase,             "item_id"),
}


# ---------------------------------------------------------------------------
# Filesystem helpers — used by load_realm.py and validate.py
# ---------------------------------------------------------------------------

def _load_file(path: Path, model, registry: dict, id_field: str) -> bool:
    """
    Parse a single JSON file against model, store in registry by id_field.
    Returns True on success, False on any error.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        log.warning("JSON parse error in %s: %s", path, e)
        return False

    try:
        obj = model(**raw)
    except Exception as e:
        log.warning("Schema validation failed for %s: %s", path, e)
        return False

    key = getattr(obj, id_field, None)
    if not key:
        log.warning("Missing or empty %r in %s — skipping", id_field, path)
        return False

    if key in registry:
        log.warning("Duplicate ID %r found in %s — overwriting previous entry", key, path)

    registry[key] = obj
    return True


def _load_directory(directory: Path, model, registry: dict, id_field: str) -> tuple[int, int]:
    """
    Load all .json files in a directory (non-recursive).
    Returns (loaded_count, error_count).
    """
    if not directory.exists():
        return 0, 0

    loaded = errors = 0
    for path in sorted(directory.glob("*.json")):
        if _load_file(path, model, registry, id_field):
            loaded += 1
        else:
            errors += 1

    return loaded, errors


def load_all_from_directory(base_path: str | Path) -> dict[str, int]:
    """Walk a RealmTemplate directory and populate registries from the filesystem.

    Used by validate.py and fabricate.py for offline/dev use. For normal server
    operation, use load_all() which reads from the database.
    """
    base = Path(base_path)
    summary: dict[str, int] = {}
    total_errors = 0

    # Abilities
    n, e = _load_directory(base / "abilities", AbilityDefinition, _abilities, "ability_id")
    summary["abilities"] = n
    total_errors += e

    # Classes
    n, e = _load_directory(base / "character" / "classes", ClassDefinition, _classes, "class_id")
    summary["classes"] = n
    total_errors += e

    # Subclasses
    n, e = _load_directory(base / "character" / "subclasses", SubclassDefinition, _subclasses, "subclass_id")
    summary["subclasses"] = n
    total_errors += e

    # Backgrounds
    n, e = _load_directory(base / "character" / "backgrounds", BackgroundDefinition, _backgrounds, "background_id")
    summary["backgrounds"] = n
    total_errors += e

    # Items — recursive
    items_dir = base / "items"
    item_count = item_errors = 0
    if items_dir.exists():
        for path in sorted(items_dir.rglob("*.json")):
            if _load_file(path, ItemBase, _items, "item_id"):
                item_count += 1
            else:
                item_errors += 1
    summary["items"] = item_count
    total_errors += item_errors

    # Sapient creatures + races
    sapient_dir = base / "creatures" / "sapient"
    creature_count = race_count = creature_errors = race_errors = 0
    if sapient_dir.exists():
        for species_dir in sorted(p for p in sapient_dir.iterdir() if p.is_dir()):
            for path in sorted(species_dir.glob("*.json")):
                if path.stem == "defaultClass":
                    continue
                if _load_file(path, CreatureDefinition, _creatures, "creature_id"):
                    creature_count += 1
                    try:
                        cid = json.loads(path.read_text(encoding="utf-8")).get("creature_id")
                        if cid:
                            _sapient_creature_ids.add(cid)
                    except Exception:
                        pass
                else:
                    creature_errors += 1

            races_dir = species_dir / "races"
            n, e = _load_directory(races_dir, RaceDefinition, _races, "race_id")
            race_count += n
            race_errors += e

    # Sentient creatures
    sentient_dir = base / "creatures" / "sentient"
    if sentient_dir.exists():
        for creature_dir in sorted(p for p in sentient_dir.iterdir() if p.is_dir()):
            for path in sorted(creature_dir.glob("*.json")):
                if _load_file(path, CreatureDefinition, _creatures, "creature_id"):
                    creature_count += 1
                else:
                    creature_errors += 1

    summary["creatures"] = creature_count
    summary["races"]     = race_count
    total_errors += creature_errors + race_errors

    if total_errors:
        log.warning("Data load completed with %d error(s). See warnings above.", total_errors)
    else:
        log.info("Data load complete (filesystem): %s", summary)

    return summary


# ---------------------------------------------------------------------------
# Public load entry point — reads from database
# ---------------------------------------------------------------------------

def load_all() -> dict[str, int]:
    """
    Populate all registries from the realm_objects table in the database.
    Returns a summary dict of {category: count_loaded}.
    """
    import db

    rows = db.get_all_realm_objects()
    summary: dict[str, int] = {}
    errors = 0

    for row in rows:
        category = row["category"]
        data_id = row["data_id"]
        data = row["data"]
        is_sapient = row["is_sapient"]

        entry = _CATEGORY_MAP.get(category)
        if entry is None:
            log.warning("Unknown category %r for %r — skipping", category, data_id)
            errors += 1
            continue

        registry, model, id_field = entry

        try:
            obj = model(**data)
        except Exception as e:
            log.warning("Schema validation failed for %s/%s: %s", category, data_id, e)
            errors += 1
            continue

        registry[data_id] = obj
        summary[category] = summary.get(category, 0) + 1

        if category == "creature" and is_sapient:
            _sapient_creature_ids.add(data_id)

    if errors:
        log.warning("DB load completed with %d error(s).", errors)
    else:
        log.info("Data load complete (database): %s", summary)

    return summary


# ---------------------------------------------------------------------------
# Getters
# ---------------------------------------------------------------------------

def get_ability(ability_id: str) -> Optional[AbilityDefinition]:
    return _abilities.get(ability_id)

def get_class(class_id: str) -> Optional[ClassDefinition]:
    return _classes.get(class_id)

def get_subclass(subclass_id: str) -> Optional[SubclassDefinition]:
    return _subclasses.get(subclass_id)

def get_background(background_id: str) -> Optional[BackgroundDefinition]:
    return _backgrounds.get(background_id)

def get_creature(creature_id: str) -> Optional[CreatureDefinition]:
    return _creatures.get(creature_id)

def get_race(race_id: str) -> Optional[RaceDefinition]:
    return _races.get(race_id)

def get_item(item_id: str) -> Optional[ItemBase]:
    return _items.get(item_id)


def list_abilities()           -> list[str]: return list(_abilities.keys())
def list_classes()             -> list[str]: return list(_classes.keys())
def list_subclasses()          -> list[str]: return list(_subclasses.keys())
def list_backgrounds()         -> list[str]: return list(_backgrounds.keys())
def list_creatures()           -> list[str]: return list(_creatures.keys())
def list_sapient_creatures()   -> list[str]: return list(_sapient_creature_ids)
def list_races()               -> list[str]: return list(_races.keys())
def list_items()               -> list[str]: return list(_items.keys())
