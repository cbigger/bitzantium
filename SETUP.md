# Setup

How to install Bitzantium, initialize the database, load a datapack, and run the servers.

---

## Prerequisites

- Python 3.11+
- PostgreSQL (running and accessible)

---

## 1. Clone and create a virtualenv

```bash
git clone <repo-url> bitzantium
cd bitzantium
python3 -m venv .venv
source .venv/bin/activate
```

## 2. Install dependencies

```bash
pip install -r requirements.txt
```

The schemas package also needs to be installed (editable is fine for dev):

```bash
pip install -e bitzantium_schemas/
```

Additional runtime dependencies not listed in `requirements.txt` but required by the server:

```bash
pip install sqlalchemy psycopg2-binary uvicorn starlette openai
```

## 3. Create the PostgreSQL database

```bash
sudo -u postgres psql -c "CREATE USER bitzantium WITH PASSWORD 'bitzantium';"
sudo -u postgres psql -c "CREATE DATABASE bitzantium_temp OWNER bitzantium;"
```

## 4. Configure `bitzantium.toml`

The default config ships with dev credentials that match the database created above:

```toml
[database]
url = "postgresql://bitzantium:bitzantium@localhost:5432/bitzantium_temp"

[auth_server]
host = "0.0.0.0"
port = 8080

[game_server]
host = "0.0.0.0"
port = 8081
url = "http://localhost:8081"

[jwt]
secret = "bitzantium-dev-secret-change-me!"
algorithm = "HS256"

[dm]
api_key = "dm-dev-key-change-me"
```

Edit `bitzantium.toml` in the project root, or point to a custom path via the `BITZANTIUM_CONFIG` environment variable.

## 5. Load a datapack into the database

A datapack is a directory of JSON files organized into subdirectories by category. `load_realm.py` walks the directory, validates each file against its Pydantic schema, and bulk-inserts everything into the `realm_objects` table. It clears existing realm data before inserting (full replacement).

Expected directory layout:

```
<datapack>/
├── abilities/          — AbilityDefinition JSON files
├── character/
│   ├── classes/        — ClassDefinition JSON files
│   ├── subclasses/     — SubclassDefinition JSON files
│   └── backgrounds/    — BackgroundDefinition JSON files
├── items/              — ItemBase JSON files (searched recursively)
└── creatures/
    ├── sapient/        — Playable species, one subdirectory per species
    │   └── <species>/
    │       ├── *.json          — CreatureDefinition files
    │       └── races/*.json    — RaceDefinition files
    └── sentient/       — Non-playable creatures, one subdirectory each
        └── <creature>/
            └── *.json          — CreatureDefinition files
```

Run the loader:

```bash
.venv/bin/python3 load_realm.py <path-to-datapack>/
```

Example:

```bash
.venv/bin/python3 load_realm.py Realms/dnd/
```

The script prints a summary of what was inserted:

```
Cleared 0 existing realm objects.
Inserted 142 realm objects: {'abilities': 40, 'classes': 6, 'subclasses': 12, ...}
```

Tables are created automatically on first run.

## 6. Start the servers

The auth server and game server run as separate processes. Both create tables and load realm data from the database on startup.

In one terminal:

```bash
.venv/bin/python3 auth_wrapper.py
```

In another:

```bash
.venv/bin/python3 game_server.py
```

By default the auth server listens on port 8080 and the game server on port 8081 (configurable in `bitzantium.toml`).

## 7. Verify it works

With both servers running, register a test account:

```bash
curl -s -X POST http://localhost:8080/api/register
```

You should get back:

```json
{"api_key": "..."}
```

Then confirm the datapack loaded correctly by browsing creation options:

```bash
curl -s -X POST http://localhost:8080/api/creation-options \
  -H "Content-Type: application/json" \
  -d '{"auth": {"api_key": "<key>"}}'
```

This returns the available species, classes, backgrounds, spells, etc. from the loaded datapack. If the response is empty or missing categories, re-check the `load_realm.py` output for validation warnings.
