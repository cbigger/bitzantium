"""
player_tools.py

Executes player-agent tool calls server-side.

Each call goes through three phases:

  1. Validate  — re-runs the same four-layer gate logic as registry.get_available_tools()
                 (conditions/economy may have changed since the system prompt was sent)

  2. Spend     — commits action economy cost to the DB (action / bonus_action /
                 reaction only). Spell slots and class resources are intentionally
                 NOT spent here — those are committed by the DM via dm_tools after
                 narrative resolution.

  3. Snapshot  — reads character state from the DB to produce the mechanical context
                 the DMAgent needs to resolve the action: attacker modifiers, target
                 defences, resource availability, etc.

Public API:
    execute_player_tool(entity_id, tool_name, args) → result dict
"""

import sys
import os
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "bitzantium_schemas", "src"))

import db
import rules
from registry import _TOOLS

from bitzantium_schemas.character import CharacterState, ProficiencyLevel
from bitzantium_schemas.schemas import Condition


# ---------------------------------------------------------------------------
# Internal helpers — bonuses only, no dice rolls
# ---------------------------------------------------------------------------

def _skill_bonus(sheet, skill: str) -> int:
    """Return total skill modifier (ability mod + proficiency) without rolling."""
    ability = rules.SKILL_ABILITY.get(skill.lower(), "strength")
    score   = getattr(sheet.ability_scores, ability, 10)
    mod     = rules.ability_mod(score)
    pb      = sheet.proficiency_bonus

    for sp in sheet.skill_proficiencies:
        if sp.skill.lower() == skill.lower():
            lvl = sp.proficiency_level
            if lvl == ProficiencyLevel.HALF:
                return mod + pb // 2
            if lvl == ProficiencyLevel.PROFICIENT:
                return mod + pb
            if lvl == ProficiencyLevel.EXPERT:
                return mod + pb * 2
            break

    return mod


def _weapon_from_slot(sheet, weapon_slot: str):
    """Return (ItemInstance | None, WeaponProperties) for the given slot."""
    slot_map = {
        "main_hand": sheet.equipment.main_hand,
        "off_hand":  sheet.equipment.off_hand,
        "ranged":    sheet.equipment.main_hand,  # ranged weapons live in main_hand
    }
    item_inst = slot_map.get(weapon_slot, sheet.equipment.main_hand)
    if item_inst and item_inst.item_base.weapon_properties:
        return item_inst, item_inst.item_base.weapon_properties
    return None, rules.UNARMED_PROPS


def _weapon_snap(sheet, weapon_slot: str) -> dict:
    item_inst, wp = _weapon_from_slot(sheet, weapon_slot)
    return {
        "name":             item_inst.item_base.name if item_inst else "Unarmed Strike",
        "damage_dice":      wp.damage_dice,
        "damage_type":      wp.damage_type.value,
        "versatile_damage": wp.versatile_damage,
        "attack_bonus":     wp.attack_bonus,    # magic/enhancement bonus on the item
        "damage_bonus":     wp.damage_bonus,
        "range_normal":     wp.range_normal,
        "range_max":        wp.range_max,
        "properties":       list(wp.properties),
        "is_finesse":       "finesse" in wp.properties,
        "is_reach":         "reach"   in wp.properties,
    }


def _target_snap(target_id: str, actor_id: str) -> dict:
    """Basic defensive stats for a target entity."""
    cs   = db.get_character_state_by_entity(target_id)
    dist = db.distance_between(actor_id, target_id)
    base = {"id": target_id, "distance_ft": dist}
    if cs is None:
        return {**base, "found": False}
    s = cs.sheet
    return {
        **base,
        "found":       True,
        "hp_current":  s.hp_current,
        "hp_max":      s.hp_max,
        "armor_class": s.armor_class,
        "conditions":  [c.value for c in s.conditions],
    }


def _attacker_snap(sheet) -> dict:
    """Raw attack components the DM uses to assemble a roll."""
    ab = sheet.ability_scores
    return {
        "str_mod":           rules.ability_mod(ab.strength),
        "dex_mod":           rules.ability_mod(ab.dexterity),
        "proficiency_bonus": sheet.proficiency_bonus,
    }


# ---------------------------------------------------------------------------
# Gate validation — mirrors registry.get_available_tools, per-tool
# ---------------------------------------------------------------------------

def _validate(tool_entry: dict, cs: CharacterState) -> str | None:
    """
    Return an error string if the tool is currently gated, None if allowed.
    Mirrors the four-layer gate logic in registry.py exactly.
    """
    sheet   = cs.sheet
    economy = cs.economy

    class_names       = {c.class_id.lower() for c in sheet.classes}
    active_conditions = set(sheet.conditions)
    resource_current  = {r.name.lower(): r.current for r in sheet.class_resources}

    # Layer 1: class
    if tool_entry["classes"] is not None and not (tool_entry["classes"] & class_names):
        return f"Your class cannot use '{tool_entry['name']}'."

    # Layer 2: conditions
    blocking = tool_entry["blocked_by"] & active_conditions
    if blocking:
        return f"Blocked by condition(s): {', '.join(c.value for c in blocking)}."

    # Layer 3: economy
    cost = tool_entry["action_cost"]
    if cost == "action"       and economy.action_spent:
        return "Your action has already been spent this turn."
    if cost == "bonus_action" and economy.bonus_action_spent:
        return "Your bonus action has already been spent this turn."
    if cost == "reaction"     and economy.reaction_spent:
        return "Your reaction has already been spent this turn."

    # Layer 4: named class resource
    required = tool_entry.get("requires_resource")
    if required is not None and resource_current.get(required, 0) <= 0:
        return f"Resource depleted: {required}."

    return None


# ---------------------------------------------------------------------------
# Economy spend
# ---------------------------------------------------------------------------

_ECONOMY_FIELD = {
    "action":       "action_spent",
    "bonus_action": "bonus_action_spent",
    "reaction":     "reaction_spent",
}


def _spend_economy(entity_id: str, cost: str) -> Optional[CharacterState]:
    """Mark the action economy flag in the DB. Returns updated state or None."""
    return db.spend_economy(entity_id, cost)


# ---------------------------------------------------------------------------
# Per-tool snapshot builders
# ---------------------------------------------------------------------------

def _snap_attack(entity_id: str, args: dict, cs: CharacterState) -> dict:
    weapon_slot = args.get("weapon_slot", "main_hand")
    target_id   = args.get("target_id", "")
    snap = {
        **_attacker_snap(cs.sheet),
        "weapon": _weapon_snap(cs.sheet, weapon_slot),
    }
    if target_id:
        snap["target"] = _target_snap(target_id, entity_id)
    return snap


def _snap_cast_spell(entity_id: str, args: dict, cs: CharacterState) -> dict:
    sheet      = cs.sheet
    spell_id   = args.get("spell_id", "")
    slot_level = args.get("slot_level", 0)
    target     = args.get("target", "")

    prepared_ids = {s.spell_id for s in sheet.spells if s.prepared or s.always_prepared}

    snap: dict = {
        "spell_id":             spell_id,
        "slot_level":           slot_level,
        "is_cantrip":           slot_level == 0,
        "spell_known":          any(s.spell_id == spell_id for s in sheet.spells),
        "spell_prepared":       spell_id in prepared_ids,
        "spell_attack_bonus":   sheet.spell_attack_bonus,
        "spell_save_dc":        sheet.spell_save_dc,
        "spellcasting_ability": sheet.spellcasting_ability,
        "slots_available":      {
            lvl: e.remaining
            for lvl, e in sheet.spell_slots.items()
            if e.remaining > 0
        },
        "target_raw": target,
    }
    # Entity targets get a full defensive snapshot; area/self targets don't
    if target and target not in ("self", "") and not target.startswith("area:"):
        snap["target"] = _target_snap(target, entity_id)
    return snap


def _snap_shove_grapple(entity_id: str, args: dict, cs: CharacterState) -> dict:
    target_id = args.get("target_id", "")
    snap: dict = {"athletics_bonus": _skill_bonus(cs.sheet, "athletics")}
    if target_id:
        ts = _target_snap(target_id, entity_id)
        target_cs = db.get_character_state_by_entity(target_id)
        if target_cs:
            ts["athletics_bonus"]  = _skill_bonus(target_cs.sheet, "athletics")
            ts["acrobatics_bonus"] = _skill_bonus(target_cs.sheet, "acrobatics")
        snap["target"] = ts
    return snap


def _snap_sneak_attack(entity_id: str, args: dict, cs: CharacterState) -> dict:
    target_id = args.get("target_id", "")
    snap = {**_attacker_snap(cs.sheet), "weapon": _weapon_snap(cs.sheet, "main_hand")}
    if target_id:
        snap["target"] = _target_snap(target_id, entity_id)
    return snap


def _snap_divine_smite(entity_id: str, args: dict, cs: CharacterState) -> dict:
    sheet      = cs.sheet
    slot_level = args.get("slot_level", 1)
    target_id  = args.get("target_id", "")
    snap: dict = {
        "slot_level":      slot_level,
        "slots_available": {
            lvl: e.remaining
            for lvl, e in sheet.spell_slots.items()
            if e.remaining > 0
        },
    }
    if target_id:
        snap["target"] = _target_snap(target_id, entity_id)
    return snap


def _snap_lay_on_hands(entity_id: str, args: dict, cs: CharacterState) -> dict:
    pool      = next(
        (r for r in cs.sheet.class_resources if r.resource_id == "lay_on_hands"), None
    )
    target_id = args.get("target_id", entity_id)
    return {
        "pool_remaining": pool.current if pool else 0,
        "hp_requested":   args.get("hp_amount", 0),
        "target":         _target_snap(target_id, entity_id),
    }


def _snap_hide(entity_id: str, args: dict, cs: CharacterState) -> dict:
    return {"stealth_bonus": _skill_bonus(cs.sheet, "stealth")}


def _snap_move(entity_id: str, args: dict, cs: CharacterState) -> dict:
    return {
        "speed_ft":           cs.sheet.speed,
        "movement_used":      cs.economy.movement_used,
        "movement_remaining": max(0, cs.sheet.speed - cs.economy.movement_used),
        "destination":        args.get("destination", ""),
        "movement_type":      args.get("movement_type", "walk"),
    }


def _snap_short_rest(entity_id: str, args: dict, cs: CharacterState) -> dict:
    """Snapshot only — actual rest mechanics are resolved by the DM."""
    hit_dice = int(args.get("hit_dice_to_spend", 0))
    sheet = cs.sheet
    return {
        "entity_id": entity_id,
        "rest_type": "short",
        "hit_dice_to_spend": hit_dice,
        "hit_dice_available": sheet.hit_dice_current,
        "hit_dice_total": sheet.hit_dice_total,
        "hp_current": sheet.hp_current,
        "hp_max": sheet.hp_max,
    }


def _snap_long_rest(entity_id: str, args: dict, cs: CharacterState) -> dict:
    """Snapshot only — actual rest mechanics are resolved by the DM."""
    sheet = cs.sheet
    return {
        "entity_id": entity_id,
        "rest_type": "long",
        "hp_current": sheet.hp_current,
        "hp_max": sheet.hp_max,
        "hit_dice_current": sheet.hit_dice_current,
        "hit_dice_total": sheet.hit_dice_total,
        "spell_slots": {str(k): v for k, v in (sheet.spell_slots or {}).items()},
        "conditions": [c.value for c in (sheet.conditions or [])],
    }


def _snap_end_turn(entity_id: str, args: dict, cs: CharacterState) -> dict:
    return {"entity_id": entity_id}


def _snap_signoff(entity_id: str, args: dict, cs: CharacterState) -> dict:
    return {
        "entity_id": entity_id,
        "departure_action": args.get("departure_action", ""),
    }


_SNAPSHOT_DISPATCH: dict[str, object] = {
    "attack":       _snap_attack,
    "cast_spell":   _snap_cast_spell,
    "shove":        _snap_shove_grapple,
    "grapple":      _snap_shove_grapple,
    "sneak_attack": _snap_sneak_attack,
    "divine_smite": _snap_divine_smite,
    "lay_on_hands": _snap_lay_on_hands,
    "hide":         _snap_hide,
    "move":         _snap_move,
    "short_rest":   _snap_short_rest,
    "long_rest":    _snap_long_rest,
    "end_turn":     _snap_end_turn,
    "signoff":      _snap_signoff,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def execute_player_tool(entity_id: str, tool_name: str, args: dict) -> dict:
    """
    Validate and execute a single player tool call.

    Args:
        entity_id: The acting character's ID (as registered in state).
        tool_name: Name of the tool being called (must exist in registry._TOOLS).
        args:      The tool arguments as sent by the player agent.

    Returns a dict:
      {
        "tool":          str,        # tool name
        "args":          dict,       # original args from player agent
        "valid":         bool,
        "error":         str | None, # None on success; reason string on failure
        "economy_spent": str | None, # "action" | "bonus_action" | "reaction" | None
        "snapshot":      dict,       # mechanical context for DMAgent resolution
      }

    On failure, the DB is not mutated.
    On success, action economy is spent immediately in the DB; spell slots
    and class resources remain for the DM to commit via dm_tools after resolution.
    """
    base: dict = {
        "tool":          tool_name,
        "args":          args,
        "valid":         False,
        "error":         None,
        "economy_spent": None,
        "snapshot":      {},
    }

    # Look up tool definition
    tool_entry = next((t for t in _TOOLS if t["name"] == tool_name), None)
    if tool_entry is None:
        base["error"] = f"Unknown tool: '{tool_name}'."
        return base

    # Load character state from DB
    cs = db.get_character_state_by_entity(entity_id)
    if cs is None:
        base["error"] = f"Entity '{entity_id}' not found in database."
        return base

    # Validate against all gates
    error = _validate(tool_entry, cs)
    if error:
        base["error"] = error
        return base

    # Commit economy spend to DB, get back updated state for snapshot
    cost = tool_entry["action_cost"]
    cs = _spend_economy(entity_id, cost) or cs

    snap_fn  = _SNAPSHOT_DISPATCH.get(tool_name)
    snapshot = snap_fn(entity_id, args, cs) if snap_fn else {}

    return {
        "tool":          tool_name,
        "args":          args,
        "valid":         True,
        "error":         None,
        "economy_spent": cost if cost in _ECONOMY_FIELD else None,
        "snapshot":      snapshot,
    }
