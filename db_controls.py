"""
db_controls.py
==============
Account creation and DB population utilities.

Importable:
    from db_controls import create_account_with_character

Standalone:
    python db_controls.py

When run directly, clears all data (temp databases only), then loads
every *.json from characters/, validates against the CharacterState
schema, creates a claimed account + character row for each, and prints
the generated API keys.
"""

import json
import logging
import secrets
from pathlib import Path
from typing import Optional

import db
from bitzantium_schemas.character import CharacterState, ActionEconomy

log = logging.getLogger(__name__)

CHARACTERS_DIR = Path("characters")


# ---------------------------------------------------------------------------
# API key generation
# ---------------------------------------------------------------------------

def generate_api_key() -> str:
    """Generate a URL-safe random API key."""
    return secrets.token_urlsafe(16)


# ---------------------------------------------------------------------------
# Account creation — importable for future registration paths
# ---------------------------------------------------------------------------

def create_account_with_character(
    character_state: CharacterState,
    api_key: Optional[str] = None,
    claimed: bool = False,
) -> tuple[db.Account, db.Character, str]:
    """Create an account with API key and attach a character.

    Args:
        character_state: Validated CharacterState to store.
        api_key:         Explicit key, or None to auto-generate.
        claimed:         Whether the account starts claimed.

    Returns:
        (account, character, api_key)
    """
    if api_key is None:
        api_key = generate_api_key()

    account = db.create_account(api_key=api_key, claimed=claimed)
    entity_id = character_state.sheet.entity_id
    character = db.create_character(
        account_id=account.id,
        entity_id=entity_id,
        state=character_state,
    )
    # Create empty turn context so the row exists for later updates
    db.save_turn_context(character_id=character.id)

    return account, character, api_key


# ---------------------------------------------------------------------------
# Load + validate character JSON
# ---------------------------------------------------------------------------

def load_character_file(path: Path) -> CharacterState:
    """Load and validate a character JSON file against CharacterState schema.

    Raises on invalid JSON or schema validation failure.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    return CharacterState(
        sheet=raw["sheet"],
        economy=ActionEconomy(**raw.get("economy", {})),
    )


# ---------------------------------------------------------------------------
# Clear all data
# ---------------------------------------------------------------------------

def clear_all_data() -> None:
    """Drop all rows from every table. Refuses to run against non-temp databases."""
    import config
    db_url = config.database_url()
    if "temp" not in db_url:
        raise RuntimeError(
            f"Refusing to clear data: connection string does not contain 'temp'. "
            f"URL: {db_url}"
        )
    with db.SessionLocal() as session:
        session.query(db.TurnContext).delete()
        session.query(db.Character).delete()
        session.query(db.Account).delete()
        session.commit()
    log.info("All data cleared.")


# ---------------------------------------------------------------------------
# Standalone: populate DB from characters/ directory
# ---------------------------------------------------------------------------

def populate_from_directory(directory: Path = CHARACTERS_DIR) -> list[dict]:
    """Load all character JSONs, create accounts, return summary."""
    results = []
    for path in sorted(directory.glob("*.json")):
        try:
            state = load_character_file(path)
        except Exception as e:
            log.error("Failed to load %s: %s", path.name, e)
            print(f"  FAIL  {path.name}: {e}")
            continue

        account, character, api_key = create_account_with_character(
            character_state=state,
            claimed=True,
        )
        results.append({
            "file": path.name,
            "name": state.sheet.name,
            "entity_id": state.sheet.entity_id,
            "api_key": api_key,
            "account_id": account.id,
        })
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    db.create_tables()

    print("Clearing existing data...")
    clear_all_data()

    print(f"Loading characters from {CHARACTERS_DIR.resolve()}/\n")
    results = populate_from_directory()

    if not results:
        print("No characters loaded.")
    else:
        print(f"{'Name':<20} {'Entity ID':<25} {'API Key'}")
        print("-" * 90)
        for r in results:
            print(f"{r['name']:<20} {r['entity_id']:<25} {r['api_key']}")
        print(f"\n{len(results)} account(s) created and ready.")
