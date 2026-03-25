"""
registry.py

Pure logic, no I/O. Given a PlayerSheet, returns the list of MCP tool definitions
that are currently valid for that character.

Gating runs in three layers:
  1. Class       — only expose tools the character's class can use
  2. Conditions  — remove tools blocked by current conditions
  3. Economy     — remove tools whose action cost is already spent this turn

Tool definitions follow the MCP tool schema:
  {
    "name": str,
    "description": str,
    "inputSchema": { "type": "object", "properties": {...}, "required": [...] }
  }
"""

from bitzantium_schemas.character import CharacterState
from bitzantium_schemas.schemas import Condition

# ---------------------------------------------------------------------------
# Master tool catalogue
# Each entry includes:
#   - definition: the MCP tool definition dict
#   - action_cost: "action" | "bonus_action" | "reaction" | "free"
#   - classes: set of class names that can use this tool, or None = universal
#   - blocked_by: set of Conditions that prevent this tool
# ---------------------------------------------------------------------------

_TOOLS: list[dict] = [

    # -----------------------------------------------------------------------
    # UNIVERSAL — all classes
    # -----------------------------------------------------------------------
    {
        "name": "move",
        "description": "Move to a location or grid coordinate. movement_type: walk | sprint | crawl | swim | climb.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "destination": {"type": "string"},
                "movement_type": {"type": "string", "default": "walk"}
            },
            "required": ["destination"]
        },
        "action_cost": "free",  # movement is its own resource
        "classes": None,
        "blocked_by": {Condition.INCAPACITATED, Condition.PARALYZED,
                       Condition.PETRIFIED, Condition.STUNNED, Condition.UNCONSCIOUS}
    },
    {
        "name": "attack",
        "description": "Make a weapon attack against a target. weapon_slot: main_hand | off_hand | ranged.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target_id": {"type": "string"},
                "weapon_slot": {"type": "string", "default": "main_hand"}
            },
            "required": ["target_id"]
        },
        "action_cost": "action",
        "classes": None,
        "blocked_by": {Condition.INCAPACITATED, Condition.PARALYZED,
                       Condition.PETRIFIED, Condition.STUNNED, Condition.UNCONSCIOUS}
    },
    {
        "name": "dodge",
        "description": "Take the Dodge action. Attacks against you have disadvantage until your next turn.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
        "action_cost": "action",
        "classes": None,
        "blocked_by": {Condition.INCAPACITATED, Condition.PARALYZED,
                       Condition.PETRIFIED, Condition.STUNNED, Condition.UNCONSCIOUS}
    },
    {
        "name": "dash",
        "description": "Double your movement speed for this turn.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
        "action_cost": "action",
        "classes": None,
        "blocked_by": {Condition.INCAPACITATED, Condition.PARALYZED,
                       Condition.PETRIFIED, Condition.STUNNED, Condition.UNCONSCIOUS}
    },
    {
        "name": "disengage",
        "description": "Prevent opportunity attacks when leaving melee range this turn.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
        "action_cost": "action",
        "classes": None,
        "blocked_by": {Condition.INCAPACITATED, Condition.PARALYZED,
                       Condition.PETRIFIED, Condition.STUNNED, Condition.UNCONSCIOUS}
    },
    {
        "name": "hide",
        "description": "Attempt to hide. Rolls Stealth against enemy passive perception.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "location_hint": {"type": "string", "default": ""}
            },
            "required": []
        },
        "action_cost": "action",
        "classes": None,
        "blocked_by": {Condition.INCAPACITATED, Condition.PARALYZED,
                       Condition.PETRIFIED, Condition.STUNNED, Condition.UNCONSCIOUS}
    },
    {
        "name": "help_ally",
        "description": "Grant an ally advantage on their next attack or ability check.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ally_id": {"type": "string"},
                "action_type": {"type": "string", "description": "attack | ability_check"}
            },
            "required": ["ally_id", "action_type"]
        },
        "action_cost": "action",
        "classes": None,
        "blocked_by": {Condition.INCAPACITATED, Condition.PARALYZED,
                       Condition.PETRIFIED, Condition.STUNNED, Condition.UNCONSCIOUS}
    },
    {
        "name": "shove",
        "description": "Shove a creature prone or push them back. Opposed Strength check.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target_id": {"type": "string"},
                "intent": {"type": "string", "default": "prone", "description": "prone | push_back"}
            },
            "required": ["target_id"]
        },
        "action_cost": "action",
        "classes": None,
        "blocked_by": {Condition.INCAPACITATED, Condition.PARALYZED,
                       Condition.PETRIFIED, Condition.STUNNED, Condition.UNCONSCIOUS}
    },
    {
        "name": "grapple",
        "description": "Attempt to grapple a creature. Opposed Athletics check.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target_id": {"type": "string"}
            },
            "required": ["target_id"]
        },
        "action_cost": "action",
        "classes": None,
        "blocked_by": {Condition.INCAPACITATED, Condition.PARALYZED,
                       Condition.PETRIFIED, Condition.STUNNED, Condition.UNCONSCIOUS}
    },
    {
        "name": "throw",
        "description": "Throw an item or improvised weapon at a target or location.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "item_id": {"type": "string"},
                "target": {"type": "string"}
            },
            "required": ["item_id", "target"]
        },
        "action_cost": "action",
        "classes": None,
        "blocked_by": {Condition.INCAPACITATED, Condition.PARALYZED,
                       Condition.PETRIFIED, Condition.STUNNED, Condition.UNCONSCIOUS}
    },

    # -----------------------------------------------------------------------
    # INTERACTION & EXPLORATION
    # -----------------------------------------------------------------------
    {
        "name": "examine",
        "description": "Inspect a creature, object, or location for visible properties and details.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target_id": {"type": "string"}
            },
            "required": ["target_id"]
        },
        "action_cost": "free",
        "classes": None,
        "blocked_by": {Condition.BLINDED, Condition.UNCONSCIOUS}
    },
    {
        "name": "look_around",
        "description": "Get a description of your immediate surroundings, visible entities, exits, and items.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
        "action_cost": "free",
        "classes": None,
        "blocked_by": {Condition.BLINDED, Condition.UNCONSCIOUS}
    },
    {
        "name": "interact_with_object",
        "description": "Interact with an environmental object. interaction: open | close | pull | push | activate | read | sit.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "object_id": {"type": "string"},
                "interaction": {"type": "string"}
            },
            "required": ["object_id", "interaction"]
        },
        "action_cost": "free",  # simple interactions are free; complex ones cost an action (DM decides)
        "classes": None,
        "blocked_by": {Condition.INCAPACITATED, Condition.PARALYZED,
                       Condition.PETRIFIED, Condition.STUNNED, Condition.UNCONSCIOUS}
    },
    {
        "name": "pick_lock",
        "description": "Attempt to pick a lock with thieves' tools. Requires proficiency.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "object_id": {"type": "string"}
            },
            "required": ["object_id"]
        },
        "action_cost": "action",
        "classes": None,
        "blocked_by": {Condition.INCAPACITATED, Condition.PARALYZED,
                       Condition.PETRIFIED, Condition.STUNNED, Condition.UNCONSCIOUS}
    },
    {
        "name": "disarm_trap",
        "description": "Attempt to disarm a detected trap using Thieves' Tools or Arcana.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "trap_id": {"type": "string"}
            },
            "required": ["trap_id"]
        },
        "action_cost": "action",
        "classes": None,
        "blocked_by": {Condition.INCAPACITATED, Condition.PARALYZED,
                       Condition.PETRIFIED, Condition.STUNNED, Condition.UNCONSCIOUS}
    },
    {
        "name": "search",
        "description": "Actively search an area for hidden objects, traps, or creatures.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "area": {"type": "string"}
            },
            "required": ["area"]
        },
        "action_cost": "action",
        "classes": None,
        "blocked_by": {Condition.BLINDED, Condition.INCAPACITATED,
                       Condition.UNCONSCIOUS}
    },

    # -----------------------------------------------------------------------
    # INVENTORY & ITEMS
    # -----------------------------------------------------------------------
    {
        "name": "get_inventory",
        "description": "Return current inventory, equipped items, and carrying capacity.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
        "action_cost": "free",
        "classes": None,
        "blocked_by": set()
    },
    {
        "name": "equip_item",
        "description": "Equip an item to a slot. slot: main_hand | off_hand | armor | helmet | boots | gloves | ring | amulet | back.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "item_id": {"type": "string"},
                "slot": {"type": "string"}
            },
            "required": ["item_id", "slot"]
        },
        "action_cost": "free",
        "classes": None,
        "blocked_by": {Condition.INCAPACITATED, Condition.PARALYZED,
                       Condition.PETRIFIED, Condition.STUNNED, Condition.UNCONSCIOUS}
    },
    {
        "name": "unequip_item",
        "description": "Unequip an item from a slot back to inventory.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "slot": {"type": "string"}
            },
            "required": ["slot"]
        },
        "action_cost": "free",
        "classes": None,
        "blocked_by": {Condition.INCAPACITATED, Condition.PARALYZED,
                       Condition.PETRIFIED, Condition.STUNNED, Condition.UNCONSCIOUS}
    },
    {
        "name": "use_item",
        "description": "Use a consumable or activatable item such as a health potion or scroll.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "item_id": {"type": "string"},
                "target_id": {"type": "string", "default": "self"}
            },
            "required": ["item_id"]
        },
        "action_cost": "action",
        "classes": None,
        "blocked_by": {Condition.INCAPACITATED, Condition.PARALYZED,
                       Condition.PETRIFIED, Condition.STUNNED, Condition.UNCONSCIOUS}
    },
    {
        "name": "pick_up",
        "description": "Pick up an item from the ground.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "item_id": {"type": "string"}
            },
            "required": ["item_id"]
        },
        "action_cost": "free",
        "classes": None,
        "blocked_by": {Condition.INCAPACITATED, Condition.PARALYZED,
                       Condition.PETRIFIED, Condition.STUNNED, Condition.UNCONSCIOUS}
    },
    {
        "name": "drop_item",
        "description": "Drop an item at your current location.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "item_id": {"type": "string"},
                "quantity": {"type": "integer", "default": 1}
            },
            "required": ["item_id"]
        },
        "action_cost": "free",
        "classes": None,
        "blocked_by": set()
    },
    {
        "name": "inspect_item",
        "description": "Get detailed stats, lore, and magical properties of an item.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "item_id": {"type": "string"}
            },
            "required": ["item_id"]
        },
        "action_cost": "free",
        "classes": None,
        "blocked_by": set()
    },

    # -----------------------------------------------------------------------
    # SOCIAL & DIALOGUE
    # -----------------------------------------------------------------------
    {
        "name": "talk_to",
        "description": "Initiate dialogue with an NPC.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "npc_id": {"type": "string"}
            },
            "required": ["npc_id"]
        },
        "action_cost": "action",
        "classes": None,
        "blocked_by": {Condition.INCAPACITATED, Condition.PARALYZED,
                       Condition.PETRIFIED, Condition.STUNNED, Condition.UNCONSCIOUS}
    },
    {
        "name": "say",
        "description": "Select a dialogue option during an active conversation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "dialogue_option_id": {"type": "string"}
            },
            "required": ["dialogue_option_id"]
        },
        "action_cost": "free",
        "classes": None,
        "blocked_by": {Condition.INCAPACITATED, Condition.UNCONSCIOUS}
    },
    {
        "name": "persuade",
        "description": "Attempt a Charisma (Persuasion) check to influence an NPC.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "npc_id": {"type": "string"},
                "argument": {"type": "string"}
            },
            "required": ["npc_id", "argument"]
        },
        "action_cost": "action",
        "classes": None,
        "blocked_by": {Condition.INCAPACITATED, Condition.UNCONSCIOUS}
    },
    {
        "name": "intimidate",
        "description": "Attempt a Charisma (Intimidation) check against an NPC.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "npc_id": {"type": "string"},
                "argument": {"type": "string"}
            },
            "required": ["npc_id", "argument"]
        },
        "action_cost": "action",
        "classes": None,
        "blocked_by": {Condition.INCAPACITATED, Condition.UNCONSCIOUS}
    },
    {
        "name": "deceive",
        "description": "Attempt a Charisma (Deception) check against an NPC.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "npc_id": {"type": "string"},
                "argument": {"type": "string"}
            },
            "required": ["npc_id", "argument"]
        },
        "action_cost": "action",
        "classes": None,
        "blocked_by": {Condition.INCAPACITATED, Condition.UNCONSCIOUS}
    },
    {
        "name": "trade",
        "description": "Propose a trade with an NPC. offer and request are {item_id: quantity} dicts. For currency use keys: 'cp', 'sp', 'ep', 'gp', 'pp'.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "npc_id": {"type": "string"},
                "offer": {"type": "object"},
                "request": {"type": "object"}
            },
            "required": ["npc_id", "offer", "request"]
        },
        "action_cost": "action",
        "classes": None,
        "blocked_by": {Condition.INCAPACITATED, Condition.UNCONSCIOUS}
    },

    # -----------------------------------------------------------------------
    # REST
    # -----------------------------------------------------------------------
    {
        "name": "short_rest",
        "description": "Take a short rest (1 hour). Spend hit dice to recover HP.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hit_dice_to_spend": {"type": "integer", "default": 0}
            },
            "required": []
        },
        "action_cost": "free",
        "classes": None,
        "blocked_by": {Condition.INCAPACITATED, Condition.UNCONSCIOUS}
    },
    {
        "name": "long_rest",
        "description": "Take a long rest (8 hours). Full HP and resource recovery. Only available in safe locations.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
        "action_cost": "free",
        "classes": None,
        "blocked_by": {Condition.INCAPACITATED, Condition.UNCONSCIOUS}
    },

    # -----------------------------------------------------------------------
    # SPELLCASTING — caster classes only
    # -----------------------------------------------------------------------
    {
        "name": "get_spells",
        "description": "Return known and prepared spells, available spell slots, and cantrips.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
        "action_cost": "free",
        "classes": {"wizard", "sorcerer", "warlock", "cleric", "druid",
                    "bard", "paladin", "ranger", "artificer"},
        "blocked_by": set()
    },
    {
        "name": "cast_spell",
        "description": (
            "Cast a spell. target: entity_id | grid_coordinate | 'self' | 'area:<description>'. "
            "slot_level: 0 for cantrips, otherwise the slot level to use (upcast if higher than spell's base)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "spell_id": {"type": "string"},
                "target": {"type": "string"},
                "slot_level": {"type": "integer", "default": 0}
            },
            "required": ["spell_id", "target"]
        },
        "action_cost": "action",  # most spells; bonus action spells handled DM-side
        "classes": {"wizard", "sorcerer", "warlock", "cleric", "druid",
                    "bard", "paladin", "ranger", "artificer"},
        "blocked_by": {Condition.INCAPACITATED, Condition.PARALYZED,
                       Condition.PETRIFIED, Condition.STUNNED, Condition.UNCONSCIOUS}
    },
    {
        "name": "concentrate",
        "description": "Manage concentration on an active spell. action: drop | status.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "drop | status"}
            },
            "required": ["action"]
        },
        "action_cost": "free",
        "classes": {"wizard", "sorcerer", "warlock", "cleric", "druid",
                    "bard", "paladin", "ranger", "artificer"},
        "blocked_by": set()
    },

    # -----------------------------------------------------------------------
    # CLASS ABILITIES
    # -----------------------------------------------------------------------

    # Barbarian
    {
        "name": "rage",
        "description": "Enter a rage. Bonus damage on Strength attacks, resistance to physical damage.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
        "action_cost": "bonus_action",
        "classes": {"barbarian"},
        "blocked_by": {Condition.INCAPACITATED, Condition.UNCONSCIOUS},
        "requires_resource": "rage"
    },
    {
        "name": "reckless_attack",
        "description": "Attack with advantage on all attacks this turn, but grant attackers advantage against you until your next turn.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
        "action_cost": "free",  # modifier on the attack action, not a separate action
        "classes": {"barbarian"},
        "blocked_by": {Condition.INCAPACITATED, Condition.UNCONSCIOUS}
    },

    # Rogue
    {
        "name": "sneak_attack",
        "description": "Make a Sneak Attack if conditions are met (advantage or an ally adjacent to target).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target_id": {"type": "string"}
            },
            "required": ["target_id"]
        },
        "action_cost": "action",
        "classes": {"rogue"},
        "blocked_by": {Condition.INCAPACITATED, Condition.PARALYZED,
                       Condition.PETRIFIED, Condition.STUNNED, Condition.UNCONSCIOUS}
    },
    {
        "name": "cunning_action",
        "description": "Use Cunning Action as a bonus action. action: dash | disengage | hide.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "dash | disengage | hide"}
            },
            "required": ["action"]
        },
        "action_cost": "bonus_action",
        "classes": {"rogue"},
        "blocked_by": {Condition.INCAPACITATED, Condition.UNCONSCIOUS}
    },

    # Fighter
    {
        "name": "second_wind",
        "description": "Recover 1d10 + Fighter level HP as a bonus action. Recharges on short rest.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
        "action_cost": "bonus_action",
        "classes": {"fighter"},
        "blocked_by": {Condition.INCAPACITATED, Condition.UNCONSCIOUS},
        "requires_resource": "second wind"
    },
    {
        "name": "action_surge",
        "description": "Gain an additional action this turn. Recharges on short rest.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
        "action_cost": "free",  # grants an action, doesn't cost one
        "classes": {"fighter"},
        "blocked_by": {Condition.INCAPACITATED, Condition.UNCONSCIOUS},
        "requires_resource": "action surge"
    },

    # Paladin
    {
        "name": "divine_smite",
        "description": "Expend a spell slot after a hit to add radiant damage.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target_id": {"type": "string"},
                "slot_level": {"type": "integer"}
            },
            "required": ["target_id", "slot_level"]
        },
        "action_cost": "free",  # triggered after a hit, not a separate action
        "classes": {"paladin"},
        "blocked_by": {Condition.INCAPACITATED, Condition.UNCONSCIOUS},
        "requires_resource": None  # gated by spell slots, handled by DM
    },
    {
        "name": "lay_on_hands",
        "description": "Restore HP from a pool of 5 × Paladin level. Can also cure disease or poison.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target_id": {"type": "string"},
                "hp_amount": {"type": "integer"}
            },
            "required": ["target_id", "hp_amount"]
        },
        "action_cost": "action",
        "classes": {"paladin"},
        "blocked_by": {Condition.INCAPACITATED, Condition.UNCONSCIOUS},
        "requires_resource": "lay on hands"
    },

    # Druid
    {
        "name": "wild_shape",
        "description": "Transform into a beast. Available forms depend on level and environment.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "creature_form": {"type": "string"}
            },
            "required": ["creature_form"]
        },
        "action_cost": "action",
        "classes": {"druid"},
        "blocked_by": {Condition.INCAPACITATED, Condition.UNCONSCIOUS}
    },

    # Bard
    {
        "name": "bardic_inspiration",
        "description": "Grant an ally a Bardic Inspiration die to add to one of their rolls.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ally_id": {"type": "string"}
            },
            "required": ["ally_id"]
        },
        "action_cost": "bonus_action",
        "classes": {"bard"},
        "blocked_by": {Condition.INCAPACITATED, Condition.UNCONSCIOUS},
        "requires_resource": "bardic inspiration"
    },

    # Ranger
    {
        "name": "hunters_mark",
        "description": "Mark a target to deal an extra 1d6 on each hit. Requires concentration.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target_id": {"type": "string"}
            },
            "required": ["target_id"]
        },
        "action_cost": "bonus_action",
        "classes": {"ranger"},
        "blocked_by": {Condition.INCAPACITATED, Condition.UNCONSCIOUS}
    },

    # Monk
    {
        "name": "ki_ability",
        "description": (
            "Spend ki points to activate a monk ability. "
            "ability: flurry_of_blows | patient_defense | step_of_the_wind | stunning_strike."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ability": {"type": "string"},
                "target_id": {"type": "string", "default": ""}
            },
            "required": ["ability"]
        },
        "action_cost": "bonus_action",
        "classes": {"monk"},
        "blocked_by": {Condition.INCAPACITATED, Condition.UNCONSCIOUS},
        "requires_resource": "ki points"
    },
]


# ---------------------------------------------------------------------------
# Main gating function
# ---------------------------------------------------------------------------

def get_available_tools(state: CharacterState) -> list[dict]:
    """
    Given a CharacterState, return the filtered list of MCP tool definition
    dicts the character may use this turn.

    Gating layers:
      1. Class       — only expose tools valid for any of the character's classes
      2. Conditions  — remove tools blocked by current conditions
      3. Economy     — remove tools whose action cost is already spent this turn
      4. Resources   — remove tools whose named class resource is depleted
    """
    class_names = {c.class_id.lower() for c in state.sheet.classes}
    active_conditions = set(state.sheet.conditions)
    economy = state.economy

    # Build a normalised name -> current map for class resources (Layer 4 lookup)
    resource_current: dict[str, int] = {
        r.name.lower(): r.current
        for r in state.sheet.class_resources
    }

    available = []

    for tool in _TOOLS:
        # --- Layer 1: Class gate ---
        if tool["classes"] is not None and not (tool["classes"] & class_names):
            continue

        # --- Layer 2: Condition gate ---
        if tool["blocked_by"] & active_conditions:
            continue

        # --- Layer 3: Action economy gate ---
        cost = tool["action_cost"]
        if cost == "action" and economy.action_spent:
            continue
        if cost == "bonus_action" and economy.bonus_action_spent:
            continue
        if cost == "reaction" and economy.reaction_spent:
            continue

        # --- Layer 4: Resource gate ---
        required = tool.get("requires_resource")
        if required is not None:
            if resource_current.get(required, 0) <= 0:
                continue

        # Strip internal fields before returning to MCP
        available.append({
            "name": tool["name"],
            "description": tool["description"],
            "inputSchema": tool["inputSchema"],
        })

    return available
