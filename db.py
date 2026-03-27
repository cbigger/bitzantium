"""
db.py
=====
Database layer for Bitzantium — SQLAlchemy ORM with JSON blob storage.

Tables:
    accounts        — API key + claim status.
    characters      — 1:1 with account. Full CharacterState as JSON.
    turn_contexts   — 1:1 with character. Prompt-building context per turn.
    realm_objects   — Realm reference data (classes, races, items, etc.)

All state is serialized/deserialized via Pydantic's model_dump / model_validate,
so swapping the DB backend later only requires changing the connection string.

Connection string is read from bitzantium.toml via config.py.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import (
    DeclarativeBase,
    Session,
    relationship,
    sessionmaker,
)

import config
from bitzantium_schemas.character import CharacterState, ActionEconomy

# ---------------------------------------------------------------------------
# Engine / session
# ---------------------------------------------------------------------------

engine = create_engine(config.database_url())
SessionLocal = sessionmaker(bind=engine)


# ---------------------------------------------------------------------------
# ORM models
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True)
    api_key = Column(String, unique=True, nullable=False, index=True)
    claimed = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    character = relationship("Character", back_populates="account", uselist=False)


class Character(Base):
    __tablename__ = "characters"

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), unique=True, nullable=False)
    entity_id = Column(String, unique=True, nullable=False, index=True)
    character_state = Column(JSONB, nullable=False)
    alive = Column(Boolean, default=True, nullable=False)
    departure_action = Column(Text, nullable=True)  # what the character was doing when they signed off

    account = relationship("Account", back_populates="character")
    turn_context = relationship("TurnContext", back_populates="character", uselist=False)


class TurnContext(Base):
    __tablename__ = "turn_contexts"

    id = Column(Integer, primary_key=True)
    character_id = Column(Integer, ForeignKey("characters.id"), unique=True, nullable=False)
    story_so_far = Column(Text, default="")
    location_area = Column(String, default="")
    location_sub = Column(String, nullable=True)
    quest_log = Column(Text, nullable=True)

    character = relationship("Character", back_populates="turn_context")


class RealmObject(Base):
    __tablename__ = "realm_objects"
    __table_args__ = (
        UniqueConstraint("category", "data_id", name="uq_realm_category_data_id"),
    )

    id = Column(Integer, primary_key=True)
    category = Column(String, nullable=False, index=True)
    data_id = Column(String, nullable=False, index=True)
    data = Column(JSONB, nullable=False)
    is_sapient = Column(Boolean, default=False, nullable=False)


# ---------------------------------------------------------------------------
# Table creation
# ---------------------------------------------------------------------------

def create_tables() -> None:
    """Create all tables if they don't exist."""
    Base.metadata.create_all(engine)


# ---------------------------------------------------------------------------
# Account CRUD
# ---------------------------------------------------------------------------

def get_account_by_api_key(api_key: str) -> Optional[Account]:
    with SessionLocal() as session:
        return session.query(Account).filter(Account.api_key == api_key).first()


def create_account(api_key: str, claimed: bool = False) -> Account:
    with SessionLocal() as session:
        account = Account(api_key=api_key, claimed=claimed)
        session.add(account)
        session.commit()
        session.refresh(account)
        return account


def claim_account(api_key: str) -> Optional[Account]:
    with SessionLocal() as session:
        account = session.query(Account).filter(Account.api_key == api_key).first()
        if not account:
            return None
        account.claimed = True
        session.commit()
        session.refresh(account)
        return account


# ---------------------------------------------------------------------------
# Character CRUD
# ---------------------------------------------------------------------------

def get_character_state(api_key: str) -> Optional[CharacterState]:
    """Look up API key -> account -> character -> deserialize CharacterState."""
    with SessionLocal() as session:
        account = session.query(Account).filter(Account.api_key == api_key).first()
        if not account or not account.character:
            return None
        return CharacterState.model_validate(account.character.character_state)


def get_entity_id(api_key: str) -> Optional[str]:
    """Look up API key -> account -> character -> entity_id."""
    with SessionLocal() as session:
        account = session.query(Account).filter(Account.api_key == api_key).first()
        if not account or not account.character:
            return None
        return account.character.entity_id


def get_character_state_by_entity(entity_id: str) -> Optional[CharacterState]:
    """Look up character state directly by entity_id."""
    with SessionLocal() as session:
        character = session.query(Character).filter(Character.entity_id == entity_id).first()
        if not character:
            return None
        return CharacterState.model_validate(character.character_state)


def save_character_state(entity_id: str, state: CharacterState) -> None:
    """Full CharacterState replacement in the DB."""
    with SessionLocal() as session:
        character = session.query(Character).filter(Character.entity_id == entity_id).first()
        if not character:
            return
        character.character_state = state.model_dump(mode="json")
        session.commit()


def create_character(account_id: int, entity_id: str, state: CharacterState) -> Character:
    with SessionLocal() as session:
        character = Character(
            account_id=account_id,
            entity_id=entity_id,
            character_state=state.model_dump(mode="json"),
        )
        session.add(character)
        session.commit()
        session.refresh(character)
        return character


# ---------------------------------------------------------------------------
# Economy — read/modify/write the economy inside CharacterState JSONB
# ---------------------------------------------------------------------------

def spend_economy(entity_id: str, cost: str) -> Optional[CharacterState]:
    """Mark an action economy flag in the DB. Returns updated state or None.

    cost: 'action' | 'bonus_action' | 'reaction' | 'free'
    No-op for 'free'.
    """
    field_map = {
        "action":       "action_spent",
        "bonus_action": "bonus_action_spent",
        "reaction":     "reaction_spent",
    }
    field = field_map.get(cost)
    if field is None:
        return get_character_state_by_entity(entity_id)

    with SessionLocal() as session:
        character = session.query(Character).filter(Character.entity_id == entity_id).first()
        if not character:
            return None
        cs = CharacterState.model_validate(character.character_state)
        new_economy = cs.economy.model_copy(update={field: True})
        cs = cs.model_copy(update={"economy": new_economy})
        character.character_state = cs.model_dump(mode="json")
        session.commit()
        return cs


def reset_economy(entity_id: str) -> Optional[CharacterState]:
    """Reset action economy to fresh turn state in the DB."""
    with SessionLocal() as session:
        character = session.query(Character).filter(Character.entity_id == entity_id).first()
        if not character:
            return None
        cs = CharacterState.model_validate(character.character_state)
        cs = cs.model_copy(update={"economy": ActionEconomy()})
        character.character_state = cs.model_dump(mode="json")
        session.commit()
        return cs


# ---------------------------------------------------------------------------
# Signoff
# ---------------------------------------------------------------------------

def save_departure_action(entity_id: str, departure_action: str) -> None:
    """Record what the character was doing when the player signed off."""
    with SessionLocal() as session:
        character = session.query(Character).filter(Character.entity_id == entity_id).first()
        if not character:
            return
        character.departure_action = departure_action
        session.commit()


def get_departure_action(entity_id: str) -> Optional[str]:
    """Get the last departure action for a character."""
    with SessionLocal() as session:
        character = session.query(Character).filter(Character.entity_id == entity_id).first()
        if not character:
            return None
        return character.departure_action


# ---------------------------------------------------------------------------
# Turn context CRUD
# ---------------------------------------------------------------------------

def get_turn_context_by_entity(entity_id: str) -> Optional[dict]:
    """Look up turn context directly by entity_id."""
    with SessionLocal() as session:
        character = session.query(Character).filter(Character.entity_id == entity_id).first()
        if not character or not character.turn_context:
            return None
        tc = character.turn_context
        return {
            "story_so_far": tc.story_so_far or "",
            "location_area": tc.location_area or "",
            "location_sub": tc.location_sub,
            "quest_log": tc.quest_log,
        }


def get_turn_context(api_key: str) -> Optional[dict]:
    """Look up API key -> account -> character -> turn context as a dict.

    Returns dict with keys: story_so_far, location_area, location_sub, quest_log.
    Matches the turn context fields used by player_mcp.build_player_prompt().
    """
    with SessionLocal() as session:
        account = session.query(Account).filter(Account.api_key == api_key).first()
        if not account or not account.character or not account.character.turn_context:
            return None
        tc = account.character.turn_context
        return {
            "story_so_far": tc.story_so_far or "",
            "location_area": tc.location_area or "",
            "location_sub": tc.location_sub,
            "quest_log": tc.quest_log,
        }


def save_turn_context(
    character_id: int,
    story_so_far: str = "",
    location_area: str = "",
    location_sub: Optional[str] = None,
    quest_log: Optional[str] = None,
) -> TurnContext:
    """Upsert turn context for a character."""
    with SessionLocal() as session:
        tc = session.query(TurnContext).filter(TurnContext.character_id == character_id).first()
        if tc:
            tc.story_so_far = story_so_far
            tc.location_area = location_area
            tc.location_sub = location_sub
            tc.quest_log = quest_log
        else:
            tc = TurnContext(
                character_id=character_id,
                story_so_far=story_so_far,
                location_area=location_area,
                location_sub=location_sub,
                quest_log=quest_log,
            )
            session.add(tc)
        session.commit()
        session.refresh(tc)
        return tc


# ---------------------------------------------------------------------------
# Realm objects — reference data (classes, races, items, etc.)
# ---------------------------------------------------------------------------

def clear_realm_objects() -> int:
    """Delete all realm objects. Returns count deleted."""
    with SessionLocal() as session:
        count = session.query(RealmObject).delete()
        session.commit()
        return count


def insert_realm_object(
    category: str,
    data_id: str,
    data: dict,
    is_sapient: bool = False,
) -> RealmObject:
    """Insert a single realm object."""
    with SessionLocal() as session:
        obj = RealmObject(
            category=category,
            data_id=data_id,
            data=data,
            is_sapient=is_sapient,
        )
        session.add(obj)
        session.commit()
        session.refresh(obj)
        return obj


def bulk_insert_realm_objects(rows: list[dict]) -> int:
    """Bulk insert realm objects. Each dict has: category, data_id, data, is_sapient.
    Returns count inserted."""
    with SessionLocal() as session:
        session.bulk_insert_mappings(RealmObject, rows)
        session.commit()
        return len(rows)


def get_realm_objects_by_category(category: str) -> list[dict]:
    """Return all realm objects for a category as (data_id, data, is_sapient) dicts."""
    with SessionLocal() as session:
        rows = session.query(RealmObject).filter(RealmObject.category == category).all()
        return [
            {"data_id": r.data_id, "data": r.data, "is_sapient": r.is_sapient}
            for r in rows
        ]


def get_all_realm_objects() -> list[dict]:
    """Return all realm objects as (category, data_id, data, is_sapient) dicts."""
    with SessionLocal() as session:
        rows = session.query(RealmObject).all()
        return [
            {"category": r.category, "data_id": r.data_id, "data": r.data, "is_sapient": r.is_sapient}
            for r in rows
        ]
