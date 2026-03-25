"""
scene_state.py

In-memory scene state: entity positions, area description, light level.
Scoped to one active scene per server instance for now.
"""

import threading
from typing import Optional

from pydantic import BaseModel, Field

from bitzantium_schemas.schemas import Position, LightLevel


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

class SceneState(BaseModel):
    area_id:           str        = "liminal"
    area_name:         str        = "Liminal Space"
    area_description:  str        = ""
    entity_positions:  dict[str, Position] = Field(default_factory=dict)
    light_level:       LightLevel = LightLevel.BRIGHT


# ---------------------------------------------------------------------------
# Module-level store
# ---------------------------------------------------------------------------

_scene: Optional[SceneState] = None
_lock  = threading.RLock()


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------

def get_scene() -> SceneState:
    with _lock:
        return _scene if _scene is not None else SceneState()


def init_scene(
    area_id:          str        = "liminal",
    area_name:        str        = "Liminal Space",
    area_description: str        = "",
    light_level:      LightLevel = LightLevel.BRIGHT,
) -> SceneState:
    global _scene
    scene = SceneState(
        area_id=area_id,
        area_name=area_name,
        area_description=area_description,
        light_level=light_level,
    )
    with _lock:
        _scene = scene
    return scene


def place_entity(entity_id: str, x: int, y: int, z: int = 0) -> SceneState:
    global _scene
    with _lock:
        base = _scene if _scene is not None else SceneState()
        area_id  = base.area_id
        new_pos  = Position(x=x, y=y, z=z, area_id=area_id)
        updated  = base.model_copy(update={
            "entity_positions": {**base.entity_positions, entity_id: new_pos}
        })
        _scene = updated
        return updated


def remove_entity(entity_id: str) -> SceneState:
    global _scene
    with _lock:
        base     = _scene if _scene is not None else SceneState()
        filtered = {k: v for k, v in base.entity_positions.items() if k != entity_id}
        updated  = base.model_copy(update={"entity_positions": filtered})
        _scene   = updated
        return updated


def distance_between(entity_a: str, entity_b: str) -> Optional[float]:
    """Euclidean distance in feet (1 grid unit = 5 ft)."""
    with _lock:
        if _scene is None:
            return None
        pa = _scene.entity_positions.get(entity_a)
        pb = _scene.entity_positions.get(entity_b)
    if pa is None or pb is None:
        return None
    grid_dist = ((pa.x - pb.x) ** 2 + (pa.y - pb.y) ** 2 + (pa.z - pb.z) ** 2) ** 0.5
    return grid_dist * 5  # convert grid squares to feet
