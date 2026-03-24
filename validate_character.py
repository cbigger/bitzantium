"""
validate_character.py

Usage:
    python validate_character.py <character.json>

Loads a character sheet, validates it against the PlayerSheet schema,
and prints the list of available tools for the current game state.
"""

import json
import sys
from pathlib import Path

from character import PlayerSheet, CharacterState, ActionEconomy
from registry import get_available_tools


def main():
    if len(sys.argv) < 2:
        print("Usage: python validate_character.py <character.json>")
        sys.exit(1)

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"File not found: {path}")
        sys.exit(1)

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    try:
        sheet = PlayerSheet(**data.get("sheet", data))
    except Exception as e:
        print(f"Schema validation failed:\n{e}")
        sys.exit(1)

    state = CharacterState(sheet=sheet, economy=ActionEconomy(**data.get("economy", {})))
    tools = get_available_tools(state)

    classes = " / ".join(
        f"{c.class_id} {c.level}" + (f" ({c.subclass_id})" if c.subclass_id else "")
        for c in sheet.classes
    )

    print(f"Character  : {sheet.name}")
    print(f"Creature   : {sheet.creature_id or '—'}")
    print(f"Race       : {sheet.race_id or '—'}")
    print(f"Background : {sheet.background_id or '—'}")
    print(f"Classes    : {classes or '—'}")
    print(f"Level      : {sheet.level}")
    print(f"HP         : {sheet.hp_current}/{sheet.hp_max}")
    print(f"Conditions : {', '.join(c.value for c in sheet.conditions) or 'none'}")
    print(f"Action     : {'spent' if state.economy.action_spent else 'available'}")
    print(f"Bonus act. : {'spent' if state.economy.bonus_action_spent else 'available'}")
    print(f"Reaction   : {'spent' if state.economy.reaction_spent else 'available'}")
    print()
    print(f"Available tools ({len(tools)}):")
    for tool in tools:
        print(f"  {tool['name']}")


if __name__ == "__main__":
    main()
