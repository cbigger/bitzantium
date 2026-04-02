"""
db.py
=====
Database layer for Bitzantium — SQLAlchemy ORM with JSON blob storage.

Tables:
    accounts           — API key + claim status.
    characters         — 1:1 with account. Full CharacterState as JSON.
    turn_contexts      — 1:1 with character. Prompt-building context per turn.
    realm_objects      — Realm reference data (classes, races, items, etc.)
    scene_states       — Active scene (positions, area, light). Single row.
    turn_states        — Turn order engine state. Single row.
    narrative_segments — Per-turn narrative segments. Each has a shared (third-person)
                         and personal (second-person) version, tagged by acting entity.
    dm_chat_history    — DM's persistent LLM conversation (one row per message).

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
import dice
from bitzantium_schemas.character import CharacterState, ActionEconomy
from bitzantium_schemas.schemas import Position, LightLevel

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


class SceneStateRow(Base):
    __tablename__ = "scene_states"

    id = Column(Integer, primary_key=True)
    area_id = Column(String, nullable=False, default="liminal")
    area_name = Column(String, nullable=False, default="Liminal Space")
    area_description = Column(Text, default="")
    light_level = Column(String, nullable=False, default="bright")
    entity_positions = Column(JSONB, nullable=False, default=dict)


class TurnStateRow(Base):
    __tablename__ = "turn_states"

    id = Column(Integer, primary_key=True)
    tick = Column(Integer, nullable=False, default=0)
    turn_order = Column(JSONB, nullable=False, default=list)
    turn_index = Column(Integer, nullable=False, default=0)
    initiative_rolls = Column(JSONB, nullable=False, default=dict)
    dm_turn_pending = Column(Boolean, nullable=False, default=False)
    player_turn_entity_id = Column(String, nullable=True)


class NarrativeSegment(Base):
    __tablename__ = "narrative_segments"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    acting_entity_id = Column(String, nullable=False)
    shared_text      = Column(Text, nullable=False)   # third-person, shown to all other players
    personal_text    = Column(Text, nullable=False)   # second-person, shown to the acting player
    created_at       = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class DmChatMessage(Base):
    __tablename__ = "dm_chat_history"

    id = Column(Integer, primary_key=True)
    role = Column(String, nullable=False)
    content = Column(JSONB, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class NarratorMessage(Base):
    __tablename__ = "narrator_history"

    id = Column(Integer, primary_key=True)
    role = Column(String, nullable=False)
    content = Column(JSONB, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


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


def get_all_entity_ids() -> list[str]:
    """Return entity_ids for every character in the database."""
    with SessionLocal() as session:
        rows = session.query(Character.entity_id).all()
        return [r[0] for r in rows]


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
# Resting
# ---------------------------------------------------------------------------

def short_rest(entity_id: str, hit_dice_to_spend: int = 0) -> Optional[dict]:
    """Short rest: spend hit dice to recover HP, recharge short-rest resources,
    and reset action economy.  Returns a summary dict or None if entity missing."""
    with SessionLocal() as session:
        character = session.query(Character).filter(Character.entity_id == entity_id).first()
        if not character:
            return None
        cs = CharacterState.model_validate(character.character_state)
        sheet = cs.sheet

        # Spend hit dice to recover HP
        hp_healed = 0
        dice_spent = 0
        con_mod = (sheet.ability_scores.constitution - 10) // 2
        remaining_to_spend = hit_dice_to_spend
        for hd in sheet.hit_dice:
            while remaining_to_spend > 0 and hd.remaining > 0:
                hd.remaining -= 1
                hp_healed += max(1, dice.roll(f"1{hd.die}") + con_mod)
                dice_spent += 1
                remaining_to_spend -= 1

        sheet.hp_current = min(sheet.hp_max, sheet.hp_current + hp_healed)

        # Recharge short-rest class resources
        resources_recharged = []
        for res in sheet.class_resources:
            if res.recharge_on in ("short_rest", "short rest") and res.current < res.max:
                res.current = res.max
                resources_recharged.append(res.name)

        # Reset economy
        cs = cs.model_copy(update={"economy": ActionEconomy()})
        character.character_state = cs.model_dump(mode="json")
        session.commit()

        return {
            "entity_id": entity_id,
            "rest_type": "short",
            "hit_dice_spent": dice_spent,
            "hp_healed": hp_healed,
            "hp_current": sheet.hp_current,
            "hp_max": sheet.hp_max,
            "resources_recharged": resources_recharged,
        }


def long_rest(entity_id: str, full_reset: bool = False) -> Optional[dict]:
    """Long rest: full HP, restore half hit dice (min 1), all spell slots,
    all class resources, clear conditions/effects/death saves, reset economy.

    If full_reset is True, all hit dice are fully restored and exhaustion is
    zeroed out instead of reduced by 1 (useful for story resets).

    Returns a summary dict or None if entity missing."""
    with SessionLocal() as session:
        character = session.query(Character).filter(Character.entity_id == entity_id).first()
        if not character:
            return None
        cs = CharacterState.model_validate(character.character_state)
        sheet = cs.sheet

        # Full HP
        sheet.hp_current = sheet.hp_max
        sheet.hp_temp = 0

        # Hit dice recovery
        for hd in sheet.hit_dice:
            if full_reset:
                hd.remaining = hd.total
            else:
                recover = max(1, hd.total // 2)
                hd.remaining = min(hd.total, hd.remaining + recover)

        # All spell slots
        for slot in sheet.spell_slots.values():
            slot.remaining = slot.total

        # All class resources
        for res in sheet.class_resources:
            res.current = res.max

        # Clear conditions, effects, death saves, exhaustion
        from bitzantium_schemas.character import DeathSaves
        sheet.death_saves = DeathSaves()
        sheet.conditions = []
        sheet.active_effects = []
        sheet.concentration_effect_id = None
        if full_reset:
            sheet.exhaustion_level = 0
        else:
            sheet.exhaustion_level = max(0, sheet.exhaustion_level - 1)

        # Reset economy
        cs = cs.model_copy(update={"economy": ActionEconomy()})
        character.character_state = cs.model_dump(mode="json")
        session.commit()

        return {
            "entity_id": entity_id,
            "rest_type": "long",
            "full_reset": full_reset,
            "hp_current": sheet.hp_current,
            "hp_max": sheet.hp_max,
        }


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


def update_player_location(
    entity_id: str,
    location_area: str,
    location_sub: Optional[str] = None,
) -> bool:
    """Update just the location fields on a player's turn context.
    Returns True if found and updated, False if character not found."""
    with SessionLocal() as session:
        character = session.query(Character).filter(Character.entity_id == entity_id).first()
        if not character or not character.turn_context:
            return False
        character.turn_context.location_area = location_area
        character.turn_context.location_sub = location_sub
        session.commit()
        return True


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


# ---------------------------------------------------------------------------
# Scene state — replaces scene_state.py in-memory store
# ---------------------------------------------------------------------------

_SCENE_ROW_ID = 1  # single-row pattern


def _get_or_create_scene(session: Session) -> SceneStateRow:
    """Get the singleton scene row, creating it if it doesn't exist."""
    row = session.query(SceneStateRow).filter(SceneStateRow.id == _SCENE_ROW_ID).first()
    if row is None:
        row = SceneStateRow(id=_SCENE_ROW_ID, entity_positions={})
        session.add(row)
        session.flush()
    return row


def get_scene() -> dict:
    """Return the current scene state as a dict."""
    with SessionLocal() as session:
        row = _get_or_create_scene(session)
        return {
            "area_id": row.area_id,
            "area_name": row.area_name,
            "area_description": row.area_description,
            "light_level": row.light_level,
            "entity_positions": row.entity_positions or {},
        }


def init_scene(
    area_id: str = "liminal",
    area_name: str = "Liminal Space",
    area_description: str = "",
    light_level: str = "bright",
) -> dict:
    """Initialise or reset the current scene. Also clears the scene narrative."""
    with SessionLocal() as session:
        row = _get_or_create_scene(session)
        row.area_id = area_id
        row.area_name = area_name
        row.area_description = area_description
        row.light_level = light_level
        row.entity_positions = {}
        # Clear the narrative — new scene, fresh story
        narr = _get_or_create_narrative(session)
        narr.narrative = ""
        session.commit()
        return {
            "area_id": row.area_id,
            "area_name": row.area_name,
            "area_description": row.area_description,
            "light_level": row.light_level,
            "entity_positions": {},
        }


def place_entity(entity_id: str, x: int, y: int, z: int = 0) -> dict:
    """Place an entity at a grid position. Returns the updated scene."""
    with SessionLocal() as session:
        row = _get_or_create_scene(session)
        positions = dict(row.entity_positions or {})
        positions[entity_id] = {"x": x, "y": y, "z": z, "area_id": row.area_id}
        row.entity_positions = positions
        session.commit()
        return {
            "area_id": row.area_id,
            "area_name": row.area_name,
            "area_description": row.area_description,
            "light_level": row.light_level,
            "entity_positions": positions,
        }


def remove_entity_from_scene(entity_id: str) -> dict:
    """Remove an entity from the scene. Returns the updated scene."""
    with SessionLocal() as session:
        row = _get_or_create_scene(session)
        positions = {k: v for k, v in (row.entity_positions or {}).items() if k != entity_id}
        row.entity_positions = positions
        session.commit()
        return {
            "area_id": row.area_id,
            "area_name": row.area_name,
            "area_description": row.area_description,
            "light_level": row.light_level,
            "entity_positions": positions,
        }


def distance_between(entity_a: str, entity_b: str) -> Optional[float]:
    """Euclidean distance in feet (1 grid unit = 5 ft)."""
    with SessionLocal() as session:
        row = _get_or_create_scene(session)
        positions = row.entity_positions or {}
        pa = positions.get(entity_a)
        pb = positions.get(entity_b)
    if pa is None or pb is None:
        return None
    grid_dist = ((pa["x"] - pb["x"]) ** 2 + (pa["y"] - pb["y"]) ** 2 + (pa["z"] - pb["z"]) ** 2) ** 0.5
    return grid_dist * 5


# ---------------------------------------------------------------------------
# Scene narrative — shared story document
# ---------------------------------------------------------------------------

def append_narrative_segment(acting_entity_id: str, shared_text: str, personal_text: str) -> dict:
    """Append a narrative segment for one DM turn.

    shared_text   — third-person prose shown to all players except the acting one.
    personal_text — second-person prose shown only to the acting player.

    Returns a dict with the new segment's id.
    """
    with SessionLocal() as session:
        seg = NarrativeSegment(
            acting_entity_id=acting_entity_id,
            shared_text=shared_text,
            personal_text=personal_text,
        )
        session.add(seg)
        session.commit()
        return {"id": seg.id}


def build_player_narrative(entity_id: str) -> str:
    """Assemble the narrative for a specific player.

    For each segment:
      - acting_entity_id == entity_id → use personal_text (second-person)
      - otherwise                     → use shared_text   (third-person)

    Returns the full assembled narrative string, or "" if no segments exist.
    """
    with SessionLocal() as session:
        segments = (
            session.query(NarrativeSegment)
            .order_by(NarrativeSegment.id)
            .all()
        )
        parts: list[str] = []
        for seg in segments:
            if seg.acting_entity_id == entity_id:
                parts.append(seg.personal_text)
            else:
                parts.append(seg.shared_text)
        return "\n\n".join(parts)


def get_last_narrative_segment() -> dict | None:
    """Return the most recently appended narrative segment as a dict, or None."""
    with SessionLocal() as session:
        seg = (
            session.query(NarrativeSegment)
            .order_by(NarrativeSegment.id.desc())
            .first()
        )
        if seg is None:
            return None
        return {
            "id": seg.id,
            "acting_entity_id": seg.acting_entity_id,
            "shared_text": seg.shared_text,
            "personal_text": seg.personal_text,
        }


def clear_narrative_segments() -> None:
    """Delete all narrative segments."""
    with SessionLocal() as session:
        session.query(NarrativeSegment).delete()
        session.commit()


# ---------------------------------------------------------------------------
# Turn state — replaces turn_state.py in-memory store
# ---------------------------------------------------------------------------

_TURN_ROW_ID = 1  # single-row pattern


def _get_or_create_turn(session: Session) -> TurnStateRow:
    """Get the singleton turn row, creating it if it doesn't exist."""
    row = session.query(TurnStateRow).filter(TurnStateRow.id == _TURN_ROW_ID).first()
    if row is None:
        row = TurnStateRow(id=_TURN_ROW_ID, turn_order=[], initiative_rolls={})
        session.add(row)
        session.flush()
    return row


def get_turn() -> dict:
    """Return the current turn state as a dict."""
    with SessionLocal() as session:
        row = _get_or_create_turn(session)
        order = row.turn_order or []
        idx = row.turn_index
        current = order[idx % len(order)] if order else None
        return {
            "tick": row.tick,
            "turn_order": order,
            "turn_index": idx,
            "current_entity": current,
            "initiative_rolls": row.initiative_rolls or {},
            "dm_turn_pending": row.dm_turn_pending,
            "player_turn_entity_id": row.player_turn_entity_id,
        }


def roll_initiative(entity_ids: list[str]) -> dict:
    """Roll initiative for the given entities and set the turn order.
    Resets action economy for whoever goes first."""
    rolls: dict[str, int] = {}
    for eid in entity_ids:
        cs = get_character_state_by_entity(eid)
        if cs is None:
            raise ValueError(f"Entity {eid!r} not found in database.")
        dex_mod = (cs.sheet.ability_scores.dexterity - 10) // 2
        rolls[eid] = dice.roll_d20()["roll"] + dex_mod

    order = sorted(rolls.keys(), key=lambda e: rolls[e], reverse=True)

    with SessionLocal() as session:
        row = _get_or_create_turn(session)
        row.turn_order = order
        row.turn_index = 0
        row.initiative_rolls = rolls
        session.commit()

    if order:
        reset_economy(order[0])

    return get_turn()


def set_turn_order(entity_ids: list[str]) -> dict:
    """Manually assign turn order without rolling."""
    with SessionLocal() as session:
        row = _get_or_create_turn(session)
        row.turn_order = list(entity_ids)
        row.turn_index = 0
        row.initiative_rolls = {}
        session.commit()

    if entity_ids:
        reset_economy(entity_ids[0])

    return get_turn()


def advance_turn() -> tuple[str, dict]:
    """Advance to the next entity. Increments tick when the order wraps.
    Resets the incoming entity's action economy.
    Returns (next_entity_id, turn_state_dict)."""
    with SessionLocal() as session:
        row = _get_or_create_turn(session)
        order = row.turn_order or []
        if not order:
            raise RuntimeError("Turn order is empty — call roll_initiative or set_turn_order first.")

        next_idx = (row.turn_index + 1) % len(order)
        new_tick = row.tick + (1 if next_idx == 0 else 0)
        row.turn_index = next_idx
        row.tick = new_tick
        session.commit()

        next_entity = order[next_idx]

    reset_economy(next_entity)
    return next_entity, get_turn()


def add_to_order(entity_id: str, after_index: Optional[int] = None) -> dict:
    """Insert an entity into the turn order."""
    with SessionLocal() as session:
        row = _get_or_create_turn(session)
        order = list(row.turn_order or [])
        if entity_id in order:
            session.commit()
            return get_turn()
        if after_index is None:
            order.append(entity_id)
        else:
            order.insert(after_index + 1, entity_id)
        row.turn_order = order
        session.commit()
    return get_turn()


def remove_from_order(entity_id: str) -> dict:
    """Remove an entity from the turn order. Adjusts turn_index if needed."""
    with SessionLocal() as session:
        row = _get_or_create_turn(session)
        order = list(row.turn_order or [])
        if entity_id not in order:
            session.commit()
            return get_turn()

        removed_idx = order.index(entity_id)
        order.remove(entity_id)

        new_idx = row.turn_index
        if removed_idx < row.turn_index:
            new_idx = max(0, new_idx - 1)
        if order:
            new_idx = new_idx % len(order)
        else:
            new_idx = 0

        row.turn_order = order
        row.turn_index = new_idx
        session.commit()
    return get_turn()


def set_dm_turn_pending(pending: bool) -> None:
    """Set the dm_turn_pending flag."""
    with SessionLocal() as session:
        row = _get_or_create_turn(session)
        row.dm_turn_pending = pending
        session.commit()


def is_dm_turn_pending() -> bool:
    """Check if the DM has a pending turn."""
    with SessionLocal() as session:
        row = _get_or_create_turn(session)
        return row.dm_turn_pending


def set_player_turn(entity_id: str | None) -> None:
    """Set which player's turn it is. None means no player's turn (DM working)."""
    with SessionLocal() as session:
        row = _get_or_create_turn(session)
        row.player_turn_entity_id = entity_id
        session.commit()


def get_player_turn() -> str | None:
    """Return the entity_id of the player whose turn it is, or None."""
    with SessionLocal() as session:
        row = _get_or_create_turn(session)
        return row.player_turn_entity_id


# ---------------------------------------------------------------------------
# DM chat history — persistent LLM conversation
# ---------------------------------------------------------------------------

def append_dm_message(role: str, content) -> None:
    """Append a message to the DM chat history."""
    with SessionLocal() as session:
        msg = DmChatMessage(role=role, content=content)
        session.add(msg)
        session.commit()


def get_dm_chat_history() -> list[dict]:
    """Return the full DM chat history in order."""
    with SessionLocal() as session:
        rows = session.query(DmChatMessage).order_by(DmChatMessage.id).all()
        return [{"role": r.role, "content": r.content} for r in rows]


def clear_dm_chat_history() -> int:
    """Clear all DM chat history. Returns count deleted."""
    with SessionLocal() as session:
        count = session.query(DmChatMessage).delete()
        session.commit()
        return count


# ---------------------------------------------------------------------------
# Narrator history — persistent narrator conversation (separate from DM)
# ---------------------------------------------------------------------------

def append_narrator_message(role: str, content) -> None:
    """Append a message to the narrator history."""
    with SessionLocal() as session:
        msg = NarratorMessage(role=role, content=content)
        session.add(msg)
        session.commit()


def get_narrator_history() -> list[dict]:
    """Return the full narrator history in order."""
    with SessionLocal() as session:
        rows = session.query(NarratorMessage).order_by(NarratorMessage.id).all()
        return [{"role": r.role, "content": r.content} for r in rows]
