"""
dm_tools.py

DM-Agent MCP tool registry.

The DM agent has full authority over world state. Tools here are ungated —
no class/condition/economy filtering. Organisation is by domain:

  Scene setup      — init_scene, place_entity
  State inspection — get_scene_state, get_character_state, get_turn_state
  Turn order       — roll_initiative, set_turn_order, next_turn,
                     add_to_turn_order, remove_from_turn_order
  Roll resolution  — resolve_attack, resolve_saving_throw,
                     resolve_ability_check, resolve_contested_check
  State mutation   — apply_damage, apply_healing, apply_condition,
                     remove_condition, apply_effect, end_effect,
                     move_entity, spend_spell_slot, restore_spell_slot,
                     spend_resource, restore_resource
  Turn bookkeeping — tick_turn_end

Public API:
    get_dm_tools()              → list of tool definition dicts
    execute_dm_tool(name, args) → result dict
"""

import uuid

import db
import rules

from bitzantium_schemas.schemas import Condition, ActiveEffect, LightLevel
from bitzantium_schemas.character import CharacterState


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_cs(entity_id: str) -> CharacterState:
    cs = db.get_character_state_by_entity(entity_id)
    if cs is None:
        raise KeyError(f"Entity {entity_id!r} not found in database.")
    return cs


def _weapon_from_slot(sheet, weapon_slot: str):
    """
    Return WeaponProperties from the requested equipment slot.
    Falls back to unarmed if the slot is empty or the item has no weapon_properties.
    """
    slot_map = {
        "main_hand": sheet.equipment.main_hand,
        "off_hand":  sheet.equipment.off_hand,
        "ranged":    sheet.equipment.main_hand,  # ranged weapons live in main_hand
    }
    item_inst = slot_map.get(weapon_slot, sheet.equipment.main_hand)
    if item_inst and item_inst.item_base.weapon_properties:
        return item_inst.item_base.weapon_properties
    return rules.UNARMED_PROPS


# ---------------------------------------------------------------------------
# Scene setup
# ---------------------------------------------------------------------------

def _handle_init_scene(args: dict) -> dict:
    """Initialise or reset the current scene."""
    area_id    = args.get("area_id", "liminal")
    area_name  = args.get("area_name", "Liminal Space")
    area_desc  = args.get("area_description", "")
    light_raw  = args.get("light_level", "bright")
    light      = LightLevel(light_raw)

    s = db.init_scene(area_id, area_name, area_desc, light.value)
    return {
        "area_id":          s["area_id"],
        "area_name":        s["area_name"],
        "area_description": s["area_description"],
        "light_level":      s["light_level"],
    }


def _handle_place_entity(args: dict) -> dict:
    """Place an already-registered entity at a grid position."""
    entity_id = args["entity_id"]
    x         = int(args.get("x", 0))
    y         = int(args.get("y", 0))
    z         = int(args.get("z", 0))

    cs = _get_cs(entity_id)
    s  = db.place_entity(entity_id, x, y, z)
    return {
        "entity_id": entity_id,
        "name":      cs.sheet.name,
        "position":  {"x": x, "y": y, "z": z, "area_id": s["area_id"]},
    }


# ---------------------------------------------------------------------------
# State inspection
# ---------------------------------------------------------------------------

def _handle_get_scene_state(args: dict) -> dict:
    s        = db.get_scene()
    entities = []
    for eid, pos in s["entity_positions"].items():
        cs = db.get_character_state_by_entity(eid)
        entry = {
            "entity_id":  eid,
            "position":   pos,
        }
        if cs:
            entry["name"]       = cs.sheet.name
            entry["hp_current"] = cs.sheet.hp_current
            entry["hp_max"]     = cs.sheet.hp_max
            entry["conditions"] = [c.value for c in cs.sheet.conditions]
            entry["ac"]         = cs.sheet.armor_class
        entities.append(entry)

    return {
        "area_id":          s["area_id"],
        "area_name":        s["area_name"],
        "area_description": s["area_description"],
        "light_level":      s["light_level"],
        "entities":         entities,
    }


def _handle_get_character_state(args: dict) -> dict:
    entity_id = args["entity_id"]
    cs        = _get_cs(entity_id)
    sh        = cs.sheet
    eco       = cs.economy

    return {
        "entity_id":         entity_id,
        "name":              sh.name,
        "level":             sh.level,
        "classes":           [{"class_id": c.class_id, "level": c.level} for c in sh.classes],
        "hp_current":        sh.hp_current,
        "hp_max":            sh.hp_max,
        "hp_temp":           sh.hp_temp,
        "armor_class":       sh.armor_class,
        "speed":             sh.speed,
        "proficiency_bonus": sh.proficiency_bonus,
        "ability_scores":    sh.ability_scores.model_dump(),
        "conditions":        [c.value for c in sh.conditions],
        "active_effects":    [e.model_dump() for e in sh.active_effects],
        "exhaustion":        sh.exhaustion_level,
        "spell_slots":       {
            str(lvl): {"total": v.total, "remaining": v.remaining}
            for lvl, v in sh.spell_slots.items()
        },
        "class_resources":   [
            {"name": r.name, "current": r.current, "max": r.max}
            for r in sh.class_resources
        ],
        "economy": {
            "action_spent":       eco.action_spent,
            "bonus_action_spent": eco.bonus_action_spent,
            "reaction_spent":     eco.reaction_spent,
            "movement_used":      eco.movement_used,
        },
        "equipment": {
            slot: (
                item.item_base.name
                if item else None
            )
            for slot, item in [
                ("main_hand", sh.equipment.main_hand),
                ("off_hand",  sh.equipment.off_hand),
                ("armor",     sh.equipment.armor),
            ]
        },
    }


def _handle_get_turn_state(args: dict) -> dict:
    t = db.get_turn()
    return {
        "tick":             t["tick"],
        "current_entity":   t["current_entity"],
        "turn_order":       t["turn_order"],
        "initiative_rolls": t["initiative_rolls"],
    }


# ---------------------------------------------------------------------------
# Turn order management
# ---------------------------------------------------------------------------

def _handle_roll_initiative(args: dict) -> dict:
    entity_ids = args["entity_ids"]
    t          = db.roll_initiative(entity_ids)

    return {
        "tick":             t["tick"],
        "current_entity":   t["current_entity"],
        "turn_order":       t["turn_order"],
        "initiative_rolls": t["initiative_rolls"],
    }


def _handle_set_turn_order(args: dict) -> dict:
    entity_ids = args["entity_ids"]
    t          = db.set_turn_order(entity_ids)

    return {
        "tick":             t["tick"],
        "turn_order":       t["turn_order"],
        "current_entity":   t["current_entity"],
    }


def _handle_next_turn(args: dict) -> dict:
    next_id, t = db.advance_turn()
    cs         = db.get_character_state_by_entity(next_id)

    return {
        "tick":           t["tick"],
        "current_entity": next_id,
        "name":           cs.sheet.name if cs else next_id,
        "turn_order":     t["turn_order"],
    }


def _handle_add_to_turn_order(args: dict) -> dict:
    entity_id   = args["entity_id"]
    after_index = args.get("after_index")
    t           = db.add_to_order(entity_id, after_index)

    return {"turn_order": t["turn_order"], "current_entity": t["current_entity"]}


def _handle_remove_from_turn_order(args: dict) -> dict:
    entity_id = args["entity_id"]
    t         = db.remove_from_order(entity_id)

    return {"turn_order": t["turn_order"], "current_entity": t["current_entity"]}


# ---------------------------------------------------------------------------
# Roll resolution  (returns roll details; does NOT mutate state)
# ---------------------------------------------------------------------------

def _handle_resolve_attack(args: dict) -> dict:
    attacker_id  = args["attacker_id"]
    target_id    = args["target_id"]
    weapon_slot  = args.get("weapon_slot", "main_hand")
    advantage    = args.get("advantage", "normal")
    proficient   = bool(args.get("proficient", True))
    two_handed   = bool(args.get("two_handed", False))

    attacker_cs  = _get_cs(attacker_id)
    target_cs    = _get_cs(target_id)
    sheet        = attacker_cs.sheet
    wp           = _weapon_from_slot(sheet, weapon_slot)

    attack       = rules.calc_attack_roll(sheet, wp, proficient, advantage)
    target_ac    = target_cs.sheet.armor_class
    hit          = attack["is_crit"] or (not attack["is_miss"] and attack["total"] >= target_ac)

    result = {
        "attacker_id":  attacker_id,
        "target_id":    target_id,
        "weapon_slot":  weapon_slot,
        "target_ac":    target_ac,
        "attack_roll":  attack,
        "hit":          hit,
    }

    if hit:
        damage = rules.calc_damage(sheet, wp, attack["is_crit"], two_handed)
        result["damage"] = damage

    return result


def _handle_resolve_saving_throw(args: dict) -> dict:
    entity_id = args["entity_id"]
    ability   = args["ability"]
    dc        = int(args["dc"])
    advantage = args.get("advantage", "normal")

    cs        = _get_cs(entity_id)
    save      = rules.calc_saving_throw(cs.sheet, ability, advantage)
    success   = save["total"] >= dc

    return {
        "entity_id":   entity_id,
        "ability":     ability,
        "dc":          dc,
        "save_roll":   save,
        "success":     success,
    }


def _handle_resolve_ability_check(args: dict) -> dict:
    entity_id       = args["entity_id"]
    skill_or_ability = args["skill_or_ability"]
    dc              = int(args.get("dc", 0))
    advantage       = args.get("advantage", "normal")

    cs              = _get_cs(entity_id)
    check           = rules.calc_ability_check(cs.sheet, skill_or_ability, advantage)
    success         = check["total"] >= dc if dc else None

    return {
        "entity_id":       entity_id,
        "skill_or_ability": skill_or_ability,
        "dc":              dc or None,
        "check_roll":      check,
        "success":         success,
    }


def _handle_resolve_contested_check(args: dict) -> dict:
    entity_a_id = args["entity_a_id"]
    skill_a     = args["skill_a"]
    entity_b_id = args["entity_b_id"]
    skill_b     = args["skill_b"]
    adv_a       = args.get("advantage_a", "normal")
    adv_b       = args.get("advantage_b", "normal")

    cs_a   = _get_cs(entity_a_id)
    cs_b   = _get_cs(entity_b_id)
    roll_a = rules.calc_ability_check(cs_a.sheet, skill_a, adv_a)
    roll_b = rules.calc_ability_check(cs_b.sheet, skill_b, adv_b)

    # Ties go to the initiator (entity_a)
    winner = entity_a_id if roll_a["total"] >= roll_b["total"] else entity_b_id

    return {
        "entity_a":  {"entity_id": entity_a_id, "skill": skill_a, "roll": roll_a},
        "entity_b":  {"entity_id": entity_b_id, "skill": skill_b, "roll": roll_b},
        "winner":    winner,
    }


# ---------------------------------------------------------------------------
# State mutation
# ---------------------------------------------------------------------------

def _handle_apply_damage(args: dict) -> dict:
    entity_id   = args["entity_id"]
    amount      = int(args["amount"])
    damage_type = args.get("damage_type", "untyped")

    cs    = _get_cs(entity_id)
    sheet = cs.sheet

    adjusted = rules.apply_resistances(amount, damage_type, sheet)

    # Temp HP absorbs first
    temp_absorbed    = min(sheet.hp_temp, adjusted)
    remaining_damage = adjusted - temp_absorbed
    new_temp         = sheet.hp_temp - temp_absorbed
    new_hp           = max(0, sheet.hp_current - remaining_damage)

    updated_sheet = sheet.model_copy(update={
        "hp_current": new_hp,
        "hp_temp":    new_temp,
    })

    # Drop to 0 → UNCONSCIOUS
    newly_downed = False
    if new_hp == 0 and sheet.hp_current > 0:
        if Condition.UNCONSCIOUS not in updated_sheet.conditions:
            updated_sheet = updated_sheet.model_copy(update={
                "conditions": updated_sheet.conditions + [Condition.UNCONSCIOUS]
            })
        newly_downed = True

    db.save_character_state(entity_id, cs.model_copy(update={"sheet": updated_sheet}))

    return {
        "entity_id":               entity_id,
        "damage_raw":              amount,
        "damage_type":             damage_type,
        "adjusted_for_resistance": adjusted != amount,
        "adjusted_amount":         adjusted,
        "temp_hp_absorbed":        temp_absorbed,
        "damage_applied":          remaining_damage,
        "hp_before":               sheet.hp_current,
        "hp_after":                new_hp,
        "temp_hp_after":           new_temp,
        "downed":                  newly_downed,
    }


def _handle_apply_healing(args: dict) -> dict:
    entity_id = args["entity_id"]
    amount    = int(args["amount"])

    cs        = _get_cs(entity_id)
    sheet     = cs.sheet
    new_hp    = min(sheet.hp_max, sheet.hp_current + amount)

    # Remove UNCONSCIOUS if healed from 0
    conditions = list(sheet.conditions)
    if sheet.hp_current == 0 and new_hp > 0 and Condition.UNCONSCIOUS in conditions:
        conditions.remove(Condition.UNCONSCIOUS)

    updated_sheet = sheet.model_copy(update={
        "hp_current": new_hp,
        "conditions": conditions,
    })
    db.save_character_state(entity_id, cs.model_copy(update={"sheet": updated_sheet}))

    return {
        "entity_id": entity_id,
        "amount":    amount,
        "hp_before": sheet.hp_current,
        "hp_after":  new_hp,
        "revived":   sheet.hp_current == 0 and new_hp > 0,
    }


def _handle_apply_condition(args: dict) -> dict:
    entity_id      = args["entity_id"]
    condition_raw  = args["condition"]
    duration_turns = args.get("duration_turns")  # None = indefinite

    condition = Condition(condition_raw)
    cs        = _get_cs(entity_id)
    sheet     = cs.sheet

    if condition in sheet.conditions:
        return {"entity_id": entity_id, "condition": condition_raw, "already_applied": True}

    updated_sheet = sheet.model_copy(update={
        "conditions": sheet.conditions + [condition]
    })
    db.save_character_state(entity_id, cs.model_copy(update={"sheet": updated_sheet}))

    return {
        "entity_id":      entity_id,
        "condition":      condition_raw,
        "duration_turns": duration_turns,
        "applied":        True,
    }


def _handle_remove_condition(args: dict) -> dict:
    entity_id     = args["entity_id"]
    condition_raw = args["condition"]

    condition = Condition(condition_raw)
    cs        = _get_cs(entity_id)
    sheet     = cs.sheet

    if condition not in sheet.conditions:
        return {"entity_id": entity_id, "condition": condition_raw, "was_present": False}

    updated_conditions = [c for c in sheet.conditions if c != condition]
    updated_sheet      = sheet.model_copy(update={"conditions": updated_conditions})
    db.save_character_state(entity_id, cs.model_copy(update={"sheet": updated_sheet}))

    return {"entity_id": entity_id, "condition": condition_raw, "removed": True}


def _handle_apply_effect(args: dict) -> dict:
    entity_id      = args["entity_id"]
    name           = args["name"]
    description    = args.get("description", "")
    source         = args.get("source", "dm")
    duration_turns = args.get("duration_turns")
    concentration  = bool(args.get("concentration", False))

    cs     = _get_cs(entity_id)
    sheet  = cs.sheet
    eff_id = str(uuid.uuid4())[:8]

    effect = ActiveEffect(
        effect_id=eff_id,
        name=name,
        source=source,
        duration_turns=duration_turns,
        concentration=concentration,
        description=description,
    )

    # If concentration, drop any previous concentration effect
    effects = list(sheet.active_effects)
    if concentration:
        effects = [e for e in effects if not e.concentration]

    effects.append(effect)

    updates = {"active_effects": effects}
    if concentration:
        updates["concentration_effect_id"] = eff_id

    updated_sheet = sheet.model_copy(update=updates)
    db.save_character_state(entity_id, cs.model_copy(update={"sheet": updated_sheet}))

    return {"entity_id": entity_id, "effect_id": eff_id, "name": name, "applied": True}


def _handle_end_effect(args: dict) -> dict:
    entity_id = args["entity_id"]
    effect_id = args["effect_id"]

    cs    = _get_cs(entity_id)
    sheet = cs.sheet

    updated_effects = [e for e in sheet.active_effects if e.effect_id != effect_id]
    updates         = {"active_effects": updated_effects}

    if sheet.concentration_effect_id == effect_id:
        updates["concentration_effect_id"] = None

    updated_sheet = sheet.model_copy(update=updates)
    db.save_character_state(entity_id, cs.model_copy(update={"sheet": updated_sheet}))

    return {"entity_id": entity_id, "effect_id": effect_id, "ended": True}


def _handle_move_entity(args: dict) -> dict:
    entity_id = args["entity_id"]
    x         = int(args["x"])
    y         = int(args["y"])
    z         = int(args.get("z", 0))

    s = db.place_entity(entity_id, x, y, z)
    return {
        "entity_id": entity_id,
        "position":  {"x": x, "y": y, "z": z, "area_id": s["area_id"]},
    }


def _handle_spend_spell_slot(args: dict) -> dict:
    entity_id = args["entity_id"]
    level     = int(args["level"])

    cs    = _get_cs(entity_id)
    sheet = cs.sheet
    entry = sheet.spell_slots.get(level)

    if entry is None or entry.remaining <= 0:
        return {"entity_id": entity_id, "level": level, "success": False,
                "reason": "No spell slots of that level remaining."}

    updated_slots = {
        **sheet.spell_slots,
        level: entry.model_copy(update={"remaining": entry.remaining - 1}),
    }
    updated_sheet = sheet.model_copy(update={"spell_slots": updated_slots})
    db.save_character_state(entity_id, cs.model_copy(update={"sheet": updated_sheet}))

    return {
        "entity_id":   entity_id,
        "level":       level,
        "success":     True,
        "remaining":   entry.remaining - 1,
        "total":       entry.total,
    }


def _handle_restore_spell_slot(args: dict) -> dict:
    entity_id = args["entity_id"]
    level     = int(args["level"])

    cs    = _get_cs(entity_id)
    sheet = cs.sheet
    entry = sheet.spell_slots.get(level)

    if entry is None:
        return {"entity_id": entity_id, "level": level, "success": False,
                "reason": "No spell slots of that level on this character."}

    new_remaining = min(entry.total, entry.remaining + 1)
    updated_slots = {
        **sheet.spell_slots,
        level: entry.model_copy(update={"remaining": new_remaining}),
    }
    updated_sheet = sheet.model_copy(update={"spell_slots": updated_slots})
    db.save_character_state(entity_id, cs.model_copy(update={"sheet": updated_sheet}))

    return {
        "entity_id": entity_id,
        "level":     level,
        "success":   True,
        "remaining": new_remaining,
        "total":     entry.total,
    }


def _handle_spend_resource(args: dict) -> dict:
    entity_id     = args["entity_id"]
    resource_name = args["resource_name"].lower()
    amount        = int(args.get("amount", 1))

    cs    = _get_cs(entity_id)
    sheet = cs.sheet

    resources = list(sheet.class_resources)
    for i, r in enumerate(resources):
        if r.name.lower() == resource_name:
            if r.current < amount:
                return {"entity_id": entity_id, "resource": resource_name,
                        "success": False, "reason": "Insufficient resource."}
            resources[i] = r.model_copy(update={"current": r.current - amount})
            updated_sheet = sheet.model_copy(update={"class_resources": resources})
            db.save_character_state(entity_id, cs.model_copy(update={"sheet": updated_sheet}))
            return {
                "entity_id": entity_id,
                "resource":  resource_name,
                "success":   True,
                "remaining": r.current - amount,
                "max":       r.max,
            }

    return {"entity_id": entity_id, "resource": resource_name,
            "success": False, "reason": "Resource not found."}


def _handle_restore_resource(args: dict) -> dict:
    entity_id     = args["entity_id"]
    resource_name = args["resource_name"].lower()
    amount        = int(args.get("amount", 1))

    cs    = _get_cs(entity_id)
    sheet = cs.sheet

    resources = list(sheet.class_resources)
    for i, r in enumerate(resources):
        if r.name.lower() == resource_name:
            new_current   = min(r.max, r.current + amount)
            resources[i]  = r.model_copy(update={"current": new_current})
            updated_sheet = sheet.model_copy(update={"class_resources": resources})
            db.save_character_state(entity_id, cs.model_copy(update={"sheet": updated_sheet}))
            return {
                "entity_id": entity_id,
                "resource":  resource_name,
                "success":   True,
                "current":   new_current,
                "max":       r.max,
            }

    return {"entity_id": entity_id, "resource": resource_name,
            "success": False, "reason": "Resource not found."}


# ---------------------------------------------------------------------------
# Turn bookkeeping
# ---------------------------------------------------------------------------

def _handle_tick_turn_end(args: dict) -> dict:
    """
    Decrement duration_turns on all active effects for an entity.
    Removes effects whose duration reaches 0. Drops concentration if needed.
    Call at the end of the entity's turn.
    """
    entity_id = args["entity_id"]
    cs        = _get_cs(entity_id)
    sheet     = cs.sheet

    expired   = []
    remaining = []

    for eff in sheet.active_effects:
        if eff.duration_turns is None:
            remaining.append(eff)
            continue
        new_duration = eff.duration_turns - 1
        if new_duration <= 0:
            expired.append(eff.effect_id)
        else:
            remaining.append(eff.model_copy(update={"duration_turns": new_duration}))

    conc_id = sheet.concentration_effect_id
    if conc_id and conc_id in expired:
        conc_id = None

    updated_sheet = sheet.model_copy(update={
        "active_effects":          remaining,
        "concentration_effect_id": conc_id,
    })
    db.save_character_state(entity_id, cs.model_copy(update={"sheet": updated_sheet}))

    return {
        "entity_id":        entity_id,
        "effects_expired":  expired,
        "effects_remaining": [e.effect_id for e in remaining],
    }


def _handle_append_narrative(args: dict) -> dict:
    """Write narrative for this turn — a shared (third-person) and personal (second-person) version."""
    acting_entity_id = args["acting_entity_id"]
    shared_text      = args["shared_text"]
    personal_text    = args["personal_text"]
    result = db.append_narrative_segment(acting_entity_id, shared_text, personal_text)
    return {"status": "appended", "segment_id": result["id"]}


def _handle_apply_short_rest(args: dict) -> dict:
    """Execute a short rest for a character."""
    entity_id = args["entity_id"]
    hit_dice = int(args.get("hit_dice_to_spend", 0))
    result = db.short_rest(entity_id, hit_dice)
    if result is None:
        return {"error": f"Character {entity_id!r} not found"}
    return result


def _handle_apply_long_rest(args: dict) -> dict:
    """Execute a long rest for a character."""
    entity_id = args["entity_id"]
    result = db.long_rest(entity_id)
    if result is None:
        return {"error": f"Character {entity_id!r} not found"}
    return result


def _handle_set_player_location(args: dict) -> dict:
    """Update a player's location context fields."""
    entity_id = args["entity_id"]
    location_area = args["location_area"]
    location_sub = args.get("location_sub")
    found = db.update_player_location(entity_id, location_area, location_sub)
    if not found:
        return {"error": f"Character {entity_id!r} not found or has no turn context"}
    return {"status": "updated", "entity_id": entity_id, "location_area": location_area}


# ---------------------------------------------------------------------------
# Tool catalogue — tool definitions + handler bindings
# ---------------------------------------------------------------------------

_DM_TOOLS: list[dict] = [

    # ── Scene setup ──────────────────────────────────────────────────────────
    {
        "name": "init_scene",
        "description": "Initialise or reset the current scene (area). Call before placing entities.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "area_id":          {"type": "string", "default": "liminal"},
                "area_name":        {"type": "string", "default": "Liminal Space"},
                "area_description": {"type": "string", "default": ""},
                "light_level":      {"type": "string", "default": "bright",
                                     "description": "bright | dim | darkness"},
            },
            "required": [],
        },
        "handler": _handle_init_scene,
    },
    {
        "name": "place_entity",
        "description": "Place an already-registered entity at a grid coordinate (1 square = 5 ft).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string"},
                "x":         {"type": "integer", "default": 0},
                "y":         {"type": "integer", "default": 0},
                "z":         {"type": "integer", "default": 0},
            },
            "required": ["entity_id"],
        },
        "handler": _handle_place_entity,
    },

    # ── State inspection ──────────────────────────────────────────────────────
    {
        "name": "get_scene_state",
        "description": "Return the current scene: area info, all entity positions, HP, conditions, AC.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
        "handler": _handle_get_scene_state,
    },
    {
        "name": "get_character_state",
        "description": "Return the full runtime state of a single character.",
        "inputSchema": {
            "type": "object",
            "properties": {"entity_id": {"type": "string"}},
            "required": ["entity_id"],
        },
        "handler": _handle_get_character_state,
    },
    {
        "name": "get_turn_state",
        "description": "Return the current turn order, whose turn it is, and the realm-time tick counter.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
        "handler": _handle_get_turn_state,
    },

    # ── Turn order management ─────────────────────────────────────────────────
    {
        "name": "roll_initiative",
        "description": (
            "Roll initiative for the listed entities and set the turn order. "
            "Can be called at any time to re-order or add new participants."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Entities to roll initiative for.",
                },
            },
            "required": ["entity_ids"],
        },
        "handler": _handle_roll_initiative,
    },
    {
        "name": "set_turn_order",
        "description": "Manually assign the turn order without rolling. Useful for scripted scenes or DM-driven sequencing.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["entity_ids"],
        },
        "handler": _handle_set_turn_order,
    },
    {
        "name": "next_turn",
        "description": "Advance to the next entity in turn order. Resets that entity's action economy. Increments tick when the order wraps.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
        "handler": _handle_next_turn,
    },
    {
        "name": "add_to_turn_order",
        "description": "Insert an entity into the turn order at an optional position.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_id":   {"type": "string"},
                "after_index": {"type": "integer",
                               "description": "Insert after this index (0-based). Omit to append at end."},
            },
            "required": ["entity_id"],
        },
        "handler": _handle_add_to_turn_order,
    },
    {
        "name": "remove_from_turn_order",
        "description": "Remove an entity from the turn order (fled, incapacitated, left scene, etc.).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string"},
            },
            "required": ["entity_id"],
        },
        "handler": _handle_remove_from_turn_order,
    },

    # ── Roll resolution ───────────────────────────────────────────────────────
    {
        "name": "resolve_attack",
        "description": (
            "Roll an attack for the attacker against a target. Returns hit/miss, roll details, "
            "and damage rolled (if hit). Does NOT apply damage — call apply_damage separately."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "attacker_id":  {"type": "string"},
                "target_id":    {"type": "string"},
                "weapon_slot":  {"type": "string", "default": "main_hand",
                                 "description": "main_hand | off_hand | ranged"},
                "advantage":    {"type": "string", "default": "normal",
                                 "description": "normal | advantage | disadvantage"},
                "proficient":   {"type": "boolean", "default": True},
                "two_handed":   {"type": "boolean", "default": False},
            },
            "required": ["attacker_id", "target_id"],
        },
        "handler": _handle_resolve_attack,
    },
    {
        "name": "resolve_saving_throw",
        "description": "Roll a saving throw for an entity against a DC. Returns success/fail and full roll detail.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string"},
                "ability":   {"type": "string",
                              "description": "strength | dexterity | constitution | intelligence | wisdom | charisma"},
                "dc":        {"type": "integer"},
                "advantage": {"type": "string", "default": "normal",
                              "description": "normal | advantage | disadvantage"},
            },
            "required": ["entity_id", "ability", "dc"],
        },
        "handler": _handle_resolve_saving_throw,
    },
    {
        "name": "resolve_ability_check",
        "description": "Roll a skill or ability check for an entity, optionally against a DC.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_id":        {"type": "string"},
                "skill_or_ability": {"type": "string",
                                    "description": "skill name (e.g. 'athletics') or ability (e.g. 'strength')"},
                "dc":               {"type": "integer", "default": 0,
                                    "description": "0 = no DC, just return the roll."},
                "advantage":        {"type": "string", "default": "normal"},
            },
            "required": ["entity_id", "skill_or_ability"],
        },
        "handler": _handle_resolve_ability_check,
    },
    {
        "name": "resolve_contested_check",
        "description": "Roll opposing skill checks for two entities. Ties go to entity_a (the initiator).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_a_id": {"type": "string"},
                "skill_a":     {"type": "string"},
                "entity_b_id": {"type": "string"},
                "skill_b":     {"type": "string"},
                "advantage_a": {"type": "string", "default": "normal"},
                "advantage_b": {"type": "string", "default": "normal"},
            },
            "required": ["entity_a_id", "skill_a", "entity_b_id", "skill_b"],
        },
        "handler": _handle_resolve_contested_check,
    },

    # ── State mutation ────────────────────────────────────────────────────────
    {
        "name": "apply_damage",
        "description": (
            "Apply damage to an entity. Respects resistances, immunities, vulnerabilities. "
            "Absorbs temp HP first. Automatically applies UNCONSCIOUS at 0 HP."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_id":   {"type": "string"},
                "amount":      {"type": "integer"},
                "damage_type": {"type": "string", "default": "untyped",
                               "description": "slashing | piercing | bludgeoning | fire | cold | etc."},
            },
            "required": ["entity_id", "amount"],
        },
        "handler": _handle_apply_damage,
    },
    {
        "name": "apply_healing",
        "description": "Restore HP to an entity, capped at max HP. Removes UNCONSCIOUS if revived from 0.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string"},
                "amount":    {"type": "integer"},
            },
            "required": ["entity_id", "amount"],
        },
        "handler": _handle_apply_healing,
    },
    {
        "name": "apply_condition",
        "description": "Apply a condition to an entity. duration_turns is informational; use tick_turn_end to track.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_id":      {"type": "string"},
                "condition":      {"type": "string",
                                  "description": "blinded | charmed | deafened | frightened | grappled | "
                                                 "incapacitated | invisible | paralyzed | petrified | "
                                                 "poisoned | prone | restrained | stunned | unconscious | "
                                                 "raging | concentrating | hidden"},
                "duration_turns": {"type": "integer",
                                  "description": "Optional — purely informational for now."},
            },
            "required": ["entity_id", "condition"],
        },
        "handler": _handle_apply_condition,
    },
    {
        "name": "remove_condition",
        "description": "Remove a condition from an entity.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string"},
                "condition": {"type": "string"},
            },
            "required": ["entity_id", "condition"],
        },
        "handler": _handle_remove_condition,
    },
    {
        "name": "apply_effect",
        "description": (
            "Attach a named ActiveEffect to an entity (buff, debuff, environmental, etc.). "
            "Set concentration=true to mark it as a concentration effect (auto-drops previous concentration). "
            "duration_turns=null means indefinite."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_id":      {"type": "string"},
                "name":           {"type": "string"},
                "description":    {"type": "string", "default": ""},
                "source":         {"type": "string", "default": "dm"},
                "duration_turns": {"type": "integer",
                                  "description": "null = indefinite"},
                "concentration":  {"type": "boolean", "default": False},
            },
            "required": ["entity_id", "name"],
        },
        "handler": _handle_apply_effect,
    },
    {
        "name": "end_effect",
        "description": "Remove an active effect by effect_id. Clears concentration tracking if applicable.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string"},
                "effect_id": {"type": "string"},
            },
            "required": ["entity_id", "effect_id"],
        },
        "handler": _handle_end_effect,
    },
    {
        "name": "move_entity",
        "description": "DM-authority move: teleport or force-move an entity to a grid position.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string"},
                "x":         {"type": "integer"},
                "y":         {"type": "integer"},
                "z":         {"type": "integer", "default": 0},
            },
            "required": ["entity_id", "x", "y"],
        },
        "handler": _handle_move_entity,
    },
    {
        "name": "spend_spell_slot",
        "description": "Consume one spell slot of a given level from a character.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string"},
                "level":     {"type": "integer", "description": "1–9"},
            },
            "required": ["entity_id", "level"],
        },
        "handler": _handle_spend_spell_slot,
    },
    {
        "name": "restore_spell_slot",
        "description": "Restore one spell slot of a given level to a character.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string"},
                "level":     {"type": "integer"},
            },
            "required": ["entity_id", "level"],
        },
        "handler": _handle_restore_spell_slot,
    },
    {
        "name": "spend_resource",
        "description": "Spend one or more charges of a named class resource (e.g. 'rage', 'ki points').",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_id":     {"type": "string"},
                "resource_name": {"type": "string"},
                "amount":        {"type": "integer", "default": 1},
            },
            "required": ["entity_id", "resource_name"],
        },
        "handler": _handle_spend_resource,
    },
    {
        "name": "restore_resource",
        "description": "Restore one or more charges of a named class resource.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_id":     {"type": "string"},
                "resource_name": {"type": "string"},
                "amount":        {"type": "integer", "default": 1},
            },
            "required": ["entity_id", "resource_name"],
        },
        "handler": _handle_restore_resource,
    },

    # ── Turn bookkeeping ──────────────────────────────────────────────────────
    {
        "name": "tick_turn_end",
        "description": (
            "Decrement duration counters on all active effects for an entity. "
            "Removes effects that expire (duration reaches 0). Clears concentration if the "
            "concentration effect expires. Call at the end of each entity's turn."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"entity_id": {"type": "string"}},
            "required": ["entity_id"],
        },
        "handler": _handle_tick_turn_end,
    },

    # ── Resting ───────────────────────────────────────────────────────────────
    {
        "name": "apply_short_rest",
        "description": (
            "Execute a short rest for a character. Spends hit dice for HP recovery "
            "and recharges short-rest resources. Call this when the DM approves a "
            "player's short rest request."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_id":        {"type": "string"},
                "hit_dice_to_spend": {"type": "integer", "default": 0},
            },
            "required": ["entity_id"],
        },
        "handler": _handle_apply_short_rest,
    },
    {
        "name": "apply_long_rest",
        "description": (
            "Execute a long rest for a character. Restores full HP, all spell slots, "
            "class resources, clears conditions and effects, recovers half total hit dice. "
            "Call this when the DM approves a player's long rest request."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string"},
            },
            "required": ["entity_id"],
        },
        "handler": _handle_apply_long_rest,
    },

    # ── Narrative ─────────────────────────────────────────────────────────────
    {
        "name": "append_narrative",
        "description": (
            "Write the narrative for this turn. This is your FINAL tool call each turn — "
            "call it once after resolving all mechanics. "
            "shared_text: third-person prose describing what happened, shown to all players "
            "except the one who acted (e.g. 'Thorin swings his axe — the goblin staggers.'). "
            "personal_text: second-person prose addressed directly to the acting player, shown "
            "only to them (e.g. 'You swing your axe hard — the goblin staggers, eyes going wide.'). "
            "acting_entity_id: the entity_id of the player whose turn this is."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "acting_entity_id": {"type": "string"},
                "shared_text":      {"type": "string"},
                "personal_text":    {"type": "string"},
            },
            "required": ["acting_entity_id", "shared_text", "personal_text"],
        },
        "handler": _handle_append_narrative,
    },
    {
        "name": "set_player_location",
        "description": (
            "Update a player's location context (shown in their prompt as "
            "'You find yourself in ...'). Call when the scene physically changes areas."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_id":     {"type": "string"},
                "location_area": {"type": "string"},
                "location_sub":  {"type": "string"},
            },
            "required": ["entity_id", "location_area"],
        },
        "handler": _handle_set_player_location,
    },
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_dm_tools() -> list[dict]:
    """Return MCP-compatible tool definition dicts (no internal handler field)."""
    return [
        {k: v for k, v in tool.items() if k != "handler"}
        for tool in _DM_TOOLS
    ]


def execute_dm_tool(name: str, args: dict) -> dict:
    """Dispatch a DM tool call by name. Returns a result dict."""
    for tool in _DM_TOOLS:
        if tool["name"] == name:
            try:
                return tool["handler"](args)
            except KeyError as e:
                return {"error": f"Missing required argument: {e}"}
            except Exception as e:
                return {"error": str(e)}
    return {"error": f"Unknown DM tool: {name!r}"}
