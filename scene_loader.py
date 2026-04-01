"""
scene_loader.py
===============
Loads scene exposition files from disk into an in-memory registry.

Each scene is a subdirectory under the configured scenes directory
containing an EXPOSITION.md file.

    scenes/
      aether/
        EXPOSITION.md
"""

import logging
import sys
from pathlib import Path

import config

log = logging.getLogger(__name__)

_scenes: dict[str, str] = {}


def load_scenes(base_path: str | None = None) -> dict[str, str]:
    """Walk the scenes directory and load EXPOSITION.md from each subdirectory.

    Fails hard if the configured default scene cannot be loaded.
    Returns the registry dict (scene_id → exposition text).
    """
    _scenes.clear()

    base = Path(base_path) if base_path else Path(config.scenes_directory())
    if not base.is_absolute():
        base = Path(__file__).resolve().parent / base

    if not base.is_dir():
        log.error("Scenes directory not found: %s", base)
        sys.exit(1)

    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        expo_path = child / "EXPOSITION.md"
        if not expo_path.exists():
            log.warning("Scene '%s' has no EXPOSITION.md — skipping", child.name)
            continue
        text = expo_path.read_text(encoding="utf-8").strip()
        if not text:
            log.warning("Scene '%s' EXPOSITION.md is empty — skipping", child.name)
            continue
        _scenes[child.name] = text
        log.info("Loaded scene: %s (%d chars)", child.name, len(text))

    default = config.default_scene()
    if default not in _scenes:
        log.error("Default scene '%s' not found. Available: %s", default, list(_scenes.keys()))
        sys.exit(1)

    log.info("Loaded %d scene(s), default: %s", len(_scenes), default)
    return _scenes


def get_exposition(scene_id: str) -> str:
    """Return exposition text for a scene. Raises KeyError if not loaded."""
    return _scenes[scene_id]


def list_scenes() -> list[str]:
    """Return all loaded scene IDs."""
    return list(_scenes.keys())
