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
1. You receive a player's turn (their declared actions) OR a system event (e.g. new player \
   joined an existing scene).
2. You decide what actually happens — whether attacks hit, how spells resolve, what the \
   narrative outcome is.
3. You use your DM tools to make it real: roll attacks, apply damage, apply conditions, \
   move entities, spend spell slots and resources.
4. When you are done resolving mechanics, call append_narrative as your FINAL tool call. \
   Do NOT write plain-text narrative after your tool calls — use append_narrative instead. \
   Provide three arguments: \
     acting_entity_id — the entity_id of the player whose turn this is. \
     shared_text — third-person prose for all other players \
       (e.g. "Thorin swings his axe — the goblin staggers back."). \
     personal_text — second-person prose addressed directly to the acting player \
       (e.g. "You swing your axe hard — the goblin staggers, its eyes going wide.").
5. When a new player joins an existing scene (you receive a "new_player_joined" event), \
   incorporate them into the current narrative — describe their arrival and place them \
   using place_entity. Do NOT re-initialize the scene.

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
- After resolving all mechanics with tools, call append_narrative with acting_entity_id, \
  shared_text, and personal_text. This is required — do not skip it.

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

## Location
- set_player_location: Update a player's location context shown in their prompt. Args: entity_id, location_area (required), location_sub.

## Narrative (call last, required every turn)
- append_narrative: Write this turn's narrative. Args: acting_entity_id (required), \
shared_text (required, third-person for all other players), \
personal_text (required, second-person addressed directly to the acting player).\
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
    content = resp.choices[0].message.content or ""
    if not content.strip():
        log.warning("empty LLM response — dumping full response object:\n%s", resp)
    return content


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
    agent_cfg = cfg.get("agent", {})
    max_iter = int(agent_cfg.get("max_iterations", 10))
    max_empty_retries = int(agent_cfg.get("empty_retries", 0))
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
            if not response.strip() and max_empty_retries > 0:
                max_empty_retries -= 1
                log.warning(
                    "empty response with no tool calls — nudging (%d retries left)",
                    max_empty_retries,
                )
                messages.append({"role": "assistant", "content": response})
                messages.append({"role": "user", "content": (
                    "Your response was empty. You MUST resolve this turn. "
                    "Use <tool_call> blocks to call your DM tools, then call "
                    "append_narrative as your final tool call. Do not return "
                    "an empty response."
                )})
                continue

            # No tool calls — this is the final narrative response
            log.info("no tool calls in iteration %d — turn resolved.", iteration)
            last_response = response
            break

        log.info("found %d tool call(s)", len(tool_calls))

        # Execute each tool call and collect results
        tool_results = []

        for tc in tool_calls:
            name = tc["name"]
            args = tc["arguments"]
            log.info("executing: %s(%s)", name, json.dumps(args))

            result = await call_dm_tool(client, base_url, name, args)

            log.info("result: %s", json.dumps(result)[:200])
            tool_results.append(f"<tool_response>\n{json.dumps(result, indent=2)}\n</tool_response>")

        # Append the assistant's tool calls, then tool results as a
        # separate user message so the model sees new input to respond to
        # rather than thinking it already finished its turn.
        messages.append({"role": "assistant", "content": response})
        messages.append({"role": "user", "content": "\n\n".join(tool_results)})
        last_response = response

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

                    # Store the full DM response (including tool calls and
                    # results) in chat history so the LLM sees its own
                    # tool-calling pattern on subsequent turns and continues
                    # to call append_narrative reliably.
                    if dm_response:
                        log.info("storing DM response in history (%d chars).", len(dm_response))
                    await client.post(
                        f"{base_url}/api/dm/append-history",
                        json={"role": "assistant", "content": dm_response},
                    )

                    # Signal turn completion
                    await client.post(f"{base_url}/api/dm/turn-complete")
                    log.info("turn complete.")

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
