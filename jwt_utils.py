"""
jwt_utils.py
============
Shared JWT creation and validation utilities.

Auth wrapper uses create_session_token() when an agent joins a session.
Game server uses validate_session_token() on every request.

Signing: HS256 with a shared secret from bitzantium.toml.
"""

import time
from typing import Optional

import jwt

import config


def create_session_token(
    entity_id: str,
    account_id: int,
    game_server_url: str,
    max_turns: Optional[int] = None,
    session_duration_seconds: int = 3600,
) -> str:
    """Create a JWT for a player joining a game session.

    Claims:
        entity_id:        character entity ID
        account_id:       DB account ID
        game_server_url:  the game server this token is valid for
        max_turns:        optional turn limit for the session
        iat:              issued-at timestamp
        exp:              expiry timestamp
    """
    now = int(time.time())
    payload = {
        "entity_id": entity_id,
        "account_id": account_id,
        "game_server_url": game_server_url,
        "max_turns": max_turns,
        "iat": now,
        "exp": now + session_duration_seconds,
    }
    return jwt.encode(payload, config.jwt_secret(), algorithm=config.jwt_algorithm())


def validate_session_token(token: str) -> dict:
    """Validate and decode a session JWT.

    Returns the decoded payload dict on success.
    Raises jwt.ExpiredSignatureError, jwt.InvalidTokenError on failure.
    """
    return jwt.decode(token, config.jwt_secret(), algorithms=[config.jwt_algorithm()])
