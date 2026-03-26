"""
auth_wrapper.py
===============
Auth wrapper for the Bitzantium remote MCP endpoint.

Validates API keys against the database and serves player data and tool
execution to remote player MCP clients.

All endpoints accept POST with:

    {"auth": {"api_key": "..."}, ...}

Endpoints:
    /api/tool     — execute a player tool call
    /api/state    — return the player's full CharacterState
    /api/context  — return the player's turn context
"""

import json
import logging
from typing import Any, Optional

from aiohttp import web

import db
import player_tools
import state as state_module

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

def _authenticate(body: dict) -> tuple[Optional[db.Account], Optional[web.Response]]:
    """Validate the auth block. Returns (account, None) on success,
    or (None, error_response) on failure."""
    auth = body.get("auth")
    if not auth or not auth.get("api_key"):
        return None, web.json_response({"error": "Missing auth.api_key"}, status=401)

    account = db.get_account_by_api_key(auth["api_key"])
    if not account:
        return None, web.json_response({"error": "Invalid API key"}, status=403)
    if not account.claimed:
        return None, web.json_response({"error": "Account not claimed"}, status=403)

    return account, None


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
# App setup
# ---------------------------------------------------------------------------

def create_app() -> web.Application:
    db.create_tables()
    app = web.Application()
    app.router.add_post("/api/tool", handle_tool_call)
    app.router.add_post("/api/state", handle_get_state)
    app.router.add_post("/api/context", handle_get_context)
    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    web.run_app(create_app(), host="0.0.0.0", port=8080)
