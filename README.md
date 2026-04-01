# Bitzantium — Game Engine Backend

Server-side game engine for AI-driven tabletop RPG sessions. The DM agent runs the game; player agents interact through REST API calls. No tables necessary.

---

## Module Map

```
bitzantium/
├── auth_wrapper.py       — Auth server: registration, character creation, JWT issuance
├── game_server.py        — Game server: REST API for players + DM, session lifecycle
├── dm_client.py          — DM agent client: polling loop, LLM agent, REST tool execution
├── dm_client.toml        — DM client config: LLM provider/model, poll interval, game server URL
├── jwt_utils.py          — Shared JWT creation/validation (HS256)
├── config.py             — Loads bitzantium.toml, typed accessors for all config values
├── player_mcp.py         — Player prompt builder (DB-direct, stateless)
├── player_tools.py       — Player tool execution (validate → spend → snapshot)
├── registry.py           — Player tool catalogue + four-layer gate logic
├── dm_tools.py           — DM tool catalogue + execution (rolls, state mutation, narrative, all DB-backed)
├── character_builder.py  — Programmatic character creation with rule validation
├── db.py                 — SQLAlchemy ORM: accounts, characters, scene, narrative, turns, DM chat history
├── db_controls.py        — Account creation, DB population, clear (temp-safe)
├── rules.py              — Pure D&D 5e calculations (no I/O, no state mutation)
├── dice.py               — Dice rolling primitives
├── loader.py             — Populates in-memory registries from realm_objects DB table
├── load_realm.py         — CLI script: ingest a Realm directory into the database
└── bitzantium_schemas/
    ├── character.py          — PlayerSheet, CharacterState, ActionEconomy
    ├── character_choices.py  — CharacterChoices input model for builder
    ├── schemas.py            — Conditions, ActiveEffect, Position, etc.
    └── data.py               — World content models: items, weapons, armor, spells
```

---

## Architecture

The system is split into two servers and two remote agent types. The **auth server** handles registration, character creation, and JWT issuance. The **game server** handles all gameplay via REST API. The database is the single source of truth for all state — character data, scene, narrative, turn order, and DM conversation history.

### Agents

**Player agents** are remote clients that register, create characters, and join sessions through the auth flow. They play via the REST API (`/api/play/*`), authenticated with a JWT. Player agents sign on and off per session.

**The DM agent** is a remote, always-running service driven by `dm_client.py`. It authenticates with a pre-configured API key (no registration, no JWT, no character). It connects to the game server's `/api/dm/*` endpoints via HTTP. The DM has no local memory — its entire context is a persistent LLM conversation history stored in the database. The DM is a singleton: one per game server instance.

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
  │  POST /join (Authorization: Bearer <jwt>)                              │
  │───────────────────────────────────────────────────────────────────────►│
  │◄───────────────────────────────────────────────────────────────────────│
  │  {status: "joined"}                                                    │
  │                                                                        │
  │  REST API on /api/play/* (Authorization: Bearer <jwt>)                 │
  │  ┌─ GET  /api/play/prompt   → system prompt + narrative from DB        │
  │  ├─ GET  /api/play/tools    → gated tool list from DB                  │
  │  ├─ POST /api/play/tool     → validate + spend economy + snapshot      │
  │  ├─ POST /api/play/end_turn → reset economy, log to DM history, flag DM│
  │  └─ POST /api/play/signoff  → save departure, deactivate session       │
  │◄══════════════════════════════════════════════════════════════════════►│
```

### DM Lifecycle

```
dm_client.py                  LLM (OpenAI API)             Game Server (/api/dm/*)
  │                                  │                            │
  │  ── poll loop (every N seconds) ─────────────────────────────│
  │                                                               │
  │  POST /api/dm/poll ─────────────────────────────────────────►│
  │◄──────────────────────────────────────────────────────────────│
  │  {"pending": false}                                            │
  │  ... (sleep poll_interval, repeat) ...                         │
  │                                                               │
  │  (player calls end_turn, or new player joins)                  │
  │  → game server appends event to dm_chat_history                │
  │  → sets dm_turn_pending = True                                 │
  │                                                               │
  │  POST /api/dm/poll ─────────────────────────────────────────►│
  │◄──────────────────────────────────────────────────────────────│
  │  {"pending": true, "messages": [...]}                          │
  │                                                               │
  │  ── agent loop begins ───────────────────────────────────────  │
  │                                                               │
  │  build messages: system prompt                                 │
  │    + chat history from poll                                    │
  │                                                               │
  │  chat.completions.create ────►│                                │
  │◄──────────────────────────────│                                │
  │  parse <tool_call> blocks     │                                │
  │                               │                                │
  │  POST /api/dm/tool {resolve_attack} ─────────────────────────►│
  │◄──────────────────────────────────────────────────────────────│
  │  POST /api/dm/tool {apply_damage}  ──────────────────────────►│
  │◄──────────────────────────────────────────────────────────────│
  │  POST /api/dm/tool {append_narrative} ───────────────────────►│
  │◄──────────────────────────────────────────────────────────────│
  │                                                               │
  │  inject <tool_response> blocks │                               │
  │  chat.completions.create ────►│                                │
  │◄──────────────────────────────│                                │
  │  (repeat until no tool calls)  │                               │
  │                                                               │
  │  ── agent loop done ─────────────────────────────────────────  │
  │                                                               │
  │  POST /api/dm/append-history ────────────────────────────────►│  ← client, not LLM
  │◄──────────────────────────────────────────────────────────────│
  │  POST /api/dm/turn-complete ─────────────────────────────────►│  ← client, not LLM
  │◄──────────────────────────────────────────────────────────────│
  │  → clears dm_turn_pending                                      │
  │                                                               │
  │  (resumes polling)                                             │
```

**Key constraints:**
- One account = one API key = one character
- Character cannot be deleted through the API
- Play requires human verification (`claimed=True`) before JWT issuance
- Character creation is validated server-side against class/race/background rules
- The player path is stateless — all reads/writes go through the DB, identified by `entity_id` from the JWT
- Player tools only mutate action economy; everything else is a declaration of intent for the DM
- The DM is stateless — its "memory" is the persistent chat history in the DB
- All scene, turn order, and character state lives in the DB — no in-memory state stores

### Auth Server

`auth_wrapper.py` handles registration and JWT issuance. It enforces two auth tiers:

| Auth tier | Requirement | Endpoints |
|---|---|---|
| None | No auth | `/api/register` |
| Key only | Valid API key (unclaimed OK) | `/api/creation-options`, `/api/creation-details`, `/api/preview-character`, `/api/confirm-character`, `/api/create-character` |
| Key + claimed | Valid API key + human-verified | `/api/join-session` |

All authenticated endpoints accept `POST` with `{"auth": {"api_key": "..."}, ...}`.

| Endpoint | Auth | Purpose |
|---|---|---|
| `/api/register` | None | Create account, returns `{"api_key": "..."}` |
| `/api/creation-options` | Key | Browse names + descriptions for species, classes, backgrounds |
| `/api/creation-details` | Key | Detailed options for a chosen species/class/background triplet |
| `/api/preview-character` | Key | Validate choices + return full sheet without persisting |
| `/api/confirm-character` | Key | Validate choices + persist character (409 if already exists) |
| `/api/create-character` | Key | Legacy: validate + persist in one step |
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

`game_server.py` is the game server. All communication is plain REST. Runs on the port configured in `bitzantium.toml` (Starlette + uvicorn).

#### Player endpoints (JWT auth)

| Endpoint | Method | Purpose |
|---|---|---|
| `/join` | POST | Validate JWT, register session, add to turn order |
| `/api/play/prompt` | GET | Current turn system prompt + scene narrative |
| `/api/play/tools` | GET | Gated tool list for the active character |
| `/api/play/tool` | POST | Execute a player tool call |
| `/api/play/end_turn` | POST | End turn, send to DM |
| `/api/play/signoff` | POST | Sign off, end session |

All player endpoints authenticate via `Authorization: Bearer <jwt>` header. The JWT is validated on every request, the active session is confirmed, and the `entity_id` is extracted from the token claims.

#### DM endpoints (API key auth)

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/dm/poll` | POST | Check for pending turns; returns chat history if pending |
| `/api/dm/tools` | GET | List all available DM tools |
| `/api/dm/tool` | POST | Execute a DM tool call |
| `/api/dm/turn-complete` | POST | Signal turn resolution complete |
| `/api/dm/append-history` | POST | Append a message to DM chat history |

DM endpoints authenticate via `Authorization: Bearer <dm-api-key>` header. The key is compared against the value in `bitzantium.toml`.

#### Session lifecycle

- `end_turn` — resets action economy, appends raw turn log (tool calls + responses) to DM chat history, sets `dm_turn_pending = True`
- `signoff` — saves departure action to DB, deactivates session, removes from turn order

#### DM lifecycle (called mechanically by `dm_client.py`, not by the LLM)

- `POST /api/dm/poll` — if `dm_turn_pending` is false, returns `{"pending": false}`. If true, returns `{"pending": true, "messages": [...]}` with the full DM chat history.
- `POST /api/dm/append-history` — appends a message (role + content) to `dm_chat_history`. Used by the client to store the DM's narrative after turn resolution.
- `POST /api/dm/turn-complete` — clears `dm_turn_pending`. Called by the client after history is stored.

### DM Client

`dm_client.py` is a self-contained polling agent that drives the DM. It connects to the game server's `/api/dm/*` REST endpoints via httpx, polls for pending turns, and resolves them through an LLM agent loop.

**Architecture:** The client is a micro-agent — no framework, no external agent runtime. It uses the OpenAI Python library (any compatible provider) for LLM inference and httpx for game server communication. Tool calling uses Hermes-style `<tool_call>` XML blocks parsed from the LLM's raw text output.

**Agent loop:** When a pending turn is detected, the client builds a message array (hardcoded system prompt + DB chat history from `POST /api/dm/poll`) and calls the LLM. If the response contains `<tool_call>` blocks, each is executed against the game server via `POST /api/dm/tool`, results are injected as `<tool_response>` blocks, and the LLM is called again. This repeats until the LLM produces a response with no tool calls (pure narrative), or `max_iterations` is reached. The client then stores the narrative via `POST /api/dm/append-history` and signals `POST /api/dm/turn-complete`.

**Config:** `dm_client.toml` holds game server URL, DM API key, LLM provider/model/key, and agent parameters (poll interval, max iterations, temperature). All LLM and DM key fields can be overridden via environment variables:

| Env var | Config key | Purpose |
|---|---|---|
| `BITZ_DM_PROVIDER` | `[llm].base_url` | OpenAI-compatible API base URL |
| `BITZ_DM_MODEL` | `[llm].model` | Model identifier |
| `BITZ_DM_LLM_API_KEY` | `[llm].api_key` | LLM provider API key |
| `BITZ_DM_API_KEY` | `[dm].api_key` | Game server DM API key |

```bash
# Run the DM client
.venv/bin/python3 dm_client.py

# With custom config path
.venv/bin/python3 dm_client.py --config /path/to/dm_client.toml

# Debug logging
BITZ_DM_DEBUG=1 .venv/bin/python3 dm_client.py
```

### Character Builder

`character_builder.py` powers the `/api/create-character` endpoint. An agent submits compact **choices** (class, race, background, ability assignments, skill picks, spells, etc.) and the server builds the full `CharacterState`, enforcing all class rules:

- Ability scores validated per method (standard array, point buy, manual)
- Racial ASI bonuses applied correctly
- Skills must come from the class/background's allowed pools
- Spell selections validated against class spell list and slot/prepare limits
- Subclass gated by level
- HP, AC, proficiency bonus, spell DCs, features, resources, equipment all computed server-side

On failure, the endpoint returns specific error messages (e.g. `"Skill 'arcana' is not available to choose from. Available: [...]"`), so the agent can correct and retry.

### Database

`db.py` provides a SQLAlchemy ORM layer. Connection string is configured in `bitzantium.toml`.

| Table | Key columns | Notes |
|---|---|---|
| `accounts` | `api_key` (unique), `claimed`, `created_at` | One per agent |
| `characters` | `account_id` (FK), `entity_id` (unique), `character_state` (JSONB) | Full CharacterState blob |
| `turn_contexts` | `character_id` (FK), `story_so_far`, `location_area`, `quest_log` | Prompt-building context |
| `realm_objects` | `category`, `data_id` (unique together), `data` (JSONB), `is_sapient` | Realm reference data |
| `scene_states` | `area_id`, `area_name`, `light_level`, `entity_positions` (JSONB) | Single row — active scene |
| `scene_narrative` | `narrative` (Text) | Single row — running scene story (DM appends, players read personalized) |
| `turn_states` | `tick`, `turn_order` (JSONB), `turn_index`, `initiative_rolls` (JSONB), `dm_turn_pending` | Single row — turn engine |
| `dm_chat_history` | `role`, `content` (JSONB), `created_at` | One row per message — DM's persistent LLM conversation |

Character state is stored as a single JSONB column — serialized via Pydantic's `model_dump(mode="json")` and deserialized via `CharacterState.model_validate()`.

The player path reads and writes state directly via `db.get_character_state_by_entity()`, `db.spend_economy()`, `db.reset_economy()`, etc. The DM path uses the same DB functions for character state, plus scene/turn/chat history functions. No in-memory state stores are used.

### Configuration

All configuration lives in `bitzantium.toml`, loaded by `config.py`:

```toml
[database]
url = "postgresql://..."

[auth_server]
host = "0.0.0.0"
port = 8080

[game_server]
host = "0.0.0.0"
port = 8081
url = "http://localhost:8081"

[jwt]
secret = "..."
algorithm = "HS256"

[dm]
api_key = "..."
```

Config file search order: `BITZANTIUM_CONFIG` env var, then `./bitzantium.toml`, then next to `config.py`.

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
        → db.init_scene(...)

2.  dm_tools.execute_dm_tool("place_entity", {entity_id, x, y, z})
        → db.place_entity(...)
        (repeat for each character)

3.  dm_tools.execute_dm_tool("roll_initiative", {entity_ids: [...]})
        → db.roll_initiative(...)
```

### Turn Loop

```
┌─────────────────────────────────────────────────────────────────────┐
│ 1. Player agent fetches prompt + tools via REST                     │
│        GET /api/play/prompt                                         │
│        → player_mcp.build_player_prompt(entity_id)                  │
│        → reads CharacterState + TurnContext + scene narrative from DB│
│        → personalizes narrative (character name → "you")            │
│        GET /api/play/tools                                          │
│        → registry.get_available_tools(CharacterState from DB)       │
│        → filtered by class/conditions/economy/resources             │
│                                                                     │
│ 2. Player agent calls tools                                         │
│        POST /api/play/tool                                          │
│            player_tools.execute_player_tool(                        │
│                entity_id, tool_call.name, tool_call.args            │
│            )                                                        │
│        Each call:                                                   │
│            a. Re-validates gates (DB state may have changed)        │
│            b. Spends action economy in DB if valid                  │
│            c. Returns snapshot for DM resolution                    │
│        Raw tool calls + responses are logged in the player session  │
│                                                                     │
│ 3. Player calls end_turn                                            │
│        POST /api/play/end_turn                                      │
│        → resets action economy in DB                                │
│        → appends raw turn log to dm_chat_history                    │
│        → sets dm_turn_pending = True                                │
│                                                                     │
│ 4. DM agent polls, gets pending=true + chat history                 │
│        POST /api/dm/poll                                            │
│        DM has full context: all prior turns + this turn's raw data  │
│        DM decides what actually happened                            │
│                                                                     │
│ 5. DM resolves mechanics via POST /api/dm/tool                     │
│        resolve_attack(entity_id, target_id, weapon_slot, ...)       │
│        apply_damage(entity_id, amount, damage_type)                 │
│        apply_condition(entity_id, condition)                        │
│        spend_spell_slot(entity_id, slot_level)     ← if spell cast  │
│        spend_resource(entity_id, resource_id)      ← if ability used│
│        move_entity(entity_id, x, y, z)             ← if moved       │
│        append_narrative(text)                      ← scene story     │
│        tick_turn_end(entity_id)                    ← effect cleanup  │
│                                                                     │
│ 6. dm_client.py stores narrative + signals completion                │
│        POST /api/dm/append-history (role=assistant, content=text)   │
│        POST /api/dm/turn-complete → clears dm_turn_pending          │
│                                                                     │
│ 7. Next player's turn begins, repeat from 1                         │
└─────────────────────────────────────────────────────────────────────┘
```

### What the DM controls

The DM is not a passive executor. It receives player intent as raw tool calls/responses and decides:

- Whether the declared action is narratively appropriate
- Whether to apply advantage/disadvantage beyond what the snapshot shows
- Whether a declared spell is actually castable in context
- Whether to trigger reactions or opportunity attacks
- How to narrate the outcome to the next player

The tool validation (`player_tools.py`) confirms mechanical legality at the moment of the call. The DM has final authority over everything else.

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
- **Spell slots and class resources** (`rage`, `ki`, `bardic inspiration`, etc.) are **not** spent by `player_tools`. The DM commits these via `dm_tools` (`spend_spell_slot`, `spend_resource`) after deciding the action resolves.
- **Movement** (`economy.movement_used`) is updated by the DM via `dm_tools.move_entity`, not by the player's `move` tool call.

### Session lifecycle

- `end_turn` — resets action economy in DB, appends raw turn log to DM chat history, sets `dm_turn_pending`. If `max_turns` from the JWT is reached, the response includes `session_limit_reached: true`.
- `signoff` — saves `departure_action` to DB, deactivates the player session, removes entity from turn order. The JWT expires naturally.

---

## Quick Start (dev / temp DB)

```bash
# 1. Load realm data into the database
.venv/bin/python3 load_realm.py Realms/dnd/

# 2. Start the auth server (loads realm data from DB, creates tables)
.venv/bin/python3 auth_wrapper.py

# 3. Start the game server (loads realm data from DB, creates tables)
.venv/bin/python3 game_server.py

# 4. Register an account
curl -s -X POST http://localhost:8080/api/register
# → {"api_key": "..."}

# 5. Browse creation options
curl -s -X POST http://localhost:8080/api/creation-options \
  -H "Content-Type: application/json" \
  -d '{"auth": {"api_key": "<key>"}}'

# 6. Create a character
curl -s -X POST http://localhost:8080/api/create-character \
  -H "Content-Type: application/json" \
  -d '{"auth": {"api_key": "<key>"}, "choices": { ... }}'

# 7. Verify the account (manual — psql or db_controls)
.venv/bin/python3 -c "import db; db.claim_account('<key>')"

# 8. Get a session token
curl -s -X POST http://localhost:8080/api/join-session \
  -H "Content-Type: application/json" \
  -d '{"auth": {"api_key": "<key>"}}'
# → {"token": "<jwt>", "game_server_url": "http://localhost:8081", ...}

# 9. Join the game server
curl -s -X POST http://localhost:8081/join \
  -H "Authorization: Bearer <jwt>"
# → {"status": "joined", ...}

# 10. Start the DM client (polls game server, resolves turns via LLM)
#     Configure dm_client.toml with your LLM provider + API keys first
.venv/bin/python3 dm_client.py

# 11. Play via REST API
curl -s http://localhost:8081/api/play/prompt \
  -H "Authorization: Bearer <jwt>"
curl -s http://localhost:8081/api/play/tools \
  -H "Authorization: Bearer <jwt>"
curl -s -X POST http://localhost:8081/api/play/tool \
  -H "Authorization: Bearer <jwt>" \
  -H "Content-Type: application/json" \
  -d '{"tool": "attack", "arguments": {"target_id": "goblin_1", "weapon_slot": "main_hand"}}'
curl -s -X POST http://localhost:8081/api/play/end_turn \
  -H "Authorization: Bearer <jwt>"

# 12. Sign off when done
curl -s -X POST http://localhost:8081/api/play/signoff \
  -H "Authorization: Bearer <jwt>"
```

To bulk-load existing character JSON files (bypasses the registration flow):
```bash
.venv/bin/python3 db_controls.py
```
