"""
monitor.py
==========
Real-time game monitor for Bitzantium.

Polls the database directly and streams a live view of the game to the terminal.

Usage:
    python monitor.py              # info mode — chat-log style
    python monitor.py --debug      # debug mode — full LLM I/O
    python monitor.py --replay     # start from beginning of history, not just new events
    python monitor.py --interval 2 # set poll interval in seconds (default 1)
"""

import argparse
import json
import sys
import time
import textwrap
from datetime import datetime, timezone

import db

# ---------------------------------------------------------------------------
# ANSI helpers
# ---------------------------------------------------------------------------

USE_COLOR = sys.stdout.isatty()

RESET   = "\033[0m"   if USE_COLOR else ""
BOLD    = "\033[1m"   if USE_COLOR else ""
DIM     = "\033[2m"   if USE_COLOR else ""
YELLOW  = "\033[33m"  if USE_COLOR else ""
CYAN    = "\033[36m"  if USE_COLOR else ""
GREEN   = "\033[32m"  if USE_COLOR else ""
MAGENTA = "\033[35m"  if USE_COLOR else ""
BLUE    = "\033[34m"  if USE_COLOR else ""
RED     = "\033[31m"  if USE_COLOR else ""
WHITE   = "\033[37m"  if USE_COLOR else ""
GRAY    = "\033[90m"  if USE_COLOR else ""

_PLAYER_PALETTE = [GREEN, MAGENTA, BLUE, RED, WHITE]
_player_color_map: dict[str, str] = {}


def _player_color(entity_id: str) -> str:
    if entity_id not in _player_color_map:
        idx = len(_player_color_map) % len(_PLAYER_PALETTE)
        _player_color_map[entity_id] = _PLAYER_PALETTE[idx]
    return _player_color_map[entity_id]


def _ts() -> str:
    now = datetime.now(timezone.utc).strftime("%H:%M:%S")
    return f"{GRAY}[{now}]{RESET}"


def _wrap(text: str, prefix: str = "  ") -> str:
    """Wrap long text with an indent for continuation lines."""
    width = 100
    lines = text.splitlines()
    wrapped = []
    for line in lines:
        if len(line) <= width:
            wrapped.append(line)
        else:
            wrapped.extend(textwrap.wrap(line, width=width, subsequent_indent=prefix))
    return "\n".join(wrapped)


def _print(line: str) -> None:
    print(line, flush=True)


# ---------------------------------------------------------------------------
# Name cache
# ---------------------------------------------------------------------------

_name_cache: dict[str, str] = {}  # entity_id -> character name


def _build_name_cache() -> None:
    with db.SessionLocal() as session:
        rows = session.query(db.Character).all()
        for row in rows:
            state = row.character_state
            if isinstance(state, dict):
                name = state.get("sheet", {}).get("name", row.entity_id)
            else:
                name = row.entity_id
            _name_cache[row.entity_id] = name


def _resolve(entity_id: str) -> str:
    return _name_cache.get(entity_id, entity_id)


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def _fmt_separator(label: str, color: str = CYAN) -> str:
    return f"{_ts()} {BOLD}{color}--- {label} ---{RESET}"


def _fmt_turn_change(new_snap: dict, old_snap: dict) -> list[str]:
    lines = []
    old_player = old_snap.get("player_turn_entity_id")
    new_player = new_snap.get("player_turn_entity_id")
    old_pending = old_snap.get("dm_turn_pending", False)
    new_pending = new_snap.get("dm_turn_pending", False)

    if new_pending and not old_pending:
        lines.append(_fmt_separator("DM resolving...", YELLOW))
    elif not new_pending and old_pending:
        pass  # turn-complete; the player line below will follow

    if new_player and new_player != old_player:
        tick = new_snap.get("tick", 0)
        name = _resolve(new_player)
        color = _player_color(new_player)
        lines.append(_fmt_separator(f"Tick {tick} — {name}'s turn", color))

    return lines


def _fmt_player_turn(content: dict, debug: bool) -> list[str]:
    lines = []
    entity_id = content.get("entity_id", "?")
    name = _resolve(entity_id)
    color = _player_color(entity_id)
    actions: list[dict] = content.get("actions", [])
    turn_num = content.get("turn_number", "?")

    tool_names = [a.get("tool", "?") for a in actions]
    tools_str = ", ".join(tool_names) if tool_names else "(no actions)"
    lines.append(
        f"{_ts()} {BOLD}{color}{name}{RESET} used: {color}{tools_str}{RESET}"
        f"  {GRAY}(turn {turn_num}){RESET}"
    )

    if debug:
        for action in actions:
            tool = action.get("tool", "?")
            args = action.get("arguments", {})
            result = action.get("result", {})
            lines.append(
                f"  {DIM}{GRAY}↳ {tool} args={json.dumps(args, separators=(',',':'))}{RESET}"
            )
            lines.append(
                f"  {DIM}{GRAY}  result={json.dumps(result, separators=(',',':'))}{RESET}"
            )

    return lines


def _fmt_new_player(content: dict) -> list[str]:
    name = content.get("name", content.get("entity_id", "?"))
    entity_id = content.get("entity_id", "")
    if entity_id:
        _name_cache[entity_id] = name
        _ = _player_color(entity_id)  # assign color on join
    return [f"{_ts()} {BOLD}{CYAN}⟶  {name} joined the game{RESET}"]


def _fmt_dm_response(content: str, debug: bool) -> list[str]:
    """Format a DM assistant message (shared_text stored in history)."""
    if not content or not content.strip():
        return []
    lines = []
    if debug:
        # Show the full content including any tool_call/tool_response XML
        lines.append(f"{_ts()} {DIM}{GRAY}[DM raw response]{RESET}")
        for ln in content.splitlines():
            lines.append(f"  {DIM}{GRAY}{ln}{RESET}")
    # In both modes we also show it as narrative (avoids duplication in info mode
    # since narrative_segments is the authoritative narrative source — but the
    # dm_chat_history assistant message is just the shared_text, so it's safe to
    # show here in debug as additional context; in info mode we skip it and let
    # poll_narratives handle display).
    return lines


def _fmt_narrative(seg: db.NarrativeSegment, debug: bool) -> list[str]:
    lines = []
    actor = _resolve(seg.acting_entity_id)
    color = _player_color(seg.acting_entity_id)

    # Shared text — shown in both modes
    shared = (seg.shared_text or "").strip()
    if shared:
        lines.append(f"{_ts()} {BOLD}{YELLOW}[DM]{RESET}")
        for ln in _wrap(shared).splitlines():
            lines.append(f"  {YELLOW}{ln}{RESET}")

    if debug:
        personal = (seg.personal_text or "").strip()
        if personal:
            lines.append(
                f"  {DIM}{GRAY}[personal → {actor}]{RESET}"
            )
            for ln in _wrap(personal, prefix="    ").splitlines():
                lines.append(f"    {DIM}{color}{ln}{RESET}")

    return lines


# ---------------------------------------------------------------------------
# Poll functions
# ---------------------------------------------------------------------------

def _snapshot(row: db.TurnStateRow) -> dict:
    return {
        "tick": row.tick,
        "turn_index": row.turn_index,
        "dm_turn_pending": row.dm_turn_pending,
        "player_turn_entity_id": row.player_turn_entity_id,
    }


def poll_turn_state(session, prev: dict) -> tuple[dict, list[str]]:
    row = session.query(db.TurnStateRow).filter(db.TurnStateRow.id == 1).first()
    if row is None:
        return prev, []
    new = _snapshot(row)
    output = _fmt_turn_change(new, prev) if new != prev else []
    return new, output


def poll_dm_chat(session, last_id: int, debug: bool) -> tuple[int, list[str]]:
    rows = (
        session.query(db.DmChatMessage)
        .filter(db.DmChatMessage.id > last_id)
        .order_by(db.DmChatMessage.id)
        .all()
    )
    output: list[str] = []
    new_last = last_id

    for row in rows:
        new_last = max(new_last, row.id)
        content = row.content
        role = row.role

        if role == "user":
            if isinstance(content, dict):
                t = content.get("type")
                if t == "player_turn":
                    output.extend(_fmt_player_turn(content, debug))
                elif t == "new_player_joined":
                    output.extend(_fmt_new_player(content))
                    _build_name_cache()  # refresh on join
                elif debug:
                    output.append(
                        f"{_ts()} {DIM}{GRAY}[chat #{row.id} user] "
                        f"{json.dumps(content)}{RESET}"
                    )
            elif debug:
                output.append(
                    f"{_ts()} {DIM}{GRAY}[chat #{row.id} user] {content}{RESET}"
                )

        elif role == "assistant":
            if isinstance(content, str):
                output.extend(_fmt_dm_response(content, debug))
            elif debug:
                output.append(
                    f"{_ts()} {DIM}{GRAY}[chat #{row.id} assistant] "
                    f"{json.dumps(content)}{RESET}"
                )

    return new_last, output


def poll_narratives(session, last_id: int, debug: bool) -> tuple[int, list[str]]:
    rows = (
        session.query(db.NarrativeSegment)
        .filter(db.NarrativeSegment.id > last_id)
        .order_by(db.NarrativeSegment.id)
        .all()
    )
    output: list[str] = []
    new_last = last_id

    for row in rows:
        new_last = max(new_last, row.id)
        output.extend(_fmt_narrative(row, debug))

    return new_last, output


# ---------------------------------------------------------------------------
# Startup banner
# ---------------------------------------------------------------------------

def _print_banner(debug: bool) -> None:
    mode = "DEBUG" if debug else "INFO"
    _print(f"\n{BOLD}{CYAN}{'═' * 60}{RESET}")
    _print(f"{BOLD}{CYAN}  Bitzantium Monitor  [{mode}]{RESET}")
    _print(f"{BOLD}{CYAN}{'═' * 60}{RESET}")

    with db.SessionLocal() as session:
        turn_row = session.query(db.TurnStateRow).filter(db.TurnStateRow.id == 1).first()
        if turn_row:
            order = turn_row.turn_order or []
            names = [f"{_resolve(e)}" for e in order]
            current = _resolve(turn_row.player_turn_entity_id) if turn_row.player_turn_entity_id else "—"
            pending = " (DM resolving)" if turn_row.dm_turn_pending else ""
            _print(f"  Tick:         {turn_row.tick}")
            _print(f"  Turn order:   {', '.join(names) if names else '(none)'}")
            _print(f"  Active:       {current}{pending}")
        else:
            _print("  No game in progress yet.")

    _print(f"{BOLD}{CYAN}{'─' * 60}{RESET}")
    _print(f"  Watching for new events...  (Ctrl+C to exit)\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Real-time Bitzantium game monitor.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Show full LLM I/O: tool call args/results, DM responses, personal narratives.",
    )
    parser.add_argument(
        "--replay", action="store_true",
        help="Start from the beginning of history rather than only new events.",
    )
    parser.add_argument(
        "--interval", type=float, default=1.0, metavar="SECS",
        help="Poll interval in seconds (default: 1).",
    )
    args = parser.parse_args()

    _build_name_cache()
    _print_banner(args.debug)

    with db.SessionLocal() as session:
        if args.replay:
            last_chat_id = 0
            last_narrative_id = 0
        else:
            # Start from current max IDs so we only show new events
            max_chat = session.query(db.DmChatMessage.id).order_by(db.DmChatMessage.id.desc()).first()
            max_narrative = session.query(db.NarrativeSegment.id).order_by(db.NarrativeSegment.id.desc()).first()
            last_chat_id = max_chat[0] if max_chat else 0
            last_narrative_id = max_narrative[0] if max_narrative else 0

        turn_row = session.query(db.TurnStateRow).filter(db.TurnStateRow.id == 1).first()
        prev_turn_snap = _snapshot(turn_row) if turn_row else {}

    try:
        while True:
            try:
                with db.SessionLocal() as session:
                    prev_turn_snap, turn_lines = poll_turn_state(session, prev_turn_snap)
                    last_chat_id, chat_lines = poll_dm_chat(session, last_chat_id, args.debug)
                    last_narrative_id, narrative_lines = poll_narratives(session, last_narrative_id, args.debug)

                for line in turn_lines + chat_lines + narrative_lines:
                    _print(line)

            except Exception as exc:
                _print(f"{_ts()} {RED}[monitor error] {exc}{RESET}")

            time.sleep(args.interval)

    except KeyboardInterrupt:
        _print(f"\n{GRAY}Monitor stopped.{RESET}")


if __name__ == "__main__":
    main()
