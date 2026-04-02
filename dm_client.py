"""
dm_client.py — Bitzantium DM agent client.

Two-pass pipeline:
  Pass 1 (Resolution) — DM resolves player actions using tools (no narrative).
  Pass 2 (Narrative)  — Narrator writes prose from the resolution results.

Self-contained polling agent that connects to the game server's /api/dm/*
endpoints, waits for player turns, resolves them, and signals completion.

    .venv/bin/python3 dm_client.py [--config dm_client.toml]

Env var overrides (take precedence over dm_client.toml):
    BITZ_DM_PROVIDER     -> [llm].base_url
    BITZ_DM_MODEL        -> [llm].model
    BITZ_DM_LLM_API_KEY  -> [llm].api_key
    BITZ_DM_API_KEY      -> [dm].api_key
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
# System prompts
# ---------------------------------------------------------------------------

DM_RESOLUTION_PROMPT = """\
You are the Dungeon Master for a D&D 5th Edition game running on the Bitzantium engine.

# Your Role
You receive player turn data — the raw tool calls and snapshots from their actions — \
and you resolve what actually happens in the game world. You have final authority over \
all outcomes. A separate narrator will write the prose — your job is to decide what \
happens and make it real using your tools.

# How a Turn Works
1. You receive a player's turn (their declared actions) OR a system event (e.g. new player \
   joined an existing scene).
2. You decide what actually happens — whether attacks hit, how spells resolve, what the \
   world does in response.
3. You use your DM tools to make it real: roll attacks, apply damage, apply conditions, \
   move entities, spend spell slots and resources.
4. When a new player joins an existing scene (you receive a "new_player_joined" event), \
   place them using place_entity. Do NOT re-initialize the scene.
5. When you are done resolving, write a brief DM summary of what happened and why. This \
   is NOT narrative prose — it is a factual summary for the narrator to work from. \
   Example: "Player moved 30ft north. Threw a pen at the shadow — improvised weapon, \
   rolled 8 vs AC 12, miss. Cast prestidigitation on the air ahead — cantrip resolves, \
   no visible effect. No enemies present."

# Your Authority
- You decide advantage/disadvantage based on narrative context.
- You decide whether a declared action is appropriate in context.
- You can trigger opportunity attacks, reactions, or environmental effects.
- Player tool calls confirm mechanical legality; you confirm everything else.

# Important Rules
- Resolve ALL mechanical effects with tools — do not just describe damage without calling \
  apply_damage, do not describe movement without calling move_entity, etc.
- When a player casts a spell, call spend_spell_slot for the appropriate level.
- When a player uses a class resource (rage, ki, etc.), call spend_resource.
- Call tick_turn_end for the acting entity after resolving their turn to decrement effects.
- Use get_scene_state or get_character_state if you need more context before resolving.

# Tool Call Format
To call a tool, emit a tool_call block:

<tool_call>
{"name": "tool_name", "arguments": {"arg1": "value1", "arg2": "value2"}}
</tool_call>

The result will be injected as a tool_response block. You may call multiple tools in \
sequence across multiple rounds. When you have finished resolving all mechanics, stop \
emitting tool_call blocks and write your DM summary.

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
- set_player_location: Update a player's location context shown in their prompt. Args: entity_id, location_area (required), location_sub.\
"""

NARRATOR_PROMPT = """\
You are the narrator for a D&D game. You receive a summary of what a player tried \
to do and what actually happened (as resolved by the Dungeon Master). Your job is to \
write vivid, concise prose describing the events.

You must write two versions of the same events:

1. SHARED — Third-person prose shown to all other players. Refer to the acting \
   player by name. Example: "Thorin swings his axe hard — the goblin staggers back, \
   clutching its side."

2. PERSONAL — Second-person prose addressed directly to the acting player. Example: \
   "You swing your axe hard — the goblin staggers back, clutching its side, eyes wide \
   with shock."

Format your response exactly like this:

[SHARED]
<third-person prose here>

[PERSONAL]
<second-person prose here>

Rules:
- Be vivid but concise. A few sentences, not paragraphs.
- Do not invent mechanical outcomes — only narrate what the DM summary tells you happened.
- Do not mention dice rolls, armor class, hit points, or other game mechanics.
- Maintain consistent tone and style with previous narrative.\
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
# Narrative response parsing
# ---------------------------------------------------------------------------

_SHARED_RE = re.compile(r"\[SHARED\]\s*(.*?)(?=\[PERSONAL\])", re.DOTALL)
_PERSONAL_RE = re.compile(r"\[PERSONAL\]\s*(.*)", re.DOTALL)


def parse_narrative(text: str) -> tuple[str, str]:
    """Extract shared_text and personal_text from narrator response."""
    shared_m = _SHARED_RE.search(text)
    personal_m = _PERSONAL_RE.search(text)
    shared = shared_m.group(1).strip() if shared_m else ""
    personal = personal_m.group(1).strip() if personal_m else ""
    return shared, personal


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
# Pass 1: DM Resolution (tool-calling loop)
# ---------------------------------------------------------------------------

async def run_resolution_pass(
    client: httpx.AsyncClient,
    base_url: str,
    llm: OpenAI,
    poll_data: dict,
    cfg: dict,
) -> str:
    """
    DM resolves the player's turn using tools.

    Returns the DM's full response text (including all tool calls and results
    across iterations). Raises on empty response.
    """
    max_iter = int(cfg.get("agent", {}).get("max_iterations", 10))
    chat_history = poll_data.get("messages", [])

    messages: list[dict] = [{"role": "system", "content": DM_RESOLUTION_PROMPT}]

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
        log.info("[resolution] iteration %d/%d — %d messages", iteration, max_iter, len(messages))

        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None, llm_complete, llm, messages, cfg,
        )
        log.info("[resolution] llm response: %d chars", len(response))
        log.debug("[resolution] llm output:\n%s", response)

        if not response.strip():
            raise RuntimeError(
                f"DM resolution returned empty response on iteration {iteration}"
            )

        tool_calls = parse_tool_calls(response)

        if not tool_calls:
            log.info("[resolution] no tool calls in iteration %d — resolution complete.", iteration)
            last_response = response
            break

        log.info("[resolution] found %d tool call(s)", len(tool_calls))

        tool_results = []
        for tc in tool_calls:
            name = tc["name"]
            args = tc["arguments"]
            log.info("[resolution] executing: %s(%s)", name, json.dumps(args))

            result = await call_dm_tool(client, base_url, name, args)
            log.info("[resolution] result: %s", json.dumps(result)[:200])
            tool_results.append(f"<tool_response>\n{json.dumps(result, indent=2)}\n</tool_response>")

        messages.append({"role": "assistant", "content": response})
        messages.append({"role": "user", "content": "\n\n".join(tool_results)})
        last_response = response

    else:
        log.warning("[resolution] max iterations (%d) reached.", max_iter)

    return last_response


# ---------------------------------------------------------------------------
# Pass 2: Narrator (single LLM call, no tools)
# ---------------------------------------------------------------------------

async def run_narrative_pass(
    client: httpx.AsyncClient,
    base_url: str,
    llm: OpenAI,
    poll_data: dict,
    dm_summary: str,
    cfg: dict,
) -> tuple[str, str]:
    """
    Narrator writes prose from the DM's resolution summary.

    Uses its own separate history for voice/style consistency.
    Returns (shared_text, personal_text). Raises on empty/unparseable response.
    """
    narrator_history = poll_data.get("narrator_messages", [])

    messages: list[dict] = [{"role": "system", "content": NARRATOR_PROMPT}]

    for entry in narrator_history:
        role = entry.get("role", "user")
        content = entry.get("content")
        if isinstance(content, dict):
            content = json.dumps(content, indent=2)
        elif not isinstance(content, str):
            content = str(content)
        messages.append({"role": role, "content": content})

    # Current turn input for the narrator
    messages.append({"role": "user", "content": dm_summary})

    log.info("[narrator] calling LLM — %d messages", len(messages))

    loop = asyncio.get_running_loop()
    response = await loop.run_in_executor(
        None, llm_complete, llm, messages, cfg,
    )
    log.info("[narrator] llm response: %d chars", len(response))
    log.debug("[narrator] llm output:\n%s", response)

    if not response.strip():
        raise RuntimeError("Narrator returned empty response")

    shared, personal = parse_narrative(response)

    if not shared or not personal:
        log.error(
            "[narrator] failed to parse narrative — shared=%d chars, personal=%d chars. "
            "Raw response:\n%s",
            len(shared), len(personal), response,
        )
        raise RuntimeError(
            f"Narrator response could not be parsed into shared/personal sections"
        )

    # Store narrator exchange in its own history
    await client.post(
        f"{base_url}/api/dm/append-narrator-history",
        json={"role": "user", "content": dm_summary},
    )
    await client.post(
        f"{base_url}/api/dm/append-narrator-history",
        json={"role": "assistant", "content": response},
    )

    return shared, personal


# ---------------------------------------------------------------------------
# Orchestrator — two-pass DM turn
# ---------------------------------------------------------------------------

def _extract_entity_id(poll_data: dict) -> str:
    """Pull the acting entity_id from the last user message in chat history."""
    for msg in reversed(poll_data.get("messages", [])):
        content = msg.get("content")
        if isinstance(content, dict):
            eid = content.get("entity_id")
            if eid:
                return eid
    return "unknown"


async def run_dm_turn(
    client: httpx.AsyncClient,
    base_url: str,
    llm: OpenAI,
    poll_data: dict,
    cfg: dict,
) -> str:
    """
    Two-pass DM turn resolution.

    Pass 1: DM resolves mechanics via tool calls.
    Pass 2: Narrator writes prose from the resolution summary.
    Then append_narrative is called from code.

    Returns the DM's resolution response for history storage.
    """
    # Pass 1: Resolution
    dm_response = await run_resolution_pass(client, base_url, llm, poll_data, cfg)

    # Pass 2: Narrative
    shared, personal = await run_narrative_pass(
        client, base_url, llm, poll_data, dm_response, cfg,
    )

    # Store narrative via append_narrative tool (called from code, not LLM)
    entity_id = _extract_entity_id(poll_data)
    log.info("appending narrative for %s — shared=%d chars, personal=%d chars",
             entity_id, len(shared), len(personal))

    await call_dm_tool(client, base_url, "append_narrative", {
        "acting_entity_id": entity_id,
        "shared_text": shared,
        "personal_text": personal,
    })

    return dm_response


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
                resp = await client.post(f"{base_url}/api/dm/poll")
                resp.raise_for_status()
                poll_result = resp.json()

                pending = poll_result.get("pending", False)

                if not pending:
                    log.debug("no pending turn.")
                else:
                    log.info("pending turn detected — resolving.")

                    dm_response = await run_dm_turn(
                        client, base_url, llm, poll_result, cfg,
                    )

                    # Store the full DM resolution in chat history so the
                    # LLM sees its own tool-calling pattern on future turns.
                    await client.post(
                        f"{base_url}/api/dm/append-history",
                        json={"role": "assistant", "content": dm_response},
                    )

                    await client.post(f"{base_url}/api/dm/turn-complete")
                    log.info("turn complete.")

            except Exception as e:
                log.error("poll cycle error: %s", e, exc_info=True)

            await asyncio.sleep(poll_interval)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(config_path: str):
    cfg = load_config(config_path)

    level = logging.DEBUG
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
