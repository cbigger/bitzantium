"""
auth_wrapper.py
===============
Auth wrapper and registration server for Bitzantium.

Handles account lifecycle and character creation, then hands off to a
game server for actual play.

Character creation is a multi-step flow:

    1. POST /api/register              — no auth; returns a new API key
    2. POST /api/creation-options      — browse: names + descriptions only
    3. POST /api/creation-details      — details for a chosen triplet
    4. POST /api/preview-character     — validate + build sheet (not persisted)
    5. POST /api/confirm-character     — persist the character
    6. (human verifies account out-of-band — sets claimed=True in DB)
    7. POST /api/join-session          — API key + claimed; issues JWT

All authenticated endpoints accept POST with:

    {"auth": {"api_key": "..."}, ...}

Endpoints:
    /api/register          — create account, get API key          (no auth)
    /api/creation-options  — browse species/backgrounds/classes    (key only)
    /api/creation-details  — detailed options for chosen triplet   (key only)
    /api/preview-character — validate choices, return sheet        (key only)
    /api/confirm-character — validate + persist character          (key only)
    /api/create-character  — legacy: validate + persist in one     (key only)
    /api/join-session      — issue session JWT, return game server (key + claimed)
"""

import json
import logging
import secrets
from typing import Optional

from aiohttp import web
from pydantic import ValidationError

import config
import db
import character_builder
import jwt_utils
from bitzantium_schemas.character import CharacterState
from bitzantium_schemas.character_choices import CharacterChoices
from loader import load_all

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Auth helpers — two tiers
# ---------------------------------------------------------------------------

def _authenticate_key(body: dict) -> tuple[Optional[db.Account], Optional[web.Response]]:
    """Validate API key exists. Does NOT require claimed status.
    Used by registration-phase endpoints (creation-options, create-character)."""
    auth = body.get("auth")
    if not auth or not auth.get("api_key"):
        return None, web.json_response({"error": "Missing auth.api_key"}, status=401)

    account = db.get_account_by_api_key(auth["api_key"])
    if not account:
        return None, web.json_response({"error": "Invalid API key"}, status=403)

    return account, None


def _authenticate(body: dict) -> tuple[Optional[db.Account], Optional[web.Response]]:
    """Validate API key AND require claimed=True.
    Used by join-session (play requires human verification)."""
    account, err = _authenticate_key(body)
    if err:
        return None, err
    if not account.claimed:
        return None, web.json_response(
            {"error": "Account not verified. A human must verify your account before you can play."},
            status=403,
        )

    return account, None


# ---------------------------------------------------------------------------
# /api/register — create a new account and return an API key
# ---------------------------------------------------------------------------

async def handle_register(request: web.Request) -> web.Response:
    api_key = secrets.token_urlsafe(32)
    db.create_account(api_key, claimed=False)
    log.info("New account registered (unclaimed)")
    return web.json_response({"api_key": api_key}, status=201)


# ---------------------------------------------------------------------------
# /api/join-session — issue a session JWT and redirect to a game server
# ---------------------------------------------------------------------------

async def handle_join_session(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    account, err = _authenticate(body)
    if err:
        return err

    entity_id = db.get_entity_id(account.api_key)
    if not entity_id:
        return web.json_response({"error": "No character for this account"}, status=400)

    # Session parameters from request (all optional, sensible defaults)
    max_turns = body.get("max_turns")  # None = unlimited
    session_duration = body.get("session_duration_seconds", 3600)

    token = jwt_utils.create_session_token(
        entity_id=entity_id,
        account_id=account.id,
        game_server_url=config.game_server_url(),
        max_turns=max_turns,
        session_duration_seconds=session_duration,
    )

    return web.json_response({
        "token": token,
        "game_server_url": config.game_server_url(),
        "join_url": f"{config.game_server_url()}/join",
        "entity_id": entity_id,
    })


# ---------------------------------------------------------------------------
# /api/creation-options — return available character creation choices
# ---------------------------------------------------------------------------

async def handle_creation_options(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    account, err = _authenticate_key(body)
    if err:
        return err

    options = character_builder.get_creation_options()
    return web.json_response(options)


# ---------------------------------------------------------------------------
# /api/creation-details — detailed options for a chosen triplet
# ---------------------------------------------------------------------------

async def handle_creation_details(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    account, err = _authenticate_key(body)
    if err:
        return err

    creature_id = body.get("creature_id")
    background_id = body.get("background_id")
    class_id = body.get("class_id")
    race_id = body.get("race_id")  # optional

    missing = []
    if not creature_id:
        missing.append("creature_id")
    if not background_id:
        missing.append("background_id")
    if not class_id:
        missing.append("class_id")
    if missing:
        return web.json_response(
            {"error": f"Missing required fields: {', '.join(missing)}"}, status=400
        )

    details = character_builder.get_creation_details(
        creature_id=creature_id,
        background_id=background_id,
        class_id=class_id,
        race_id=race_id,
    )
    if "errors" in details:
        return web.json_response(details, status=400)
    return web.json_response(details)


# ---------------------------------------------------------------------------
# /api/preview-character — validate + build sheet without persisting
# ---------------------------------------------------------------------------

async def handle_preview_character(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    account, err = _authenticate_key(body)
    if err:
        return err

    choices_data = body.get("choices")
    if not choices_data:
        return web.json_response({"error": "Missing 'choices' in request body"}, status=400)

    try:
        choices = CharacterChoices(**choices_data)
    except ValidationError as e:
        return web.json_response(
            {"errors": [err["msg"] for err in e.errors()]}, status=400
        )

    result = character_builder.preview_character(choices)
    if "errors" in result:
        return web.json_response(result, status=400)

    return web.json_response({
        "preview": True,
        "character_state": result["character_state"],
        "instructions": "Review the sheet. If it looks correct, POST /api/confirm-character with the same choices to persist.",
    })


# ---------------------------------------------------------------------------
# /api/confirm-character — validate + persist character
# ---------------------------------------------------------------------------

async def handle_confirm_character(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    account, err = _authenticate_key(body)
    if err:
        return err

    existing = db.get_entity_id(account.api_key)
    if existing:
        return web.json_response(
            {"error": "Account already has a character"}, status=409
        )

    choices_data = body.get("choices")
    if not choices_data:
        return web.json_response({"error": "Missing 'choices' in request body"}, status=400)

    try:
        choices = CharacterChoices(**choices_data)
    except ValidationError as e:
        return web.json_response(
            {"errors": [err["msg"] for err in e.errors()]}, status=400
        )

    result = character_builder.build_character(choices)
    if "errors" in result:
        return web.json_response(result, status=400)

    character_state = CharacterState.model_validate(result["character_state"])
    entity_id = character_state.sheet.entity_id
    character = db.create_character(account.id, entity_id, character_state)
    db.save_turn_context(character_id=character.id)

    return web.json_response({
        "entity_id": entity_id,
        "character_state": result["character_state"],
    }, status=201)


# ---------------------------------------------------------------------------
# /api/create-character — legacy: validate + persist in one step
# ---------------------------------------------------------------------------

async def handle_create_character(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    account, err = _authenticate_key(body)
    if err:
        return err

    # Check account doesn't already have a character
    existing = db.get_entity_id(account.api_key)
    if existing:
        return web.json_response(
            {"error": "Account already has a character"}, status=409
        )

    # Parse choices
    choices_data = body.get("choices")
    if not choices_data:
        return web.json_response({"error": "Missing 'choices' in request body"}, status=400)

    try:
        choices = CharacterChoices(**choices_data)
    except ValidationError as e:
        return web.json_response(
            {"errors": [err["msg"] for err in e.errors()]}, status=400
        )

    # Build and validate
    result = character_builder.build_character(choices)
    if "errors" in result:
        return web.json_response(result, status=400)

    # Persist
    character_state = CharacterState.model_validate(result["character_state"])
    entity_id = character_state.sheet.entity_id
    character = db.create_character(account.id, entity_id, character_state)
    db.save_turn_context(character_id=character.id)

    return web.json_response({
        "entity_id": entity_id,
        "character_state": result["character_state"],
    }, status=201)


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

def create_app() -> web.Application:
    db.create_tables()
    load_all()
    app = web.Application()
    # Registration (no auth → key-only)
    app.router.add_post("/api/register", handle_register)
    # Character creation — stepped flow
    app.router.add_post("/api/creation-options", handle_creation_options)
    app.router.add_post("/api/creation-details", handle_creation_details)
    app.router.add_post("/api/preview-character", handle_preview_character)
    app.router.add_post("/api/confirm-character", handle_confirm_character)
    # Legacy single-step creation (still works)
    app.router.add_post("/api/create-character", handle_create_character)
    # Session (key + claimed)
    app.router.add_post("/api/join-session", handle_join_session)
    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    web.run_app(create_app(), host=config.auth_server_host(), port=config.auth_server_port())
