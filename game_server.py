"""
game_server.py
==============
Game server (the "bitzantium server") — REST API with authentication.

Player agents connect via /api/play/* with their API key in the Authorization: Bearer header.
The DM agent connects via /api/dm/* with the pre-configured DM API key as Bearer.

Responsibilities:
    - POST /join:  accept API key, verify account + character, register active session
    - /api/play/*: Player endpoints (API key auth)
    - /api/dm/*:   DM endpoints (DM API key auth)
    - Intercept end_turn/signoff for session lifecycle
    - On player end_turn: append raw turn output to DM chat history, set dm_turn_pending
    - Track active players, turn counts, session limits

Run:
    python game_server.py
"""

import json
import logging
from typing import Any, Optional

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

import config
import db
import dm_tools
import loader
import player_mcp
import player_tools
import registry
import scene_loader

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Active session tracking
# ---------------------------------------------------------------------------

class PlayerSession:
    """Tracks an active player in this game server."""
    def __init__(self, entity_id: str, account_id: int, api_key: str):
        self.entity_id = entity_id
        self.account_id = account_id
        self.api_key = api_key
        self.turns_taken: int = 0
        self.active: bool = True
        self.turn_log: list[dict] = []  # raw tool calls + responses for the current turn


# api_key -> PlayerSession
_active_sessions: dict[str, PlayerSession] = {}


# ---------------------------------------------------------------------------
# Player auth helpers
# ---------------------------------------------------------------------------

def _extract_api_key(request: Request) -> Optional[str]:
    """Extract API key from Authorization: Bearer header."""
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return None


# ---------------------------------------------------------------------------
# Session lifecycle logic
# ---------------------------------------------------------------------------

def _do_end_turn(session: PlayerSession) -> dict:
    """End the current player's turn. Resets economy, appends raw turn
    output to DM chat history, sets dm_turn_pending."""
    session.turns_taken += 1

    db.reset_economy(session.entity_id)

    # Append raw player turn output to DM chat history
    if session.turn_log:
        db.append_dm_message("user", {
            "type": "player_turn",
            "entity_id": session.entity_id,
            "turn_number": session.turns_taken,
            "actions": session.turn_log,
        })
        session.turn_log = []

    # Signal DM that it has work to do, clear player turn
    db.set_dm_turn_pending(True)
    db.set_player_turn(None)

    return {
        "status": "turn_ended",
        "entity_id": session.entity_id,
        "turns_taken": session.turns_taken,
    }


def _do_signoff(session: PlayerSession, args: dict) -> dict:
    """Sign the player off from the session."""
    departure_action = args.get("departure_action", "")

    db.save_departure_action(session.entity_id, departure_action)

    session.active = False
    db.remove_from_order(session.entity_id)

    log.info("Player signed off: %s — %s", session.entity_id, departure_action)

    return {
        "status": "signed_off",
        "entity_id": session.entity_id,
        "departure_action": departure_action,
        "turns_taken": session.turns_taken,
    }


# ---------------------------------------------------------------------------
# /join — REST endpoint to register in the session
# ---------------------------------------------------------------------------

async def handle_join(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    # Accept API key from body or header
    api_key = _extract_api_key(request) or body.get("api_key")
    if not api_key:
        return JSONResponse(
            {"error": "Missing api_key in body or Authorization: Bearer header"},
            status_code=401,
        )

    account = db.get_account_by_api_key(api_key)
    if not account:
        return JSONResponse({"error": "Invalid API key"}, status_code=403)
    if not account.claimed:
        return JSONResponse(
            {"error": "Account not verified. A human must verify your account before you can play."},
            status_code=403,
        )

    entity_id = db.get_entity_id(api_key)
    if not entity_id:
        return JSONResponse({"error": "No character for this account"}, status_code=400)

    character = db.get_character_state_by_entity(entity_id)
    if not character:
        return JSONResponse({"error": "Character not found in database"}, status_code=404)

    session = PlayerSession(entity_id, account.id, api_key)
    _active_sessions[api_key] = session

    turn = db.get_turn()
    first_player = len(turn["turn_order"]) == 0

    if entity_id not in turn["turn_order"]:
        db.add_to_order(entity_id)

    if first_player:
        # First player gets their turn immediately — no DM involvement
        db.set_player_turn(entity_id)
        log.info("First player — turn set directly, no DM trigger.")
    else:
        # Subsequent players: DM needs to incorporate them into the scene
        db.append_dm_message("user", {
            "type": "new_player_joined",
            "entity_id": entity_id,
            "name": character.sheet.name,
        })
        db.set_dm_turn_pending(True)
        log.info("Additional player — DM turn triggered to incorporate.")

    log.info("Player joined: %s (entity: %s)", character.sheet.name, entity_id)

    return JSONResponse({
        "status": "joined",
        "entity_id": entity_id,
        "name": character.sheet.name,
    })


# ---------------------------------------------------------------------------
# REST play endpoints — JWT auth, requires active session
# ---------------------------------------------------------------------------

def _get_session_from_request(request: Request) -> tuple[Optional[PlayerSession], Optional[JSONResponse]]:
    """Extract API key from header and look up active session."""
    api_key = _extract_api_key(request)
    if not api_key:
        return None, JSONResponse(
            {"error": "Missing Authorization: Bearer <api_key> header"},
            status_code=401,
        )

    session = _active_sessions.get(api_key)
    if not session or not session.active:
        return None, JSONResponse(
            {"error": "No active session. POST /join first."},
            status_code=403,
        )

    return session, None


async def handle_play_prompt(request: Request):
    """Return the current turn system prompt."""
    session, err = _get_session_from_request(request)
    if err:
        return err

    prompt = player_mcp.build_player_prompt(session.entity_id)
    dm_pending = db.is_dm_turn_pending()
    your_turn = db.get_player_turn() == session.entity_id
    return JSONResponse({"prompt": prompt, "dm_pending": dm_pending, "your_turn": your_turn})


async def handle_play_tools(request: Request):
    """Return the list of currently available tools."""
    session, err = _get_session_from_request(request)
    if err:
        return err

    cs = db.get_character_state_by_entity(session.entity_id)
    if not cs:
        return JSONResponse({"error": "Character not found"}, status_code=404)

    tools = registry.get_available_tools(cs)
    return JSONResponse({"tools": tools})


async def handle_play_tool(request: Request):
    """Execute a single player tool call."""
    session, err = _get_session_from_request(request)
    if err:
        return err

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    tool_name = body.get("tool")
    arguments = body.get("arguments", {})
    if not tool_name:
        return JSONResponse({"error": "Missing 'tool' field"}, status_code=400)

    result = player_tools.execute_player_tool(session.entity_id, tool_name, arguments)

    session.turn_log.append({
        "tool": tool_name,
        "arguments": arguments,
        "result": result,
    })

    return JSONResponse(result)


async def handle_play_end_turn(request: Request):
    """End the current turn."""
    session, err = _get_session_from_request(request)
    if err:
        return err

    result = _do_end_turn(session)
    return JSONResponse(result)


async def handle_play_signoff(request: Request):
    """Sign off from the session."""
    session, err = _get_session_from_request(request)
    if err:
        return err

    try:
        body = await request.json()
    except Exception:
        body = {}

    result = _do_signoff(session, body)
    return JSONResponse(result)


# ---------------------------------------------------------------------------
# DM REST endpoints — API key auth
# ---------------------------------------------------------------------------

def _validate_dm_auth(request: Request) -> Optional[JSONResponse]:
    """Validate DM API key from Authorization header. Returns error response or None."""
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return JSONResponse(
            {"error": "Missing Authorization: Bearer <dm-api-key> header"},
            status_code=401,
        )
    if auth[7:] != config.dm_api_key():
        return JSONResponse({"error": "Invalid DM API key"}, status_code=403)
    return None


async def handle_dm_poll(request: Request):
    """Check if the DM has a pending turn. Returns chat history if pending."""
    err = _validate_dm_auth(request)
    if err:
        return err

    pending = db.is_dm_turn_pending()
    if not pending:
        return JSONResponse({"pending": False})

    history = db.get_dm_chat_history()
    return JSONResponse({"pending": True, "messages": history})


async def handle_dm_tools(request: Request):
    """Return the full DM tool catalogue."""
    err = _validate_dm_auth(request)
    if err:
        return err

    tools = dm_tools.get_dm_tools()
    return JSONResponse({"tools": tools})


async def handle_dm_tool(request: Request):
    """Execute a single DM tool call."""
    err = _validate_dm_auth(request)
    if err:
        return err

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    tool_name = body.get("tool")
    arguments = body.get("arguments", {})
    if not tool_name:
        return JSONResponse({"error": "Missing 'tool' field"}, status_code=400)

    result = dm_tools.execute_dm_tool(tool_name, arguments)
    return JSONResponse(result)


async def handle_dm_turn_complete(request: Request):
    """Signal that the DM has finished resolving the current turn.

    Clears dm_turn_pending and advances the turn to the next player.
    """
    err = _validate_dm_auth(request)
    if err:
        return err

    db.set_dm_turn_pending(False)

    # Advance to next player in turn order
    turn = db.get_turn()
    order = turn["turn_order"]
    if order:
        next_entity, _ = db.advance_turn()
        db.set_player_turn(next_entity)
        log.info("DM turn complete — next player: %s", next_entity)
    else:
        db.set_player_turn(None)
        log.warning("DM turn complete but turn order is empty.")

    return JSONResponse({"status": "dm_turn_complete"})


async def handle_dm_append_history(request: Request):
    """Append a message to DM chat history."""
    err = _validate_dm_auth(request)
    if err:
        return err

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    role = body.get("role", "assistant")
    content = body.get("content", "")
    db.append_dm_message(role, content)
    return JSONResponse({"status": "appended"})


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

def create_app() -> Starlette:
    db.create_tables()
    loader.load_all()
    scene_loader.load_scenes()

    app = Starlette(
        routes=[
            Route("/join", handle_join, methods=["POST"]),
            # Player endpoints
            Route("/api/play/prompt", handle_play_prompt, methods=["GET"]),
            Route("/api/play/tools", handle_play_tools, methods=["GET"]),
            Route("/api/play/tool", handle_play_tool, methods=["POST"]),
            Route("/api/play/end_turn", handle_play_end_turn, methods=["POST"]),
            Route("/api/play/signoff", handle_play_signoff, methods=["POST"]),
            # DM endpoints
            Route("/api/dm/poll", handle_dm_poll, methods=["POST"]),
            Route("/api/dm/tools", handle_dm_tools, methods=["GET"]),
            Route("/api/dm/tool", handle_dm_tool, methods=["POST"]),
            Route("/api/dm/turn-complete", handle_dm_turn_complete, methods=["POST"]),
            Route("/api/dm/append-history", handle_dm_append_history, methods=["POST"]),
        ],
    )
    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    host = config.game_server_host()
    port = config.game_server_port()
    log.info("Game server starting on %s:%d", host, port)
    app = create_app()
    uvicorn.run(app, host=host, port=port)
