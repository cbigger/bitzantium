"""
game_server.py
==============
Game server (the "bitzantium server") — wraps player MCP behind
streamable HTTP with JWT authentication.

The agent connects to /mcp as a standard MCP client with its JWT in the
Authorization: Bearer header. Tool discovery, tool calls, and prompt
retrieval all go through MCP natively — the agent never deals with auth
in tool calls.

Responsibilities:
    - POST /join: accept JWT, load character, spin up per-player MCP state
    - /mcp:       MCP-over-streamable-HTTP endpoint (JWT in Bearer header)
    - Intercept end_turn/signoff tool calls at the MCP layer for lifecycle
    - Track active players, turn counts, session limits

Run:
    python game_server.py
"""

import json
import logging
import os
from typing import Any, Optional

import jwt as pyjwt
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

import mcp.types as types
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

import db
import jwt_utils
import loader
import player_mcp
import player_tools
import registry
import state as state_module
import turn_state as turns
from pathlib import Path

log = logging.getLogger(__name__)

REALM_PATH = Path(__file__).resolve().parent / "Realms" / "dnd"
HOST = os.environ.get("GAME_SERVER_HOST", "0.0.0.0")
PORT = int(os.environ.get("GAME_SERVER_PORT", "8081"))


# ---------------------------------------------------------------------------
# Active session tracking
# ---------------------------------------------------------------------------

class PlayerSession:
    """Tracks an active player in this game server."""
    def __init__(self, entity_id: str, account_id: int, token_claims: dict):
        self.entity_id = entity_id
        self.account_id = account_id
        self.max_turns: Optional[int] = token_claims.get("max_turns")
        self.turns_taken: int = 0
        self.active: bool = True


# entity_id -> PlayerSession
_active_sessions: dict[str, PlayerSession] = {}


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

def _validate_bearer(headers: dict) -> tuple[Optional[dict], Optional[str]]:
    """Extract and validate JWT from headers. Returns (claims, error_msg)."""
    auth = headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return None, "Missing Authorization: Bearer <token> header"
    token = auth[7:]
    try:
        claims = jwt_utils.validate_session_token(token)
    except pyjwt.ExpiredSignatureError:
        return None, "Session token expired"
    except pyjwt.InvalidTokenError as e:
        return None, f"Invalid token: {e}"
    return claims, None


# ---------------------------------------------------------------------------
# Per-player MCP server factory
#
# Each player gets their own MCP Server instance. The game server creates
# one on /join and routes MCP requests to it based on JWT identity.
#
# We use a single shared MCP Server for now since the player_mcp module
# already scopes state via set_turn_context + state_module keyed by
# entity_id. Per-player Server instances can come later for full isolation.
# ---------------------------------------------------------------------------

_mcp_server = Server("bitzantium-player")


def _load_player_state(entity_id: str) -> bool:
    """Load character + turn context from DB into in-memory stores.
    Returns False if character not found."""
    character = db.get_character_state_by_entity(entity_id)
    if not character:
        return False
    state_module.update_character(entity_id, character)

    context = db.get_turn_context_by_entity(entity_id)
    if context:
        player_mcp.set_turn_context(
            entity_id=entity_id,
            story_so_far=context.get("story_so_far", ""),
            location_area=context.get("location_area", ""),
            location_sub=context.get("location_sub"),
            quest_log=context.get("quest_log"),
        )
    else:
        player_mcp.set_turn_context(entity_id=entity_id, story_so_far="", location_area="")
    return True


@_mcp_server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    """Gated tool list for the active entity."""
    entity_id = player_mcp._entity_id
    if not entity_id:
        return []
    cs = state_module.get_character(entity_id)
    if not cs:
        return []
    return [
        types.Tool(
            name=t["name"],
            description=t["description"],
            inputSchema=t["inputSchema"],
        )
        for t in registry.get_available_tools(cs)
    ]


@_mcp_server.call_tool()
async def handle_call_tool(
    name: str,
    arguments: dict[str, Any] | None,
) -> list[types.TextContent]:
    """Execute a player tool, intercepting session lifecycle tools."""
    entity_id = player_mcp._entity_id
    if not entity_id:
        return [types.TextContent(type="text", text=json.dumps({"error": "No active entity"}))]

    session = _active_sessions.get(entity_id)

    # --- Session lifecycle interception ---
    if name == "end_turn" and session:
        result = _do_end_turn(session)
        return [types.TextContent(type="text", text=json.dumps(result))]

    if name == "signoff" and session:
        result = _do_signoff(session, arguments or {})
        return [types.TextContent(type="text", text=json.dumps(result))]

    # --- Regular tool execution ---
    result = player_tools.execute_player_tool(entity_id, name, arguments or {})

    # Persist updated state
    updated_state = state_module.get_character(entity_id)
    if updated_state:
        db.save_character_state(entity_id, updated_state)

    return [types.TextContent(type="text", text=json.dumps(result, indent=2))]


@_mcp_server.list_prompts()
async def handle_list_prompts() -> list[types.Prompt]:
    return [
        types.Prompt(
            name="player_context",
            description="Current turn system prompt for the player agent",
        )
    ]


@_mcp_server.get_prompt()
async def handle_get_prompt(
    name: str,
    arguments: dict[str, str] | None,
) -> types.GetPromptResult:
    """Return the current turn system prompt."""
    prompt = player_mcp.build_player_prompt()
    return types.GetPromptResult(
        description="Player agent system prompt for the current turn",
        messages=[
            types.PromptMessage(
                role="user",
                content=types.TextContent(type="text", text=prompt),
            )
        ],
    )


# ---------------------------------------------------------------------------
# Session lifecycle logic
# ---------------------------------------------------------------------------

def _do_end_turn(session: PlayerSession) -> dict:
    """End the current player's turn."""
    session.turns_taken += 1
    at_limit = session.max_turns is not None and session.turns_taken >= session.max_turns

    state_module.reset_economy(session.entity_id)

    updated = state_module.get_character(session.entity_id)
    if updated:
        db.save_character_state(session.entity_id, updated)

    return {
        "status": "turn_ended",
        "entity_id": session.entity_id,
        "turns_taken": session.turns_taken,
        "max_turns": session.max_turns,
        "session_limit_reached": at_limit,
    }


def _do_signoff(session: PlayerSession, args: dict) -> dict:
    """Sign the player off from the session."""
    departure_action = args.get("departure_action", "")

    db.save_departure_action(session.entity_id, departure_action)

    final_state = state_module.get_character(session.entity_id)
    if final_state:
        db.save_character_state(session.entity_id, final_state)

    session.active = False
    turns.remove_from_order(session.entity_id)
    state_module.remove_character(session.entity_id)

    log.info("Player signed off: %s — %s", session.entity_id, departure_action)

    return {
        "status": "signed_off",
        "entity_id": session.entity_id,
        "departure_action": departure_action,
        "turns_taken": session.turns_taken,
    }


# ---------------------------------------------------------------------------
# MCP streamable HTTP with JWT middleware
# ---------------------------------------------------------------------------

_session_manager = StreamableHTTPSessionManager(
    app=_mcp_server,
    json_response=True,
    stateless=True,
)


class JWTMCPMiddleware:
    """ASGI middleware that validates JWT and loads player state before
    passing requests through to the MCP session manager.

    Sits between the Starlette router and the MCP transport. Every MCP
    request (POST for tool calls, GET for SSE streams) gets JWT-validated
    here. The agent's MCP client just sets Authorization: Bearer <jwt>
    once — it never touches auth in tool arguments.
    """

    def __init__(self, mcp_app):
        self.mcp_app = mcp_app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "https"):
            await self.mcp_app(scope, receive, send)
            return

        # Extract Authorization header from ASGI scope
        headers = dict(scope.get("headers", []))
        # ASGI headers are bytes pairs
        auth_value = ""
        for key, val in scope.get("headers", []):
            if key == b"authorization":
                auth_value = val.decode("utf-8")
                break

        if not auth_value.startswith("Bearer "):
            response = JSONResponse(
                {"error": "Missing Authorization: Bearer <token> header"},
                status_code=401,
            )
            await response(scope, receive, send)
            return

        token = auth_value[7:]
        try:
            claims = jwt_utils.validate_session_token(token)
        except pyjwt.ExpiredSignatureError:
            response = JSONResponse({"error": "Session token expired"}, status_code=401)
            await response(scope, receive, send)
            return
        except pyjwt.InvalidTokenError as e:
            response = JSONResponse({"error": f"Invalid token: {e}"}, status_code=401)
            await response(scope, receive, send)
            return

        entity_id = claims["entity_id"]

        session = _active_sessions.get(entity_id)
        if not session or not session.active:
            response = JSONResponse(
                {"error": "No active session. POST /join with your token first."},
                status_code=403,
            )
            await response(scope, receive, send)
            return

        # Load latest player state so MCP handlers see current data
        _load_player_state(entity_id)

        # Pass through to MCP
        await self.mcp_app(scope, receive, send)


# ---------------------------------------------------------------------------
# /join — REST endpoint to register in the session
# ---------------------------------------------------------------------------

async def handle_join(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    # Accept token from body or header
    claims, error = _validate_bearer(dict(request.headers))
    if error:
        # Try body
        token = body.get("token")
        if not token:
            return JSONResponse({"error": error}, status_code=401)
        try:
            claims = jwt_utils.validate_session_token(token)
        except pyjwt.ExpiredSignatureError:
            return JSONResponse({"error": "Session token expired"}, status_code=401)
        except pyjwt.InvalidTokenError as e:
            return JSONResponse({"error": f"Invalid token: {e}"}, status_code=401)

    entity_id = claims["entity_id"]
    account_id = claims["account_id"]

    character = db.get_character_state_by_entity(entity_id)
    if not character:
        return JSONResponse({"error": "Character not found in database"}, status_code=404)

    state_module.update_character(entity_id, character)

    session = PlayerSession(entity_id, account_id, claims)
    _active_sessions[entity_id] = session

    turn = turns.get_turn()
    if entity_id not in turn.turn_order:
        turns.add_to_order(entity_id)

    log.info("Player joined: %s (entity: %s)", character.sheet.name, entity_id)

    return JSONResponse({
        "status": "joined",
        "entity_id": entity_id,
        "name": character.sheet.name,
        "max_turns": session.max_turns,
        "mcp_endpoint": "/mcp",
    })


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

def create_app() -> Starlette:
    db.create_tables()
    loader.load_all(str(REALM_PATH))

    jwt_mcp = JWTMCPMiddleware(_session_manager.handle_request)

    app = Starlette(
        routes=[
            Route("/join", handle_join, methods=["POST"]),
            Mount("/mcp", app=jwt_mcp),
        ],
    )
    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    log.info("Game server starting on %s:%d", HOST, PORT)
    app = create_app()
    uvicorn.run(app, host=HOST, port=PORT)
