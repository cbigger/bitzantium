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
    - POST /join: accept JWT, verify character exists, register active session
    - /mcp:       MCP-over-streamable-HTTP endpoint (JWT in Bearer header)
    - Intercept end_turn/signoff tool calls at the MCP layer for lifecycle
    - Track active players, turn counts, session limits

Run:
    python game_server.py
"""

import contextvars
import json
import logging
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

import config
import db
import jwt_utils
import loader
import player_mcp
import player_tools
import registry
import turn_state as turns

# contextvars — threaded through from JWT middleware to MCP handlers
_current_entity_id: contextvars.ContextVar[str] = contextvars.ContextVar("_current_entity_id")

log = logging.getLogger(__name__)


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
# We use a single shared MCP Server. Entity scoping is handled via
# contextvars set by JWT middleware — all state reads go to DB directly.
# Per-player Server instances can come later for full isolation.
# ---------------------------------------------------------------------------

_mcp_server = Server("bitzantium-player")


@_mcp_server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    """Gated tool list for the active entity (from JWT via contextvars)."""
    entity_id = _current_entity_id.get(None)
    if not entity_id:
        return []
    cs = db.get_character_state_by_entity(entity_id)
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
    entity_id = _current_entity_id.get(None)
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

    # --- Regular tool execution (reads/writes DB directly) ---
    result = player_tools.execute_player_tool(entity_id, name, arguments or {})
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
    """Return the current turn system prompt (pulls from DB via entity_id)."""
    entity_id = _current_entity_id.get(None)
    if not entity_id:
        text = "No active entity."
    else:
        text = player_mcp.build_player_prompt(entity_id)
    return types.GetPromptResult(
        description="Player agent system prompt for the current turn",
        messages=[
            types.PromptMessage(
                role="user",
                content=types.TextContent(type="text", text=text),
            )
        ],
    )


# ---------------------------------------------------------------------------
# Session lifecycle logic
# ---------------------------------------------------------------------------

def _do_end_turn(session: PlayerSession) -> dict:
    """End the current player's turn. Resets economy in DB."""
    session.turns_taken += 1
    at_limit = session.max_turns is not None and session.turns_taken >= session.max_turns

    db.reset_economy(session.entity_id)

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

    session.active = False
    turns.remove_from_order(session.entity_id)

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

        # Set entity_id in contextvars so MCP handlers can read it
        _current_entity_id.set(entity_id)

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
    loader.load_all()

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
    host = config.game_server_host()
    port = config.game_server_port()
    log.info("Game server starting on %s:%d", host, port)
    app = create_app()
    uvicorn.run(app, host=host, port=port)
