"""
player_mcp.py
=============
Player Agent MCP Server for Bitzantium.

Exposes dynamically-gated player tools to the player LLM agent via the MCP
protocol (stdio transport). The DM engine calls set_turn_context() once per
turn, after state mutations, to push narrative context into the server.

DM integration (shared-module pattern — same process):

    import player_mcp

    player_mcp.set_turn_context(
        entity_id     = "aldric",
        story_so_far  = "...",
        location_area = "The Aether",
        location_sub  = None,   # non-None → indoors sub-room name
        quest_log     = None,   # TBD feature
    )

Run (stdio transport):
    python player_mcp.py
"""

import asyncio
import json
from typing import Any, Optional

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

import loader
import player_tools
import registry
import state as state_module


# ---------------------------------------------------------------------------
# Turn context — set by the DM engine once per turn
# ---------------------------------------------------------------------------

_entity_id: Optional[str] = None
_story_so_far: str = ""
_location_area: str = ""
_location_sub: Optional[str] = None
_quest_log: Optional[str] = None


def set_turn_context(
    entity_id: str,
    story_so_far: str,
    location_area: str,
    location_sub: Optional[str] = None,
    quest_log: Optional[str] = None,
) -> None:
    """Push narrative context for the current turn.

    Must be called AFTER state.update_character() so that tool gating and the
    stats block reflect the latest CharacterState.

    Args:
        entity_id:     The character whose turn it is.
        story_so_far:  Concatenated level-up narrative summaries.
        location_area: Name of the current room / area.
        location_sub:  Sub-room description (indoors only); None when outdoors.
        quest_log:     Active quest text (TBD feature); None if no quest.
    """
    global _entity_id, _story_so_far, _location_area, _location_sub, _quest_log
    _entity_id = entity_id
    _story_so_far = story_so_far
    _location_area = location_area
    _location_sub = location_sub
    _quest_log = quest_log


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def _identity_line(cs) -> str:
    sheet = cs.sheet

    # Optional race prefix — present only when race_id is set
    race_str = ""
    if sheet.race_id:
        race = loader.get_race(sheet.race_id)
        if race:
            race_str = f"{race.name} "

    # Creature type (species name; fall back to ID string)
    creature_str = sheet.creature_id
    if sheet.creature_id:
        creature = loader.get_creature(sheet.creature_id)
        if creature:
            creature_str = creature.name

    # Class(es): "Level N [Subclass ]Class" joined with " / "
    class_parts: list[str] = []
    for entry in sheet.classes:
        cls_def = loader.get_class(entry.class_id)
        cls_name = cls_def.name if cls_def else entry.class_id.title()
        if entry.subclass_id:
            sub_def = loader.get_subclass(entry.subclass_id)
            if sub_def:
                cls_name = f"{sub_def.name} {cls_name}"
        class_parts.append(f"Level {entry.level} {cls_name}")

    return f"You are {sheet.name}, a {race_str}{creature_str} {' / '.join(class_parts)}."


def _location_line() -> str:
    if _location_sub:
        return f"You find yourself in {_location_sub} in {_location_area}."
    return f"You find yourself in {_location_area}."


def _tools_block(cs) -> str:
    tools = registry.get_available_tools(cs)
    lines = ["TOOLS AVAILABLE"]
    if not tools:
        lines.append("  (none — you are unable to act)")
    else:
        for t in tools:
            lines.append(f"  {t['name']}: {t['description']}")
    return "\n".join(lines)


def _stats_block(cs) -> str:
    sheet = cs.sheet
    lines: list[str] = []

    # Vitals
    hp_str = f"{sheet.hp_current}/{sheet.hp_max}"
    if sheet.hp_temp:
        hp_str += f" (+{sheet.hp_temp} temp)"
    lines.append(f"HP {hp_str}  |  AC {sheet.armor_class}  |  Speed {sheet.speed} ft")

    # Conditions
    if sheet.conditions:
        lines.append("Conditions: " + ", ".join(c.value.upper() for c in sheet.conditions))

    # Active effects
    if sheet.active_effects:
        lines.append("Effects: " + ", ".join(e.name for e in sheet.active_effects))

    # Exhaustion
    if sheet.exhaustion_level:
        lines.append(f"Exhaustion: {sheet.exhaustion_level}")

    # Ability scores with modifiers
    a = sheet.ability_scores

    def _fmt(score: int) -> str:
        m = (score - 10) // 2
        return f"+{m}" if m >= 0 else str(m)

    lines.append(
        f"STR {a.strength}({_fmt(a.strength)})  "
        f"DEX {a.dexterity}({_fmt(a.dexterity)})  "
        f"CON {a.constitution}({_fmt(a.constitution)})  "
        f"INT {a.intelligence}({_fmt(a.intelligence)})  "
        f"WIS {a.wisdom}({_fmt(a.wisdom)})  "
        f"CHA {a.charisma}({_fmt(a.charisma)})"
    )

    # Class resources (rage, ki, bardic inspiration, etc.)
    if sheet.class_resources:
        lines.append(
            "Resources: " + ", ".join(
                f"{r.name}: {r.current}/{r.max}" for r in sheet.class_resources
            )
        )

    # Spell slots
    if sheet.spell_slots:
        slot_parts = [
            f"L{lvl}: {entry.remaining}/{entry.total}"
            for lvl, entry in sorted(sheet.spell_slots.items())
            if entry.total > 0
        ]
        if slot_parts:
            lines.append("Spell Slots: " + "  ".join(slot_parts))

    # Spell names (look up display name from ability registry; fall back to ID)
    if sheet.spells:
        spell_names: list[str] = []
        for ref in sheet.spells:
            ability = loader.get_ability(ref.spell_id)
            spell_names.append(ability.name if ability else ref.spell_id)
        lines.append("Spells: " + ", ".join(spell_names))

    # Class feature / ability names (not full descriptions — those live in the sheet)
    if sheet.features:
        lines.append("Abilities: " + ", ".join(f.name for f in sheet.features))

    # Equipped slots only — inventory is excluded
    eq = sheet.equipment
    slot_display = [
        ("main hand", eq.main_hand),
        ("off hand",  eq.off_hand),
        ("armor",     eq.armor),
        ("helmet",    eq.helmet),
        ("boots",     eq.boots),
        ("gloves",    eq.gloves),
        ("ring 1",    eq.ring_1),
        ("ring 2",    eq.ring_2),
        ("amulet",    eq.amulet),
        ("back",      eq.back),
    ]
    equipped = [
        f"{label}: {item.item_base.name}"
        for label, item in slot_display
        if item is not None
    ]
    if equipped:
        lines.append("Equipped: " + " | ".join(equipped))

    return "\n".join(lines)


def build_player_prompt() -> str:
    """Assemble the full system prompt for the current turn.

    Section order matches the player agent prompt schema:

        You are <name>, a [<race>]<creature> <class>.
        <description>
        <story so far>
        You find yourself in [<sub> in ]<area>.
        [QUEST LOG
        <quest_log>]
        TOOLS AVAILABLE
          tool_name: description
          …
        HP …  |  AC …  |  Speed …
        Conditions: …
        STR … DEX … …
        Resources: …
        Spell Slots: …
        Spells: …
        Abilities: …
        Equipped: …
    """
    if not _entity_id:
        return "No active entity."
    cs = state_module.get_character(_entity_id)
    if not cs:
        return "No character state found."

    parts: list[str] = [_identity_line(cs)]
    if cs.sheet.description:
        parts.append(cs.sheet.description)
    if _story_so_far:
        parts.append(_story_so_far)
    parts.append(_location_line())
    if _quest_log:
        parts.append(f"QUEST LOG\n{_quest_log}")
    parts.append(_tools_block(cs))
    parts.append(_stats_block(cs))

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

server = Server("bitzantium-player")


@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    """Return the gated tool list for the active entity.

    Tools are rebuilt on every request from registry.get_available_tools so
    that class, condition, economy, and resource gates always reflect the
    current CharacterState.
    """
    if not _entity_id:
        return []
    cs = state_module.get_character(_entity_id)
    if not cs:
        return []
    return [
        types.Tool(
            name=t["name"],
            description=t["description"],
            inputSchema=t["inputSchema"],
        )
        for t in registry.get_available_tools(cs)
    ]


@server.call_tool()
async def handle_call_tool(
    name: str,
    arguments: dict[str, Any] | None,
) -> list[types.TextContent]:
    """Execute a player tool.

    Delegates to player_tools.execute_player_tool, which re-validates all
    four gate layers before spending economy or building the DM snapshot.
    Invalid calls return {valid: false, error: ...} rather than raising.
    """
    if not _entity_id:
        return [types.TextContent(type="text", text=json.dumps({"error": "No active entity"}))]
    result = player_tools.execute_player_tool(_entity_id, name, arguments or {})
    return [types.TextContent(type="text", text=json.dumps(result, indent=2))]


@server.list_prompts()
async def handle_list_prompts() -> list[types.Prompt]:
    return [
        types.Prompt(
            name="player_context",
            description="Current turn system prompt for the player agent",
        )
    ]


@server.get_prompt()
async def handle_get_prompt(
    name: str,
    arguments: dict[str, str] | None,
) -> types.GetPromptResult:
    """Return the current turn system prompt.

    The MCP client fetches this at the start of each turn and injects it as
    the system message for the player LLM. Call set_turn_context() before
    each turn so the content is fresh.
    """
    return types.GetPromptResult(
        description="Player agent system prompt for the current turn",
        messages=[
            types.PromptMessage(
                role="user",
                content=types.TextContent(type="text", text=build_player_prompt()),
            )
        ],
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def _run() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(_run())
