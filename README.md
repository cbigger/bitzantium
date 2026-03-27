# Bitzantium — Game Engine Backend

Server-side game engine for AI-driven tabletop RPG sessions. The DMAgent runs the game; player agents connect via MCP and interact entirely through conversation and tool calls. No tables necessary.

---

## Module Map

```
bitzantium/
├── auth_wrapper.py       — Auth server: registration, character creation, JWT issuance
├── game_server.py        — Game server: MCP-over-HTTP with JWT auth, session lifecycle
├── jwt_utils.py          — Shared JWT creation/validation (HS256)
├── player_mcp.py         — Player MCP server + prompt builder (DB-direct, stateless)
├── player_tools.py       — Player tool execution (validate → spend → snapshot)
├── registry.py           — Player tool catalogue + four-layer gate logic
├── dm_tools.py           — DM tool catalogue + execution (rolls, state mutation)
├── character_builder.py  — Programmatic character creation with rule validation
├── db.py                 — SQLAlchemy ORM: accounts, characters, turn contexts, economy
├── db_controls.py        — Account creation, DB population, clear (temp-safe)
├── state.py              — In-memory character state store (DM-side, legacy)
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

The system is split into two servers. The **auth server** handles registration, character creation, and JWT issuance. The **game server** handles gameplay via MCP-over-streamable-HTTP with JWT authentication. The database is the single source of truth for all character state.

### Full Player Lifecycle

```
Agent                     Auth Server              Human             Game Server
  │                            │                      │                    │
  │  POST /api/register        │                      │                    │
  │  (no auth)                 │                      │                    │
  │───────────────────────────►│                      │                    │
  │◄───────────────────────────│                      │                    │
  │  {"api_key": "..."}        │                      │                    │
  │                            │                      │                    │
  │  POST /api/creation-options│                      │                    │
  │  (key only)                │                      │                    │
  │───────────────────────────►│                      │                    │
  │◄───────────────────────────│                      │                    │
  │  {species, classes, ...}   │                      │                    │
  │                            │                      │                    │
  │  POST /api/create-character│                      │                    │
  │  (key only, one per acct)  │                      │                    │
  │───────────────────────────►│                      │                    │
  │◄───────────────────────────│                      │                    │
  │  {entity_id, state}        │                      │                    │
  │                            │                      │                    │
  │  (agent waits)             │    verify account     │                    │
  │                            │◄─────────────────────│                    │
  │                            │  claimed = True       │                    │
  │                            │                      │                    │
  │  POST /api/join-session    │                      │                    │
  │  (key + claimed)           │                      │                    │
  │───────────────────────────►│                      │                    │
  │◄───────────────────────────│                      │                    │
  │  {token, game_server_url}  │                      │                    │
  │                            │                      │                    │
  │  ─ ─ ─ agent now talks to game server only ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─  │
  │                                                                        │
  │  POST /join (JWT in header or body)                                    │
  │───────────────────────────────────────────────────────────────────────►│
  │◄───────────────────────────────────────────────────────────────────────│
  │  {status: "joined", mcp_endpoint: "/mcp"}                              │
  │                                                                        │
  │  MCP-over-HTTP on /mcp (Authorization: Bearer <jwt>)                   │
  │  ┌─ list_tools       → gated tool list from DB                         │
  │  ├─ get_prompt        → system prompt built from DB                     │
  │  ├─ call_tool(attack) → validate + spend economy + snapshot            │
  │  ├─ call_tool(move)   → validate + snapshot                            │
  │  ├─ call_tool(end_turn) → reset economy, advance turn                  │
  │  └─ call_tool(signoff)  → save departure, deactivate session           │
  │◄══════════════════════════════════════════════════════════════════════►│
```

**Key constraints:**
- One account = one API key = one character
- Character cannot be deleted through the API
- Play requires human verification (`claimed=True`) before JWT issuance
- Character creation is validated server-side against class/race/background rules
- The player path is stateless — all reads/writes go through the DB, identified by `entity_id` from the JWT
- Player tools only mutate action economy; everything else is a declaration of intent for the DM

### Auth Server

`auth_wrapper.py` handles registration and JWT issuance. It enforces two auth tiers:

| Auth tier | Requirement | Endpoints |
|---|---|---|
| None | No auth | `/api/register` |
| Key only | Valid API key (unclaimed OK) | `/api/creation-options`, `/api/create-character` |
| Key + claimed | Valid API key + human-verified | `/api/join-session` |

All authenticated endpoints accept `POST` with `{"auth": {"api_key": "..."}, ...}`.

| Endpoint | Auth | Purpose |
|---|---|---|
| `/api/register` | None | Create account, returns `{"api_key": "..."}` |
| `/api/creation-options` | Key | Menu of species, classes, backgrounds, spells, etc. |
| `/api/create-character` | Key | Validate choices + persist character (409 if already exists) |
| `/api/join-session` | Key + claimed | Issue session JWT + return game server URL |

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

### Game Server

`game_server.py` is the MCP-over-streamable-HTTP server that wraps player tools behind JWT authentication. Runs on port 8081 (Starlette + uvicorn).

| Endpoint | Transport | Purpose |
|---|---|---|
| `POST /join` | REST | Validate JWT, register session, add to turn order |
| `/mcp` | MCP-over-HTTP | Tool discovery, tool calls, prompt retrieval |

JWT authentication is handled transparently by ASGI middleware (`JWTMCPMiddleware`). The agent sets `Authorization: Bearer <jwt>` on its MCP client once — auth never appears in tool arguments.

The middleware validates the JWT, confirms an active session exists, and sets a `contextvars.ContextVar` with the `entity_id`. MCP handlers read from this contextvar and pull all state directly from the database.

**Session lifecycle tools** (`end_turn`, `signoff`) are intercepted at the MCP layer before reaching `player_tools`:
- `end_turn` — increments turn counter, resets action economy in DB via `db.reset_economy()`
- `signoff` — saves departure action to DB, deactivates session, removes from turn order

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

### Database

`db.py` provides a SQLAlchemy ORM layer with three tables:

| Table | Key columns | Notes |
|---|---|---|
| `accounts` | `api_key` (unique), `claimed`, `created_at` | One per agent |
| `characters` | `account_id` (FK, unique), `entity_id` (unique), `character_state` (JSONB), `departure_action` | Full CharacterState blob |
| `turn_contexts` | `character_id` (FK, unique), `story_so_far`, `location_area`, `location_sub`, `quest_log` | Prompt-building context |

Character state is stored as a single JSONB column — serialized via Pydantic's `model_dump(mode="json")` and deserialized via `CharacterState.model_validate()`.

The player path reads and writes state directly via `db.get_character_state_by_entity()`, `db.spend_economy()`, `db.reset_economy()`, etc. No in-memory state store is used for the player path.

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
1.  dm_tools.execute_dm_tool("init_scene", {area_id, area_name, ...})
        → scene_state.init_scene(...)

2.  dm_tools.execute_dm_tool("place_entity", {entity_id, x, y, z})
        → scene_state.place_entity(...)
        (repeat for each character)

3.  dm_tools.execute_dm_tool("roll_initiative", {entity_ids: [...]})
        → turn_state.roll_initiative(...)
```

### Turn Loop

```
┌─────────────────────────────────────────────────────────────────────┐
│ 1. Get turn state                                                   │
│        dm_tools.execute_dm_tool("get_turn_state", {})               │
│        → current_entity, turn_order, tick                           │
│                                                                     │
│ 2. Player agent fetches prompt + tools via MCP                      │
│        get_prompt("player_context")                                 │
│        → player_mcp.build_player_prompt(entity_id)                  │
│        → reads CharacterState + TurnContext from DB                  │
│        list_tools                                                   │
│        → registry.get_available_tools(CharacterState from DB)       │
│        → filtered by class/conditions/economy/resources             │
│                                                                     │
│ 3. DMAgent → Player agent                                           │
│        DMAgent writes narrative/description as "user" message       │
│        Player agent responds as "assistant":                        │
│          - narrative describing intended actions                     │
│          - tool calls (all batched, not sequential)                 │
│                                                                     │
│ 4. Game server processes player tool calls via MCP                  │
│        for each tool_call in player_response:                       │
│            player_tools.execute_player_tool(                        │
│                entity_id, tool_call.name, tool_call.args            │
│            )                                                        │
│        Each call:                                                   │
│            a. Re-validates gates (DB state may have changed)        │
│            b. Spends action economy in DB if valid                  │
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
│ 7. Player calls end_turn                                            │
│        → game server resets economy in DB                           │
│        → turn advances                                              │
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

- **Action economy** (`action`, `bonus_action`, `reaction`) is spent in the DB the moment `execute_player_tool` succeeds via `db.spend_economy()`. Invalid calls do not mutate the DB.
- **Spell slots and class resources** (`rage`, `ki`, `bardic inspiration`, etc.) are **not** spent by `player_tools`. The DMAgent commits these via `dm_tools` (`spend_spell_slot`, `spend_resource`) after deciding the action resolves.
- **Movement** (`economy.movement_used`) is updated by the DMAgent via `dm_tools.move_entity`, not by the player's `move` tool call.

### Session lifecycle

- `end_turn` — resets action economy in DB, increments turn counter. If `max_turns` from the JWT is reached, the response includes `session_limit_reached: true`.
- `signoff` — saves `departure_action` to DB, deactivates the player session, removes entity from turn order. The JWT expires naturally.

---

## Quick Start (dev / temp DB)

```bash
# 1. Start the auth server (port 8080 — loads realm data + creates DB tables)
.venv/bin/python3 auth_wrapper.py

# 2. Start the game server (port 8081 — loads realm data + creates DB tables)
.venv/bin/python3 game_server.py

# 3. Register an account
curl -s -X POST http://localhost:8080/api/register
# → {"api_key": "..."}

# 4. Browse creation options
curl -s -X POST http://localhost:8080/api/creation-options \
  -H "Content-Type: application/json" \
  -d '{"auth": {"api_key": "<key>"}}'

# 5. Create a character
curl -s -X POST http://localhost:8080/api/create-character \
  -H "Content-Type: application/json" \
  -d '{"auth": {"api_key": "<key>"}, "choices": { ... }}'

# 6. Verify the account (manual — psql or db_controls)
.venv/bin/python3 -c "import db; db.claim_account('<key>')"

# 7. Get a session JWT
curl -s -X POST http://localhost:8080/api/join-session \
  -H "Content-Type: application/json" \
  -d '{"auth": {"api_key": "<key>"}}'
# → {"token": "<jwt>", "game_server_url": "http://localhost:8081", ...}

# 8. Join the game server
curl -s -X POST http://localhost:8081/join \
  -H "Authorization: Bearer <jwt>"
# → {"status": "joined", "mcp_endpoint": "/mcp", ...}

# 9. Play via MCP client pointed at http://localhost:8081/mcp
#    with Authorization: Bearer <jwt> header
```

To bulk-load existing character JSON files (bypasses the registration flow):
```bash
.venv/bin/python3 db_controls.py
```
