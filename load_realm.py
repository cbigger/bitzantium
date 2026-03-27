#!/usr/bin/env python3
"""
load_realm.py

CLI script to load a RealmTemplate directory into the database.

Usage:
    .venv/bin/python3 load_realm.py Realms/dnd/

Walks the directory, validates each JSON file against its Pydantic schema,
and inserts all valid objects into the realm_objects table. Clears existing
realm data before inserting (full replacement).
"""

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "bitzantium_schemas" / "src"))

import db
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


def _validate_file(path: Path, model) -> tuple[object | None, str | None]:
    """Parse and validate a single JSON file. Returns (obj, id) or (None, None)."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        log.warning("JSON parse error in %s: %s", path, e)
        return None, None

    try:
        obj = model(**raw)
    except Exception as e:
        log.warning("Schema validation failed for %s: %s", path, e)
        return None, None

    return obj, raw


def _collect_directory(directory: Path, model, id_field: str) -> list[tuple[object, dict]]:
    """Validate all .json files in a directory (non-recursive). Returns list of (obj, raw_dict)."""
    results = []
    if not directory.exists():
        return results
    for path in sorted(directory.glob("*.json")):
        obj, raw = _validate_file(path, model)
        if obj is not None:
            data_id = getattr(obj, id_field, None)
            if data_id:
                results.append((data_id, obj))
            else:
                log.warning("Missing or empty %r in %s — skipping", id_field, path)
    return results


def load_realm_to_db(base_path: str | Path) -> dict[str, int]:
    """Walk a RealmTemplate directory and insert all objects into the DB.

    Clears existing realm data first. Returns a summary dict.
    """
    base = Path(base_path)
    if not base.exists():
        print(f"Error: directory does not exist: {base}")
        sys.exit(1)

    db.create_tables()

    rows_to_insert: list[dict] = []
    summary: dict[str, int] = {}
    total_errors = 0

    # --- Abilities ---
    abilities_dir = base / "abilities"
    collected = _collect_directory(abilities_dir, AbilityDefinition, "ability_id")
    for data_id, obj in collected:
        rows_to_insert.append({
            "category": "ability",
            "data_id": data_id,
            "data": obj.model_dump(mode="json"),
            "is_sapient": False,
        })
    summary["abilities"] = len(collected)

    # --- Classes ---
    classes_dir = base / "character" / "classes"
    collected = _collect_directory(classes_dir, ClassDefinition, "class_id")
    for data_id, obj in collected:
        rows_to_insert.append({
            "category": "class",
            "data_id": data_id,
            "data": obj.model_dump(mode="json"),
            "is_sapient": False,
        })
    summary["classes"] = len(collected)

    # --- Subclasses ---
    subclasses_dir = base / "character" / "subclasses"
    collected = _collect_directory(subclasses_dir, SubclassDefinition, "subclass_id")
    for data_id, obj in collected:
        rows_to_insert.append({
            "category": "subclass",
            "data_id": data_id,
            "data": obj.model_dump(mode="json"),
            "is_sapient": False,
        })
    summary["subclasses"] = len(collected)

    # --- Backgrounds ---
    backgrounds_dir = base / "character" / "backgrounds"
    collected = _collect_directory(backgrounds_dir, BackgroundDefinition, "background_id")
    for data_id, obj in collected:
        rows_to_insert.append({
            "category": "background",
            "data_id": data_id,
            "data": obj.model_dump(mode="json"),
            "is_sapient": False,
        })
    summary["backgrounds"] = len(collected)

    # --- Items (recursive) ---
    items_dir = base / "items"
    item_count = 0
    if items_dir.exists():
        for path in sorted(items_dir.rglob("*.json")):
            obj, raw = _validate_file(path, ItemBase)
            if obj is not None:
                data_id = getattr(obj, "item_id", None)
                if data_id:
                    rows_to_insert.append({
                        "category": "item",
                        "data_id": data_id,
                        "data": obj.model_dump(mode="json"),
                        "is_sapient": False,
                    })
                    item_count += 1
                else:
                    log.warning("Missing item_id in %s — skipping", path)
            else:
                total_errors += 1
    summary["items"] = item_count

    # --- Sapient creatures + races ---
    sapient_dir = base / "creatures" / "sapient"
    creature_count = race_count = 0
    if sapient_dir.exists():
        for species_dir in sorted(p for p in sapient_dir.iterdir() if p.is_dir()):
            for path in sorted(species_dir.glob("*.json")):
                if path.stem == "defaultClass":
                    continue
                obj, raw = _validate_file(path, CreatureDefinition)
                if obj is not None:
                    data_id = getattr(obj, "creature_id", None)
                    if data_id:
                        rows_to_insert.append({
                            "category": "creature",
                            "data_id": data_id,
                            "data": obj.model_dump(mode="json"),
                            "is_sapient": True,
                        })
                        creature_count += 1
                else:
                    total_errors += 1

            # Races within this species
            races_dir = species_dir / "races"
            if races_dir.exists():
                for path in sorted(races_dir.glob("*.json")):
                    obj, raw = _validate_file(path, RaceDefinition)
                    if obj is not None:
                        data_id = getattr(obj, "race_id", None)
                        if data_id:
                            rows_to_insert.append({
                                "category": "race",
                                "data_id": data_id,
                                "data": obj.model_dump(mode="json"),
                                "is_sapient": False,
                            })
                            race_count += 1
                    else:
                        total_errors += 1

    # --- Sentient creatures (non-playable) ---
    sentient_dir = base / "creatures" / "sentient"
    if sentient_dir.exists():
        for creature_dir in sorted(p for p in sentient_dir.iterdir() if p.is_dir()):
            for path in sorted(creature_dir.glob("*.json")):
                obj, raw = _validate_file(path, CreatureDefinition)
                if obj is not None:
                    data_id = getattr(obj, "creature_id", None)
                    if data_id:
                        rows_to_insert.append({
                            "category": "creature",
                            "data_id": data_id,
                            "data": obj.model_dump(mode="json"),
                            "is_sapient": False,
                        })
                        creature_count += 1
                else:
                    total_errors += 1

    summary["creatures"] = creature_count
    summary["races"] = race_count

    # --- Insert into DB ---
    cleared = db.clear_realm_objects()
    if cleared:
        print(f"Cleared {cleared} existing realm objects.")

    inserted = db.bulk_insert_realm_objects(rows_to_insert)
    print(f"Inserted {inserted} realm objects: {summary}")

    if total_errors:
        print(f"Warning: {total_errors} file(s) failed validation (see log warnings).")
        return summary

    return summary


def main():
    parser = argparse.ArgumentParser(description="Load a RealmTemplate directory into the database.")
    parser.add_argument("realm_dir", type=str, help="Path to the RealmTemplate directory (e.g., Realms/dnd/)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    load_realm_to_db(args.realm_dir)


if __name__ == "__main__":
    main()
