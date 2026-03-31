"""
test_dm.py — Test client for DM REST endpoints on the game server.

Requires game_server.py running on :8081.

Walks through each /api/dm/* endpoint and prints the response.
No LLM calls — just exercises the HTTP layer.
"""

import json
import sys

import httpx

GAME_URL = "http://localhost:8081"
DM_API_KEY = "dm-dev-key-change-me"

HEADERS = {"Authorization": f"Bearer {DM_API_KEY}"}


def pp(label: str, resp: httpx.Response):
    """Pretty-print a response."""
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"  {resp.request.method} {resp.request.url}")
    print(f"  Status: {resp.status_code}")
    print(f"{'='*60}")
    try:
        print(json.dumps(resp.json(), indent=2))
    except Exception:
        print(resp.text)


def main():
    client = httpx.Client(timeout=30.0, headers=HEADERS)

    # ------------------------------------------------------------------
    # 1. Poll (should be pending=false on fresh DB)
    # ------------------------------------------------------------------
    resp = client.post(f"{GAME_URL}/api/dm/poll")
    pp("DM POLL (expect pending=false)", resp)

    # ------------------------------------------------------------------
    # 2. List tools
    # ------------------------------------------------------------------
    resp = client.get(f"{GAME_URL}/api/dm/tools")
    pp("DM TOOLS", resp)

    if resp.status_code == 200:
        tools = resp.json().get("tools", [])
        print(f"\n  >> {len(tools)} tools available")
        for t in tools[:5]:
            print(f"     - {t['name']}: {t['description'][:60]}")
        if len(tools) > 5:
            print(f"     ... and {len(tools) - 5} more")

    # ------------------------------------------------------------------
    # 3. Call a tool — get_scene_state (read-only, always safe)
    # ------------------------------------------------------------------
    resp = client.post(
        f"{GAME_URL}/api/dm/tool",
        json={"tool": "get_scene_state", "arguments": {}},
    )
    pp("DM TOOL: get_scene_state", resp)

    # ------------------------------------------------------------------
    # 4. Call a tool — get_turn_state (read-only)
    # ------------------------------------------------------------------
    resp = client.post(
        f"{GAME_URL}/api/dm/tool",
        json={"tool": "get_turn_state", "arguments": {}},
    )
    pp("DM TOOL: get_turn_state", resp)

    # ------------------------------------------------------------------
    # 5. Call a tool — init_scene (mutating, sets up a test scene)
    # ------------------------------------------------------------------
    resp = client.post(
        f"{GAME_URL}/api/dm/tool",
        json={
            "tool": "init_scene",
            "arguments": {
                "area_id": "test_room",
                "area_name": "Test Chamber",
                "area_description": "A plain stone room used for testing.",
                "light_level": "bright",
            },
        },
    )
    pp("DM TOOL: init_scene", resp)

    # ------------------------------------------------------------------
    # 6. Append history
    # ------------------------------------------------------------------
    resp = client.post(
        f"{GAME_URL}/api/dm/append-history",
        json={"role": "assistant", "content": "Test narrative from DM."},
    )
    pp("DM APPEND HISTORY", resp)

    # ------------------------------------------------------------------
    # 7. Poll again (still pending=false, we didn't set it)
    # ------------------------------------------------------------------
    resp = client.post(f"{GAME_URL}/api/dm/poll")
    pp("DM POLL (still pending=false, but history should exist)", resp)

    # ------------------------------------------------------------------
    # 8. Turn complete (no-op when not pending, but should succeed)
    # ------------------------------------------------------------------
    resp = client.post(f"{GAME_URL}/api/dm/turn-complete")
    pp("DM TURN COMPLETE", resp)

    # ------------------------------------------------------------------
    # 9. Auth failure test — wrong key
    # ------------------------------------------------------------------
    bad_client = httpx.Client(timeout=10.0, headers={"Authorization": "Bearer wrong-key"})
    resp = bad_client.post(f"{GAME_URL}/api/dm/poll")
    pp("DM POLL (bad key — expect 403)", resp)

    # ------------------------------------------------------------------
    # 10. Auth failure test — no header
    # ------------------------------------------------------------------
    no_auth_client = httpx.Client(timeout=10.0)
    resp = no_auth_client.post(f"{GAME_URL}/api/dm/poll")
    pp("DM POLL (no auth — expect 401)", resp)

    print("\n\nDone.")


if __name__ == "__main__":
    main()
