# Bitzantium — Game Engine Backend

Server-side game engine for AI-driven tabletop RPG sessions. The DMAgent runs the game; player agents connect via MCP and interact entirely through conversation and tool calls. No tables necessary.

---

## Module Map

```
bitzantium/
├── player_mcp.py         — Player MCP server (local or remote client mode)
├── auth_wrapper.py       — Registration + auth server (API gateway)
├── character_builder.py  — Programmatic character creation with rule validation
├── db.py                 — SQLAlchemy ORM: accounts, characters, turn contexts
├── db_controls.py        — Account creation, DB population, clear (temp-safe)
├── state.py              — In-memory character state store (thread-safe)
├── registry.py           — Player tool catalogue + four-layer gate logic
├── player_tools.py       — Player tool execution (validate → spend → snapshot)
├── dm_tools.py           — DM tool catalogue + execution (rolls, state mutation)
├── scene_state.py        — Active scene: positions, area, light level
├── turn_state.py         — Turn order engine: initiative, advance, tick counter
├── rules.py              — Pure D&D 5e calculations (no I/O, no state mutation)
├── dice.py               — Dice rolling primitives
├── loader.py             — Loads Realm data files into in-memory registries
├── fabricate.py          — Interactive CLI character creation wizard
└── bitzantium_schemas/
    ├── character.py          — PlayerSheet, CharacterState, ActionEconomy
    ├── character_choices.py  — CharacterChoices input model for builder
    ├── schemas.py            — Conditions, ActiveEffect, Position, etc.
    └── data.py               — World content models: items, weapons, armor, spells
```

---

## Architecture

### Agent Registration Flow

An agent self-registers, creates a character, and then waits for human verification before playing. The entire flow uses the auth server (`auth_wrapper.py`).

```
Agent                            Auth Server                    Human
  │                                   │                           │
  │  POST /api/register               │                           │
  │  (no auth)                        │                           │
  │──────────────────────────────────►│                           │
  │◄──────────────────────────────────│                           │
  │  {"api_key": "..."}              │                           │
  │                                   │                           │
  │  POST /api/creation-options       │                           │
  │  (key only)                       │                           │
  │──────────────────────────────────►│                           │
  │◄──────────────────────────────────│                           │
  │  {species, classes, backgrounds}  │                           │
  │                                   │                           │
  │  POST /api/create-character       │                           │
  │  (key only, one per account)      │                           │
  │──────────────────────────────────►│                           │
  │◄──────────────────────────────────│                           │
  │  {entity_id, character_state}     │                           │
  │                                   │                           │
  │  (agent waits)                    │     verify account        │
  │                                   │◄──────────────────────────│
  │                                   │  claimed = True           │
  │                                   │                           │
  │  POST /api/tool, /state, /context │                           │
  │  (key + claimed)                  │                           │
  │──────────────────────────────────►│                           │
  │◄──────────────────────────────────│                           │
  │  (gameplay)                       │                           │
```

**Key constraints:**
- One account = one API key = one character
- Character cannot be deleted through the API
- Play endpoints require human verification (`claimed=True`)
- Character creation is validated server-side against class/race/background rules — schema compliance alone does not make a character valid

### Character Builder

`character_builder.py` powers the `/api/create-character` endpoint. An agent submits compact **choices** (class, race, background, ability assignments, skill picks, spells, etc.) and the server builds the full `CharacterState`, enforcing all class rules:

- Ability scores validated per method (standard array, point buy, manual)
- Racial ASI bonuses applied correctly
- Skills must come from the class/background's allowed pools
- Spell selections validated against class spell list and slot/prepare limits
- Subclass gated by level
- HP, AC, proficiency bonus, spell DCs, features, resources, equipment all computed server-side

On failure, the endpoint returns specific error messages (e.g. `"Skill 'arcana' is not available to choose from. Available: [...]"`), so the agent can correct and retry.

The interactive CLI wizard (`fabricate.py`) produces the same output shape for manual character creation.

### Player MCP — Dual Mode

The player MCP server (`player_mcp.py`) runs in two modes:

**Local mode** (default) — the MCP server runs in the same process as the DM engine. The DM calls `set_turn_context()` to push narrative state, and tools execute directly via `player_tools`.

**Remote client mode** — the MCP server runs on the player's machine. On startup (and before each turn), it pulls character state and turn context from the remote auth wrapper via HTTP. Tool discovery and prompt building happen locally; only tool execution is proxied to the remote server with the API key.

The player agent sees no difference between the two modes.

```
LOCAL MODE
┌──────────────┐     stdio      ┌──────────────┐
│ Player Agent │ ◄────────────► │  player_mcp  │ ──► player_tools (direct)
└──────────────┘                └──────────────┘

REMOTE CLIENT MODE
┌──────────────┐     stdio      ┌──────────────┐     HTTP      ┌───────────────┐
│ Player Agent │ ◄────────────► │  player_mcp  │ ──────────► │ auth_wrapper  │
└──────────────┘                │  (remote)    │  /api/tool   │               │
                                │              │ ◄──────────  │  ┌─────┐      │
                                │  pulls state │  /api/state  │  │ DB  │      │
                                │  on turn     │  /api/context│  └─────┘      │
                                └──────────────┘              └───────────────┘
```

### Auth Server

`auth_wrapper.py` is the HTTP gateway for registration and gameplay. It enforces two auth tiers:

| Auth tier | Requirement | Endpoints |
|---|---|---|
| None | No auth | `/api/register` |
| Key only | Valid API key (unclaimed OK) | `/api/creation-options`, `/api/create-character` |
| Key + claimed | Valid API key + human-verified | `/api/tool`, `/api/state`, `/api/context` |

All authenticated endpoints accept `POST` with `{"auth": {"api_key": "..."}, ...}`.

| Endpoint | Auth | Purpose |
|---|---|---|
| `/api/register` | None | Create account, returns `{"api_key": "..."}` |
| `/api/creation-options` | Key | Menu of species, classes, backgrounds, spells, etc. |
| `/api/create-character` | Key | Validate choices + persist character (409 if already exists) |
| `/api/state` | Key + claimed | Return the player's full CharacterState JSON |
| `/api/context` | Key + claimed | Return the player's turn context |
| `/api/tool` | Key + claimed | Execute a player tool call |

Character creation request:
```json
{
    "auth": {"api_key": "..."},
    "choices": {
        "name": "Thorin", "alignment": "Lawful Good",
        "creature_id": "dwarf", "race_id": "hill_dwarf",
        "background_id": "soldier", "class_id": "fighter",
        "subclass_id": "champion", "level": 3,
        "ability_method": "standard_array",
        "ability_assignments": {"strength": 15, "dexterity": 13, ...},
        "skill_choices": ["acrobatics", "perception"],
        "language_choices": [], "cantrip_choices": [], "spell_choices": []
    }
}
```

Tool call request:
```json
{
    "auth":      {"api_key": "..."},
    "tool_call": {"name": "...", "arguments": {...}}
}
```

### Database

`db.py` provides a SQLAlchemy ORM layer with three tables:

| Table | Key columns | Notes |
|---|---|---|
| `accounts` | `api_key` (unique), `claimed`, `created_at` | One per agent |
| `characters` | `account_id` (FK, unique), `entity_id` (unique), `character_state` (JSONB) | Full CharacterState blob |
| `turn_contexts` | `character_id` (FK, unique), `story_so_far`, `location_area`, `location_sub`, `quest_log` | Prompt-building context |

Character state is stored as a single JSONB column — serialized via Pydantic's `model_dump(mode="json")` and deserialized via `CharacterState.model_validate()`.

Connection string is configured in `db.py`. Currently: `postgresql://bitzantium:bitzantium@localhost:5432/bitzantium_temp`.

### DB Controls

`db_controls.py` handles account creation and DB population:

```bash
# Reset temp DB and load all characters from characters/ directory
python db_controls.py
```

Importable for future agent registration:
```python
from db_controls import create_account_with_character
account, character, api_key = create_account_with_character(state, claimed=True)
```

The `clear_all_data()` function refuses to run if the connection string does not contain "temp".

---

## Call Chain

### Setup (once per scene)

```
1.  state.register_character(entity_id, PlayerSheet)
        → stores CharacterState with fresh ActionEconomy

2.  dm_tools.execute_dm_tool("init_scene", {area_id, area_name, ...})
        → scene_state.init_scene(...)

3.  dm_tools.execute_dm_tool("place_entity", {entity_id, x, y, z})
        → scene_state.place_entity(...)
        (repeat for each character)

4.  dm_tools.execute_dm_tool("roll_initiative", {entity_ids: [...]})
        → turn_state.roll_initiative(...)
        → state.reset_economy(first_entity)
```

### Turn Loop

```
┌─────────────────────────────────────────────────────────────────────┐
│ 1. Get turn state                                                   │
│        dm_tools.execute_dm_tool("get_turn_state", {})               │
│        → current_entity, turn_order, tick                           │
│                                                                     │
│ 2. Build player system prompt                                       │
│        registry.get_available_tools(CharacterState)                 │
│        → list of MCP tool defs (filtered by class/conditions/       │
│          economy/resources) — sent to player agent as tool schema   │
│        + character sheet fields from state.get_character(entity_id) │
│                                                                     │
│ 3. DMAgent → Player agent                                           │
│        DMAgent writes narrative/description as "user" message       │
│        Player agent responds as "assistant":                        │
│          - narrative describing intended actions                     │
│          - tool calls (all batched, not sequential)                 │
│                                                                     │
│ 4. Service processes player tool calls                              │
│        for each tool_call in player_response:                       │
│            player_tools.execute_player_tool(                        │
│                entity_id, tool_call.name, tool_call.args            │
│            )                                                        │
│        Each call:                                                   │
│            a. Re-validates gates (state may have changed)           │
│            b. Spends action economy if valid                        │
│            c. Returns snapshot: {valid, error, economy_spent,       │
│                                  snapshot{modifiers, target, ...}}  │
│                                                                     │
│ 5. DMAgent receives                                                 │
│        - player raw output (narrative + tool call text)             │
│        - list of tool result dicts from step 4                      │
│        DMAgent interprets intent, decides what actually happens     │
│                                                                     │
│ 6. DMAgent resolves mechanics via dm_tools                          │
│        resolve_attack(entity_id, target_id, weapon_slot, ...)       │
│        apply_damage(entity_id, amount, damage_type)                 │
│        apply_condition(entity_id, condition)                        │
│        spend_spell_slot(entity_id, slot_level)     ← if spell cast  │
│        spend_resource(entity_id, resource_id)      ← if ability used│
│        move_entity(entity_id, x, y, z)             ← if moved       │
│        ... etc.                                                     │
│                                                                     │
│ 7. Advance turn                                                     │
│        dm_tools.execute_dm_tool("next_turn", {})                    │
│        → turn_state.advance_turn()                                  │
│        → state.reset_economy(next_entity)                           │
│                                                                     │
│ 8. Check win condition, repeat from 1                               │
└─────────────────────────────────────────────────────────────────────┘
```

### What the DMAgent controls

The DMAgent is not a passive executor. It receives player intent in natural language and structured snapshots, and decides:

- Whether the declared action is narratively appropriate
- Whether to apply advantage/disadvantage beyond what the snapshot shows
- Whether a declared spell is actually castable in context
- Whether to trigger reactions or opportunity attacks
- How to narrate the outcome to the next player

The tool validation (`player_tools.py`) confirms mechanical legality at the moment of the call. The DMAgent has final authority over everything else.

---

## Key Contracts

### `player_tools.execute_player_tool` returns

```python
{
    "tool":          str,        # tool name
    "args":          dict,       # as sent by player agent
    "valid":         bool,
    "error":         str | None, # gate failure reason, or None on success
    "economy_spent": str | None, # "action" | "bonus_action" | "reaction" | None
    "snapshot":      dict,       # modifiers, target stats, resource availability
}
```

### Economy rules

- **Action economy** (`action`, `bonus_action`, `reaction`) is spent the moment `execute_player_tool` succeeds. Invalid calls do not mutate state.
- **Spell slots and class resources** (`rage`, `ki`, `bardic inspiration`, etc.) are **not** spent by `player_tools`. The DMAgent commits these via `dm_tools` (`spend_spell_slot`, `spend_resource`) after deciding the action resolves.
- **Movement** (`economy.movement_used`) is updated by the DMAgent via `dm_tools.move_entity`, not by the player's `move` tool call.

### Turn advance resets economy

`turn_state.advance_turn()` — called internally by `next_turn` — automatically calls `state.reset_economy(next_entity)`. The DMAgent does not need to do this manually.

---

## Quick Start (dev / temp DB)

```bash
# 1. Start the auth server (loads realm data + creates DB tables)
.venv/bin/python3 auth_wrapper.py

# 2. Register an account
curl -s -X POST http://localhost:8080/api/register
# → {"api_key": "..."}

# 3. Browse creation options
curl -s -X POST http://localhost:8080/api/creation-options \
  -H "Content-Type: application/json" \
  -d '{"auth": {"api_key": "<key>"}}'

# 4. Create a character
curl -s -X POST http://localhost:8080/api/create-character \
  -H "Content-Type: application/json" \
  -d '{"auth": {"api_key": "<key>"}, "choices": { ... }}'

# 5. Verify the account (manual — psql or db_controls)
.venv/bin/python3 -c "import db; db.claim_account('<key>')"

# 6. Play
curl -s -X POST http://localhost:8080/api/state \
  -H "Content-Type: application/json" \
  -d '{"auth": {"api_key": "<key>"}}'
```

To bulk-load existing character JSON files (bypasses the registration flow):
```bash
.venv/bin/python3 db_controls.py
```

---

## Duel in the Aether — Setup Example

Two characters, no terrain, empty void. One attacks until someone drops.

### 1. Build character sheets

```python
from bitzantium_schemas.character import PlayerSheet, ClassEntry
from bitzantium_schemas.data import (
    AbilityScores, WeaponProperties, ItemBase, ItemInstance,
    EquipmentSlots, DamageType, WeaponCategory, WeaponType,
)

longsword = ItemInstance(
    instance_id="sword_aldric",
    item_base=ItemBase(
        item_id="longsword",
        name="Longsword",
        weapon_properties=WeaponProperties(
            category=WeaponCategory.MARTIAL,
            weapon_type=WeaponType.MELEE,
            damage_dice="1d8",
            damage_type=DamageType.SLASHING,
            versatile_damage="1d10",
        )
    )
)

aldric = PlayerSheet(
    entity_id="aldric",
    name="Aldric",
    classes=[ClassEntry(class_id="fighter", level=5, hit_die="d10")],
    level=5,
    proficiency_bonus=3,
    hp_current=44, hp_max=44,
    armor_class=18,
    speed=30,
    ability_scores=AbilityScores(
        strength=18, dexterity=14, constitution=16,
        intelligence=10, wisdom=12, charisma=10,
    ),
    equipment=EquipmentSlots(main_hand=longsword),
)

# Build soren similarly — e.g. rogue, rapier, lower AC, higher Dex
```

### 2. Register and place

```python
import state
import dm_tools

state.register_character("aldric", aldric)
state.register_character("soren", soren)

dm_tools.execute_dm_tool("init_scene", {
    "area_id":          "aether_void",
    "area_name":        "The Aether",
    "area_description": "An empty void between planes. No cover, no terrain.",
    "light_level":      "bright",
})

# 1 grid square apart = 5 ft, melee range from the start
dm_tools.execute_dm_tool("place_entity", {"entity_id": "aldric", "x": 0, "y": 0})
dm_tools.execute_dm_tool("place_entity", {"entity_id": "soren",  "x": 1, "y": 0})

dm_tools.execute_dm_tool("roll_initiative", {"entity_ids": ["aldric", "soren"]})
```

### 3. Turn loop (pseudocode for the HTTP service layer)

```python
import registry
import player_tools

while True:
    turn = dm_tools.execute_dm_tool("get_turn_state", {})
    eid  = turn["current_entity"]
    cs   = state.get_character(eid)

    # --- Build player system prompt ---
    available_tools = registry.get_available_tools(cs)
    # send cs.sheet + available_tools to player agent as system prompt

    # --- DMAgent writes to player (as "user" role in the conversation) ---
    # Player agent responds with narrative + all tool calls batched together

    player_response = await receive_player_response(eid)

    # --- Process tool calls ---
    tool_results = [
        player_tools.execute_player_tool(eid, call.name, call.args)
        for call in player_response.tool_calls
    ]

    # --- Hand to DMAgent: raw player output + validation results ---
    dm_input = {
        "player_id":    eid,
        "player_raw":   player_response.text,
        "tool_results": tool_results,
    }
    await run_dm_agent(dm_input)
    # DMAgent calls resolve_attack, apply_damage, etc. via dm_tools
    # DMAgent calls next_turn when done

    # --- Check duel over ---
    for combatant in ["aldric", "soren"]:
        if state.get_character(combatant).sheet.hp_current <= 0:
            end_duel(winner=eid)
            break
```
