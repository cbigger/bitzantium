"""
auth_wrapper.py
===============
Auth wrapper and registration server for Bitzantium.

Registration flow (agent-facing):
    1. POST /api/register              — no auth; returns a new API key
    2. POST /api/creation-options      — API key required; returns menu of choices
    3. POST /api/create-character      — API key required; validates + persists character
    4. (human verifies account out-of-band — sets claimed=True in DB)
    5. POST /api/tool, /state, /context — API key required AND account must be claimed

All authenticated endpoints accept POST with:

    {"auth": {"api_key": "..."}, ...}

Endpoints:
    /api/register          — create account, get API key          (no auth)
    /api/creation-options  — available character creation choices  (key only)
    /api/create-character  — validate choices, create character    (key only)
    /api/tool              — execute a player tool call            (key + claimed)
    /api/state             — return the player's full state        (key + claimed)
    /api/context           — return the player's turn context      (key + claimed)
"""

import json
import logging
import secrets
from pathlib import Path
from typing import Any, Optional

from aiohttp import web
from pydantic import ValidationError

import db
import player_tools
import state as state_module
import character_builder
from bitzantium_schemas.character import CharacterState
from bitzantium_schemas.character_choices import CharacterChoices
from loader import load_all

log = logging.getLogger(__name__)

REALM_PATH = Path(__file__).resolve().parent / "Realms" / "dnd"


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
    Used by play-phase endpoints (tool, state, context)."""
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
# /api/tool — execute a player tool call
# ---------------------------------------------------------------------------

async def handle_tool_call(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    account, err = _authenticate(body)
    if err:
        return err

    api_key = account.api_key

    # --- Resolve entity ---
    entity_id = db.get_entity_id(api_key)
    if not entity_id:
        return web.json_response({"error": "No character for this account"}, status=400)

    character_state = db.get_character_state(api_key)
    if not character_state:
        return web.json_response({"error": "Character state not found"}, status=400)
    state_module.update_character(entity_id, character_state)

    # --- Tool call ---
    tool_call = body.get("tool_call")
    if not tool_call or "name" not in tool_call:
        return web.json_response({"error": "Missing tool_call.name"}, status=400)

    name: str = tool_call["name"]
    arguments: dict[str, Any] = tool_call.get("arguments", {})

    result = player_tools.execute_player_tool(entity_id, name, arguments)

    # Persist updated state back to DB
    updated_state = state_module.get_character(entity_id)
    if updated_state:
        db.save_character_state(entity_id, updated_state)

    return web.json_response(result)


# ---------------------------------------------------------------------------
# /api/state — return the player's full CharacterState
# ---------------------------------------------------------------------------

async def handle_get_state(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    account, err = _authenticate(body)
    if err:
        return err

    character_state = db.get_character_state(account.api_key)
    if not character_state:
        return web.json_response({"error": "No character for this account"}, status=400)

    return web.json_response(character_state.model_dump(mode="json"))


# ---------------------------------------------------------------------------
# /api/context — return the player's turn context
# ---------------------------------------------------------------------------

async def handle_get_context(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    account, err = _authenticate(body)
    if err:
        return err

    context = db.get_turn_context(account.api_key)
    if not context:
        return web.json_response({"error": "No turn context found"}, status=400)

    return web.json_response(context)


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
# /api/create-character — validate choices and create a new character
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
    db.create_character(account.id, entity_id, character_state)

    return web.json_response({
        "entity_id": entity_id,
        "character_state": result["character_state"],
    }, status=201)


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

def create_app() -> web.Application:
    db.create_tables()
    load_all(str(REALM_PATH))
    app = web.Application()
    # Registration (no auth → key-only)
    app.router.add_post("/api/register", handle_register)
    app.router.add_post("/api/creation-options", handle_creation_options)
    app.router.add_post("/api/create-character", handle_create_character)
    # Play (key + claimed)
    app.router.add_post("/api/tool", handle_tool_call)
    app.router.add_post("/api/state", handle_get_state)
    app.router.add_post("/api/context", handle_get_context)
    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    web.run_app(create_app(), host="0.0.0.0", port=8080)
