# DM Client Setup

How to configure and run the DM agent client (`dm_client.py`). The DM client is a self-contained polling agent that connects to the game server, waits for player turns, resolves them through an LLM, and signals completion.

---

## Prerequisites

- The game server must be running (see [SETUP.md](SETUP.md))
- A datapack must be loaded into the database
- An OpenAI-compatible LLM API endpoint (OpenAI, a local server, any compatible provider)

---

## 1. Configure `dm_client.toml`

The DM client has its own config file, separate from `bitzantium.toml`:

```toml
[game_server]
url = "http://localhost:8081"

[dm]
api_key = "dm-dev-key-change-me"

[llm]
base_url = "https://api.openai.com/v1"
model = "gpt-4"
api_key = ""  # prefer BITZ_DM_LLM_API_KEY env var

[agent]
poll_interval = 10     # seconds between poll calls when idle
max_iterations = 10    # max tool-call round-trips per turn
temperature = 0.7
```

**`[game_server]`** — The URL of the running game server. Must match the host/port in `bitzantium.toml`.

**`[dm]`** — The DM API key. Must match the `[dm].api_key` in `bitzantium.toml` exactly — this is how the game server authenticates the DM client.

**`[llm]`** — The LLM provider. Any OpenAI-compatible API works (OpenAI, vLLM, Ollama with OpenAI compat, etc.). Set `base_url` to your provider's endpoint and `model` to the model identifier it expects.

**`[agent]`** — Tuning parameters:
- `poll_interval`: How often (seconds) the client checks for a pending turn when idle.
- `max_iterations`: Maximum LLM-tool call round-trips per turn before forcing completion.
- `temperature`: LLM sampling temperature.

## 2. Set API keys

The LLM API key should be set via environment variable rather than stored in the config file:

```bash
export BITZ_DM_LLM_API_KEY="sk-..."
```

All config values can be overridden with environment variables:

| Env var | Config key | Purpose |
|---|---|---|
| `BITZ_DM_PROVIDER` | `[llm].base_url` | LLM API base URL |
| `BITZ_DM_MODEL` | `[llm].model` | Model identifier |
| `BITZ_DM_LLM_API_KEY` | `[llm].api_key` | LLM provider API key |
| `BITZ_DM_API_KEY` | `[dm].api_key` | Game server DM API key |

Environment variables take precedence over the config file.

## 3. Run the DM client

```bash
.venv/bin/python3 dm_client.py
```

With a custom config path:

```bash
.venv/bin/python3 dm_client.py --config /path/to/dm_client.toml
```

With debug logging (logs full LLM output and tool call details):

```bash
BITZ_DM_DEBUG=1 .venv/bin/python3 dm_client.py
```

## 4. Verify it works

On startup the client logs its poll interval and server URL:

```
12:00:00 [dm_client] INFO starting poll loop — interval=10s, server=http://localhost:8081
```

While idle (no players have ended a turn), you'll see periodic debug-level messages:

```
12:00:10 [dm_client] DEBUG no pending turn.
```

When a player ends their turn, the client detects the pending turn and begins resolving:

```
12:00:20 [dm_client] INFO pending turn detected — resolving.
12:00:20 [dm_client] INFO agent iteration 1/10 — 5 messages
12:00:22 [dm_client] INFO found 3 tool call(s)
12:00:22 [dm_client] INFO executing: resolve_attack({"attacker_id": "thorin", ...})
...
12:00:25 [dm_client] INFO no tool calls in iteration 2 — turn resolved.
12:00:25 [dm_client] INFO turn complete — narrative stored (412 chars).
```

---

## How it works

The DM client is a micro-agent with no framework — just the OpenAI Python library for LLM inference and httpx for game server communication.

**Poll cycle:** The client POSTs to `/api/dm/poll` and checks if `dm_turn_pending` is set. If not, it sleeps for `poll_interval` seconds and tries again. The HTTP client is kept alive across poll cycles (persistent connection).

**Agent loop:** When a pending turn is detected, the client:

1. Builds a message array: hardcoded system prompt + full DM chat history from the poll response.
2. Calls the LLM.
3. Parses the response for `<tool_call>` XML blocks.
4. Executes each tool call against the game server via `POST /api/dm/tool`.
5. Injects `<tool_response>` blocks back into the conversation.
6. Calls the LLM again. Repeats until no tool calls remain (pure narrative) or `max_iterations` is reached.

**After resolution:** The client mechanically (not via the LLM):
1. Strips tool call/response blocks from the final output to extract the narrative.
2. Stores the narrative via `POST /api/dm/append-history`.
3. Signals `POST /api/dm/turn-complete` to clear `dm_turn_pending`.
4. Resumes polling.

The DM has no local memory. Its entire context is the persistent chat history stored in the database, rebuilt from scratch on every turn.

---

## DM API Endpoints

All DM endpoints require `Authorization: Bearer <dm-api-key>`.

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/api/dm/poll` | Check for pending turns; returns chat history if pending |
| `GET` | `/api/dm/tools` | List available DM tools |
| `POST` | `/api/dm/tool` | Execute a DM tool: `{"tool": "name", "arguments": {...}}` |
| `POST` | `/api/dm/turn-complete` | Signal DM turn completion |
| `POST` | `/api/dm/append-history` | Append to chat history: `{"role": "...", "content": "..."}` |

---

## Using a local LLM

Point `base_url` at any OpenAI-compatible server:

```bash
# vLLM
export BITZ_DM_PROVIDER="http://localhost:8000/v1"
export BITZ_DM_MODEL="meta-llama/Llama-3-70b-chat-hf"
export BITZ_DM_LLM_API_KEY="not-needed"

# Ollama (OpenAI compat mode)
export BITZ_DM_PROVIDER="http://localhost:11434/v1"
export BITZ_DM_MODEL="llama3"
export BITZ_DM_LLM_API_KEY="ollama"
```

The client uses non-streaming completions, so the provider must support the `/chat/completions` endpoint. The model must be capable of following the Hermes-style `<tool_call>` XML format described in the system prompt — larger models (70B+) tend to be more reliable at this.

---

## Remote deployment

The DM client can run on a different machine than the game server. Set `[game_server].url` to the remote game server's address and ensure the DM API key matches. The only network requirement is HTTP access to the game server's `/api/dm/*` endpoints.

```toml
[game_server]
url = "http://your-game-server:8081"
```
