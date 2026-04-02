#!/usr/bin/env python3
"""
reset_story.py
==============
Wipe all gameplay state while preserving accounts, characters, and realm data.

Clears:
    narrative_segments  — all story narrative
    dm_chat_history     — DM LLM conversation
    turn_states         — turn order, initiative, tick counter
    scene_states        — current scene / entity positions
    turn_contexts       — per-character story_so_far, location, quest_log

Usage:
    .venv/bin/python3 reset_story.py          # interactive confirm
    .venv/bin/python3 reset_story.py --yes    # skip confirm
"""

import argparse
import db


TABLES_TO_CLEAR = [
    db.NarrativeSegment,
    db.DmChatMessage,
    db.TurnStateRow,
    db.SceneStateRow,
    db.TurnContext,
]


def reset_story() -> dict[str, int]:
    """Delete all rows from gameplay tables and reset character economies.
    Returns {table: rows_deleted} plus an 'economies_reset' count."""
    counts: dict[str, int] = {}
    with db.SessionLocal() as session:
        for model in TABLES_TO_CLEAR:
            n = session.query(model).delete()
            counts[model.__tablename__] = n

        # Reset every character's action economy to fresh state
        characters = session.query(db.Character).all()
        for char in characters:
            cs = db.CharacterState.model_validate(char.character_state)
            cs = cs.model_copy(update={"economy": db.ActionEconomy()})
            char.character_state = cs.model_dump(mode="json")
        counts["economies_reset"] = len(characters)

        session.commit()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset all gameplay state.")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt.")
    args = parser.parse_args()

    if not args.yes:
        tables = ", ".join(m.__tablename__ for m in TABLES_TO_CLEAR)
        answer = input(f"This will delete all rows from: {tables}\nContinue? [y/N] ")
        if answer.lower() not in ("y", "yes"):
            print("Aborted.")
            return

    counts = reset_story()
    for table, n in counts.items():
        print(f"  {table}: {n} rows deleted")
    print("Story reset complete.")


if __name__ == "__main__":
    main()
