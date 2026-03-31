"""
dm_client.py — Bitzantium DM agent client.

Self-contained polling agent that connects to the game server's /api/dm/*
endpoints, waits for player turns, resolves them via LLM + DM tools, and
signals completion.

    .venv/bin/python3 dm_client.py [--config dm_client.toml]

Env var overrides (take precedence over dm_client.toml):
    BITZ_DM_PROVIDER     → [llm].base_url
    BITZ_DM_MODEL        → [llm].model
    BITZ_DM_LLM_API_KEY  → [llm].api_key
    BITZ_DM_API_KEY      → [dm].api_key
"""

import asyncio
import json
import logging
import os
import re
import sys
import tomllib
from pathlib import Path

import httpx
from openai import OpenAI

log = logging.getLogger("dm_client")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config(path: str = "dm_client.toml") -> dict:
    p = Path(path)
    if not p.exists():
        print(f"Config not found: {path}", file=sys.stderr)
        sys.exit(1)
    with open(p, "rb") as f:
        cfg = tomllib.load(f)

    # Env var overrides
    if v := os.environ.get("BITZ_DM_PROVIDER"):
        cfg.setdefault("llm", {})["base_url"] = v
    if v := os.environ.get("BITZ_DM_MODEL"):
        cfg.setdefault("llm", {})["model"] = v
    if v := os.environ.get("BITZ_DM_LLM_API_KEY"):
        cfg.setdefault("llm", {})["api_key"] = v
    if v := os.environ.get("BITZ_DM_API_KEY"):
        cfg.setdefault("dm", {})["api_key"] = v

    return cfg


# ---------------------------------------------------------------------------
# System prompt — hardcoded for now
# ---------------------------------------------------------------------------

DM_SYSTEM_PROMPT = """\
You are the Dungeon Master for a D&D 5th Edition game running on the Bitzantium engine.

# Your Role
You receive player turn data — the raw tool calls and snapshots from their actions — \
and you resolve what actually happens in the game world. You have final authority over \
all mechanical and narrative outcomes.

# How a Turn Works
1. You receive a player's turn (their declared actions) OR a system event (new player joined).
2. You decide what actually happens — whether attacks hit, how spells resolve, what the \
   narrative outcome is.
3. You use your DM tools to make it real: roll attacks, apply damage, apply conditions, \
   move entities, spend spell slots and resources.
4. When you are done resolving mechanics, call append_narrative with your story text. \
   Write in third person using character names ("Thorin swings his axe"). The system \
   personalizes it for each player automatically. This narrative is the shared story \
   that all players read — write it like a book being written in real time.
5. When a new player joins (you receive a "new_player_joined" event), set up the scene \
   with init_scene and place_entity, then call append_narrative with your opening \
   scene description and welcome. Call set_player_location to set where they are.

# Your Authority
- You decide advantage/disadvantage based on narrative context.
- You decide whether a declared action is appropriate in context.
- You can trigger opportunity attacks, reactions, or environmental effects.
- You narrate outcomes — be vivid but concise.
- Player tool calls confirm mechanical legality; you confirm everything else.

# Important Rules
- Resolve ALL mechanical effects with tools — do not just narrate damage without calling \
  apply_damage, do not narrate movement without calling move_entity, etc.
- When a player casts a spell, call spend_spell_slot for the appropriate level.
- When a player uses a class resource (rage, ki, etc.), call spend_resource.
- Call tick_turn_end for the acting entity after resolving their turn to decrement effects.
- Use get_scene_state or get_character_state if you need more context before resolving.
- ALWAYS call append_narrative as your final tool call with your story text. This is how \
  players see what happened. After calling append_narrative, do NOT emit any more \
  tool_call blocks — the system handles turn completion.

# Tool Call Format
To call a tool, emit a tool_call block:

<tool_call>
{"name": "tool_name", "arguments": {"arg1": "value1", "arg2": "value2"}}
</tool_call>

The result will be injected as a tool_response block. You may call multiple tools in \
sequence across multiple rounds. When you have finished resolving all mechanics, stop \
emitting tool_call blocks and write your narrative instead.

# Available Tools

## Scene Setup
- init_scene: Initialise or reset the scene. Args: area_id, area_name, area_description, light_level (bright|dim|darkness).
- place_entity: Place an entity at grid coords. Args: entity_id (required), x, y, z.

## State Inspection
- get_scene_state: Get full scene state — area info, all entity positions, HP, conditions, AC. No args.
- get_character_state: Get full state of a character. Args: entity_id (required).
- get_turn_state: Get turn order, current entity, tick counter. No args.

## Turn Order
- roll_initiative: Roll initiative and set turn order. Args: entity_ids (required, array).
- set_turn_order: Manually set turn order. Args: entity_ids (required, array).
- next_turn: Advance to next entity. No args.
- add_to_turn_order: Insert entity into turn order. Args: entity_id (required), after_index.
- remove_from_turn_order: Remove entity from turn order. Args: entity_id (required).

## Roll Resolution (does NOT mutate state — call apply_damage etc. separately)
- resolve_attack: Roll attack against target. Args: attacker_id, target_id (required), weapon_slot (main_hand|off_hand|ranged), advantage (normal|advantage|disadvantage), proficient (bool), two_handed (bool).
- resolve_saving_throw: Roll a saving throw. Args: entity_id, ability, dc (required), advantage.
- resolve_ability_check: Roll skill/ability check. Args: entity_id, skill_or_ability (required), dc, advantage.
- resolve_contested_check: Opposed checks. Args: entity_a_id, skill_a, entity_b_id, skill_b (all required), advantage_a, advantage_b.

## State Mutation
- apply_damage: Apply damage (respects resistances, temp HP, auto-unconscious at 0). Args: entity_id, amount (required), damage_type.
- apply_healing: Heal (capped at max, removes unconscious if revived). Args: entity_id, amount (required).
- apply_condition: Apply a condition. Args: entity_id, condition (required), duration_turns.
- remove_condition: Remove a condition. Args: entity_id, condition (required).
- apply_effect: Attach a named effect (buff/debuff). Args: entity_id, name (required), description, source, duration_turns, concentration (bool).
- end_effect: Remove an effect by ID. Args: entity_id, effect_id (required).
- move_entity: Move entity to grid position. Args: entity_id, x, y (required), z.
- spend_spell_slot: Consume a spell slot. Args: entity_id, level (required).
- restore_spell_slot: Restore a spell slot. Args: entity_id, level (required).
- spend_resource: Spend class resource charges. Args: entity_id, resource_name (required), amount.
- restore_resource: Restore class resource charges. Args: entity_id, resource_name (required), amount.

## Turn Bookkeeping
- tick_turn_end: Decrement effect durations, expire effects at 0. Args: entity_id (required).

## Narrative
- append_narrative: Append story text to the shared scene narrative that all players read. Write in third person using character names. Args: text (required).
- set_player_location: Update a player's location context shown in their prompt. Args: entity_id, location_area (required), location_sub.\
"""


# ---------------------------------------------------------------------------
# Tool call parsing
# ---------------------------------------------------------------------------

TOOL_CALL_RE = re.compile(
    r"<tool_call>\s*(.*?)\s*</tool_call>",
    re.DOTALL,
)


def parse_tool_calls(text: str) -> list[dict]:
    """Extract tool call dicts from LLM output."""
    calls = []
    for m in TOOL_CALL_RE.finditer(text):
        try:
            payload = json.loads(m.group(1))
            calls.append({
                "name": payload.get("name", ""),
                "arguments": payload.get("arguments", {}),
            })
        except json.JSONDecodeError as e:
            log.warning("malformed tool_call JSON: %s", e)
    return calls


def strip_tool_blocks(text: str) -> str:
    """Remove all <tool_call> and <tool_response> blocks, return narrative only."""
    text = TOOL_CALL_RE.sub("", text)
    text = re.sub(r"<tool_response>.*?</tool_response>", "", text, flags=re.DOTALL)
    # Collapse blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Game server HTTP client
# ---------------------------------------------------------------------------

async def call_dm_tool(client: httpx.AsyncClient, base_url: str, name: str, arguments: dict) -> dict:
    """Call a single DM tool on the game server via REST."""
    resp = await client.post(
        f"{base_url}/api/dm/tool",
        json={"tool": name, "arguments": arguments},
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# LLM client
# ---------------------------------------------------------------------------

def create_llm(cfg: dict) -> OpenAI:
    llm_cfg = cfg.get("llm", {})
    api_key = llm_cfg.get("api_key", "")
    if not api_key:
        print("No LLM API key. Set BITZ_DM_LLM_API_KEY or [llm].api_key in config.",
              file=sys.stderr)
        sys.exit(1)
    return OpenAI(
        api_key=api_key,
        base_url=llm_cfg.get("base_url", "https://api.openai.com/v1"),
    )


def llm_complete(client: OpenAI, messages: list[dict], cfg: dict) -> str:
    """Single non-streaming LLM completion."""
    agent_cfg = cfg.get("agent", {})
    resp = client.chat.completions.create(
        model=cfg["llm"]["model"],
        messages=messages,
        temperature=float(agent_cfg.get("temperature", 0.7)),
    )
    return resp.choices[0].message.content or ""


# ---------------------------------------------------------------------------
# Agent loop — one DM turn
# ---------------------------------------------------------------------------

async def run_dm_turn(
    client: httpx.AsyncClient,
    base_url: str,
    llm: OpenAI,
    poll_data: dict,
    cfg: dict,
) -> str:
    """
    Resolve a single DM turn.

    Builds a conversation from the system prompt + chat history, then loops:
    LLM → parse tool calls → execute via REST → inject results → repeat
    until the LLM produces a response with no tool calls (pure narrative).

    Returns the DM's final narrative text for history storage.
    """
    max_iter = int(cfg.get("agent", {}).get("max_iterations", 10))
    chat_history = poll_data.get("messages", [])

    # Build the initial messages array
    messages: list[dict] = [{"role": "system", "content": DM_SYSTEM_PROMPT}]

    # Append existing DM chat history as context
    for entry in chat_history:
        role = entry.get("role", "user")
        content = entry.get("content")
        if isinstance(content, dict):
            content = json.dumps(content, indent=2)
        elif not isinstance(content, str):
            content = str(content)
        messages.append({"role": role, "content": content})

    last_response = ""

    for iteration in range(1, max_iter + 1):
        log.info("agent iteration %d/%d — %d messages", iteration, max_iter, len(messages))

        # LLM call (blocking, run in executor to not block the event loop)
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None, llm_complete, llm, messages, cfg,
        )
        log.info("llm response: %d chars", len(response))
        log.debug("llm output:\n%s", response)

        # Parse tool calls
        tool_calls = parse_tool_calls(response)

        if not tool_calls:
            # No tool calls — this is the final narrative response
            log.info("no tool calls in iteration %d — turn resolved.", iteration)
            last_response = response
            break

        log.info("found %d tool call(s)", len(tool_calls))

        # Execute each tool call and build the continuation
        continuation = response

        for tc in tool_calls:
            name = tc["name"]
            args = tc["arguments"]
            log.info("executing: %s(%s)", name, json.dumps(args))

            result = await call_dm_tool(client, base_url, name, args)

            log.info("result: %s", json.dumps(result)[:200])
            continuation += f"\n<tool_response>\n{json.dumps(result, indent=2)}\n</tool_response>\n"

        # Append assistant turn with tool calls + results, loop for next LLM call
        messages.append({"role": "assistant", "content": continuation})
        last_response = continuation

    else:
        log.warning("max iterations (%d) reached.", max_iter)

    return last_response


# ---------------------------------------------------------------------------
# Poll loop
# ---------------------------------------------------------------------------

async def poll_loop(cfg: dict):
    """Main loop: poll for pending turns, resolve them, repeat."""
    llm = create_llm(cfg)
    poll_interval = int(cfg.get("agent", {}).get("poll_interval", 10))
    dm_api_key = cfg["dm"]["api_key"]
    base_url = cfg["game_server"]["url"].rstrip("/")
    headers = {"Authorization": f"Bearer {dm_api_key}"}

    log.info("starting poll loop — interval=%ds, server=%s", poll_interval, base_url)

    async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
        while True:
            try:
                # Poll for pending turns
                resp = await client.post(f"{base_url}/api/dm/poll")
                resp.raise_for_status()
                poll_result = resp.json()

                pending = poll_result.get("pending", False)

                if not pending:
                    log.debug("no pending turn.")
                else:
                    log.info("pending turn detected — resolving.")

                    # Run the DM agent turn
                    dm_response = await run_dm_turn(
                        client, base_url, llm, poll_result, cfg,
                    )

                    # Extract narrative (strip tool blocks) for chat history
                    narrative = strip_tool_blocks(dm_response)

                    # Store the DM's response in chat history
                    await client.post(
                        f"{base_url}/api/dm/append-history",
                        json={"role": "assistant", "content": narrative},
                    )

                    # Signal turn completion
                    await client.post(f"{base_url}/api/dm/turn-complete")
                    log.info("turn complete — narrative stored (%d chars).", len(narrative))

            except Exception as e:
                log.error("poll cycle error: %s", e)

            await asyncio.sleep(poll_interval)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(config_path: str):
    cfg = load_config(config_path)

    level = logging.DEBUG #if os.environ.get("BITZ_DM_DEBUG") else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    await poll_loop(cfg)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Bitzantium DM agent client")
    parser.add_argument("--config", "-c", default="dm_client.toml")
    args = parser.parse_args()
    asyncio.run(main(args.config))
