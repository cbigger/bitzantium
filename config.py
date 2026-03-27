"""
config.py

Loads bitzantium.toml and exposes configuration values.

Searches for the config file in order:
    1. BITZANTIUM_CONFIG env var (explicit path)
    2. ./bitzantium.toml (working directory)
    3. <script_dir>/bitzantium.toml (next to this file)
"""

import os
import tomllib
from pathlib import Path

_config: dict = {}


def _find_config_path() -> Path:
    """Locate the config file."""
    env = os.environ.get("BITZANTIUM_CONFIG")
    if env:
        return Path(env)

    cwd = Path.cwd() / "bitzantium.toml"
    if cwd.exists():
        return cwd

    return Path(__file__).resolve().parent / "bitzantium.toml"


def _load() -> dict:
    global _config
    if _config:
        return _config
    path = _find_config_path()
    with open(path, "rb") as f:
        _config = tomllib.load(f)
    return _config


def get() -> dict:
    """Return the full config dict, loading on first call."""
    return _load()


# Convenience accessors

def database_url() -> str:
    return get()["database"]["url"]

def auth_server_host() -> str:
    return get()["auth_server"]["host"]

def auth_server_port() -> int:
    return get()["auth_server"]["port"]

def game_server_host() -> str:
    return get()["game_server"]["host"]

def game_server_port() -> int:
    return get()["game_server"]["port"]

def game_server_url() -> str:
    return get()["game_server"]["url"]

def jwt_secret() -> str:
    return get()["jwt"]["secret"]

def jwt_algorithm() -> str:
    return get()["jwt"]["algorithm"]
