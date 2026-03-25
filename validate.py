"""
validate.py

Loads all realm data through the loader and cross-references loaded schema
objects against each other to find integrity issues.

Checks performed:
  - Schema validation (any file that fails to load via the loader)
  - Starting equipment: every item_id in class/background starting_equipment
    must exist in the loaded item registry
  - Subclass parent references: every subclass.parent_class_id must exist
    in the loaded class registry
  - Race parent references: every race.parent_creature_id must exist
    in the loaded creature registry

Usage:
    python validate.py [REALM_DIR]
"""

import argparse
import logging
import sys

import loader

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")


def validate_starting_equipment() -> list[str]:
    """Check that all starting_equipment item_ids resolve in the item registry."""
    issues = []
    items = set(loader.list_items())

    for class_id in loader.list_classes():
        cls = loader.get_class(class_id)
        for item_id in cls.starting_equipment:
            if item_id not in items:
                issues.append(f"class:{class_id} references missing item '{item_id}'")

    for bg_id in loader.list_backgrounds():
        bg = loader.get_background(bg_id)
        for item_id in bg.starting_equipment:
            if item_id not in items:
                issues.append(f"background:{bg_id} references missing item '{item_id}'")

    return issues


def validate_subclass_parents() -> list[str]:
    """Check that every subclass references a loaded class."""
    issues = []
    classes = set(loader.list_classes())
    for sc_id in loader.list_subclasses():
        sc = loader.get_subclass(sc_id)
        if sc.parent_class_id not in classes:
            issues.append(f"subclass:{sc_id} references missing class '{sc.parent_class_id}'")
    return issues


def validate_race_parents() -> list[str]:
    """Check that every race references a loaded creature."""
    issues = []
    creatures = set(loader.list_creatures())
    for race_id in loader.list_races():
        race = loader.get_race(race_id)
        if race.parent_creature_id not in creatures:
            issues.append(f"race:{race_id} references missing creature '{race.parent_creature_id}'")
    return issues


def main():
    parser = argparse.ArgumentParser(description="Validate loaded realm data integrity.")
    parser.add_argument("realm_dir", nargs="?", default="Realms/dnd",
                        help="Path to the realm directory (default: Realms/dnd)")
    args = parser.parse_args()

    print(f"Loading realm: {args.realm_dir}")
    summary = loader.load_all(args.realm_dir)
    print(f"  Loaded: {summary}\n")

    all_issues = []

    print("Checking starting equipment references...")
    issues = validate_starting_equipment()
    all_issues.extend(issues)

    print("Checking subclass → class references...")
    issues = validate_subclass_parents()
    all_issues.extend(issues)

    print("Checking race → creature references...")
    issues = validate_race_parents()
    all_issues.extend(issues)

    print()
    if all_issues:
        print(f"FOUND {len(all_issues)} ISSUE(S):\n")
        for issue in all_issues:
            print(f"  {issue}")
        sys.exit(1)
    else:
        print("All checks passed.")
        sys.exit(0)


if __name__ == "__main__":
    main()
