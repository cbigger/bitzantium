"""
game_server.py
==============
Game server (the "bitzantium server") — wraps player and DM MCP behind
streamable HTTP with authentication.

Player agents connect to /mcp with a JWT in the Authorization: Bearer header.
The DM agent connects to /dm-mcp with the pre-configured DM API key as Bearer.

Responsibilities:
    - POST /join:  accept JWT, verify character exists, register active session
    - /mcp:        Player MCP endpoint (JWT auth)
    - /dm-mcp:     DM MCP endpoint (API key auth)
    - Intercept end_turn/signoff tool calls at the MCP layer for lifecycle
    - On player end_turn: append raw turn output to DM chat history, set dm_turn_pending
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
import dm_tools
import jwt_utils
import loader
import player_mcp
import player_tools
import registry

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
        self.turn_log: list[dict] = []  # raw tool calls + responses for the current turn


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

    # Log raw tool call + response for DM context
    if session:
        session.turn_log.append({
            "tool": name,
            "arguments": arguments or {},
            "result": result,
        })

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
    """End the current player's turn. Resets economy, appends raw turn
    output to DM chat history, sets dm_turn_pending."""
    session.turns_taken += 1
    at_limit = session.max_turns is not None and session.turns_taken >= session.max_turns

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

    # Signal DM that it has work to do
    db.set_dm_turn_pending(True)

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
    db.remove_from_order(session.entity_id)

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

    turn = db.get_turn()
    if entity_id not in turn["turn_order"]:
        db.add_to_order(entity_id)

    log.info("Player joined: %s (entity: %s)", character.sheet.name, entity_id)

    return JSONResponse({
        "status": "joined",
        "entity_id": entity_id,
        "name": character.sheet.name,
        "max_turns": session.max_turns,
        "mcp_endpoint": "/mcp",
    })


# ---------------------------------------------------------------------------
# DM MCP server — tools, prompt, status/context, turn completion
# ---------------------------------------------------------------------------

_dm_mcp_server = Server("bitzantium-dm")


@_dm_mcp_server.list_tools()
async def dm_handle_list_tools() -> list[types.Tool]:
    """Return the full DM tool catalogue plus dm_turn_complete."""
    tools = [
        types.Tool(
            name=t["name"],
            description=t["description"],
            inputSchema=t["inputSchema"],
        )
        for t in dm_tools.get_dm_tools()
    ]
    # Add the dm_turn_complete lifecycle tool
    tools.append(types.Tool(
        name="dm_turn_complete",
        description="Signal that the DM has finished resolving the current turn.",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ))
    # Add the dm_poll tool (check pending + pull context)
    tools.append(types.Tool(
        name="dm_poll",
        description=(
            "Check if the DM has a pending turn. If pending, returns the full "
            "DM chat history for context. If not pending, returns pending=false."
        ),
        inputSchema={"type": "object", "properties": {}, "required": []},
    ))
    return tools


@_dm_mcp_server.call_tool()
async def dm_handle_call_tool(
    name: str,
    arguments: dict[str, Any] | None,
) -> list[types.TextContent]:
    """Execute a DM tool call."""
    if name == "dm_turn_complete":
        db.set_dm_turn_pending(False)
        return [types.TextContent(type="text", text=json.dumps({"status": "dm_turn_complete"}))]

    if name == "dm_poll":
        pending = db.is_dm_turn_pending()
        if not pending:
            return [types.TextContent(type="text", text=json.dumps({"pending": False}))]
        history = db.get_dm_chat_history()
        return [types.TextContent(type="text", text=json.dumps({
            "pending": True,
            "messages": history,
        }))]

    if name == "dm_append_history":
        args = arguments or {}
        role = args.get("role", "assistant")
        content = args.get("content", "")
        db.append_dm_message(role, content)
        return [types.TextContent(type="text", text=json.dumps({"status": "appended"}))]

    result = dm_tools.execute_dm_tool(name, arguments or {})
    return [types.TextContent(type="text", text=json.dumps(result, indent=2))]


@_dm_mcp_server.list_prompts()
async def dm_handle_list_prompts() -> list[types.Prompt]:
    return [
        types.Prompt(
            name="dm_context",
            description="Current scene, turn state, and DM chat history",
        )
    ]


@_dm_mcp_server.get_prompt()
async def dm_handle_get_prompt(
    name: str,
    arguments: dict[str, str] | None,
) -> types.GetPromptResult:
    """Return full DM context: scene state, turn state, chat history."""
    scene = db.get_scene()
    turn = db.get_turn()
    history = db.get_dm_chat_history()
    context = {
        "scene": scene,
        "turn": turn,
        "chat_history": history,
    }
    return types.GetPromptResult(
        description="DM agent context for the current turn",
        messages=[
            types.PromptMessage(
                role="user",
                content=types.TextContent(type="text", text=json.dumps(context, indent=2)),
            )
        ],
    )


# ---------------------------------------------------------------------------
# DM MCP streamable HTTP with API key middleware
# ---------------------------------------------------------------------------

_dm_session_manager = StreamableHTTPSessionManager(
    app=_dm_mcp_server,
    json_response=True,
    stateless=True,
)


class DmAPIKeyMiddleware:
    """ASGI middleware that validates the DM API key from the Bearer header."""

    def __init__(self, mcp_app):
        self.mcp_app = mcp_app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "https"):
            await self.mcp_app(scope, receive, send)
            return

        auth_value = ""
        for key, val in scope.get("headers", []):
            if key == b"authorization":
                auth_value = val.decode("utf-8")
                break

        if not auth_value.startswith("Bearer "):
            response = JSONResponse(
                {"error": "Missing Authorization: Bearer <dm-api-key> header"},
                status_code=401,
            )
            await response(scope, receive, send)
            return

        provided_key = auth_value[7:]
        if provided_key != config.dm_api_key():
            response = JSONResponse({"error": "Invalid DM API key"}, status_code=403)
            await response(scope, receive, send)
            return

        await self.mcp_app(scope, receive, send)


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

def create_app() -> Starlette:
    db.create_tables()
    loader.load_all()

    jwt_mcp = JWTMCPMiddleware(_session_manager.handle_request)
    dm_mcp = DmAPIKeyMiddleware(_dm_session_manager.handle_request)

    app = Starlette(
        routes=[
            Route("/join", handle_join, methods=["POST"]),
            Mount("/mcp", app=jwt_mcp),
            Mount("/dm-mcp", app=dm_mcp),
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
