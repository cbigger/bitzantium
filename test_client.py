"""
test_client.py — Dumb test client for Bitzantium auth + character creation endpoints.

Requires auth_wrapper.py running on :8080.

Walks through:
    1. POST /api/register                — get an API key
    2. POST /api/creation-options         — browse species/backgrounds/classes
    3. POST /api/creation-details         — detail for a chosen triplet
    4. POST /api/preview-character        — validate + preview a character sheet
    5. POST /api/confirm-character        — persist the character
    6. POST /api/create-character (legacy) — one-shot creation (separate account)

Each step prints the full response to console.
"""

import json
import sys

import httpx

AUTH_URL = "http://localhost:8080"


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


def auth(api_key: str) -> dict:
    return {"api_key": api_key}


def main():
    client = httpx.Client(timeout=30.0)

    # ------------------------------------------------------------------
    # 1. Register
    # ------------------------------------------------------------------
    resp = client.post(f"{AUTH_URL}/api/register")
    pp("REGISTER", resp)
    if resp.status_code != 201:
        print("Registration failed, aborting.")
        sys.exit(1)
    api_key = resp.json()["api_key"]
    print(f"\n  >> API key: {api_key}")

    # ------------------------------------------------------------------
    # 2. Creation options (browse)
    # ------------------------------------------------------------------
    resp = client.post(
        f"{AUTH_URL}/api/creation-options",
        json={"auth": auth(api_key)},
    )
    pp("CREATION OPTIONS", resp)

    if resp.status_code != 200:
        print("Failed to get creation options, aborting.")
        sys.exit(1)

    options = resp.json()

    # Pick the first species, background, and class available
    species = options.get("species", [])
    backgrounds = options.get("backgrounds", [])
    classes = options.get("classes", [])

    if not species or not backgrounds or not classes:
        print("No creation options returned, aborting.")
        sys.exit(1)

    creature_id = species[0]["creature_id"]
    background_id = backgrounds[0]["background_id"]
    class_id = classes[0]["class_id"]

    # Races are nested inside the species entry
    races = species[0].get("races", [])
    race_id = races[0]["race_id"] if races else ""

    print(f"\n  >> Picking: creature={creature_id}, background={background_id}, class={class_id}, race={race_id}")

    # ------------------------------------------------------------------
    # 3. Creation details (drill down)
    # ------------------------------------------------------------------
    resp = client.post(
        f"{AUTH_URL}/api/creation-details",
        json={
            "auth": auth(api_key),
            "creature_id": creature_id,
            "background_id": background_id,
            "class_id": class_id,
            "race_id": race_id or None,
        },
    )
    pp("CREATION DETAILS", resp)

    if resp.status_code != 200:
        print("Failed to get creation details, aborting.")
        sys.exit(1)

    details = resp.json()

    # ------------------------------------------------------------------
    # Build choices from details
    # ------------------------------------------------------------------
    choices = build_choices_from_details(creature_id, background_id, class_id, race_id, details)
    print(f"\n  >> Built choices:")
    print(json.dumps(choices, indent=2))

    # ------------------------------------------------------------------
    # 4. Preview character
    # ------------------------------------------------------------------
    resp = client.post(
        f"{AUTH_URL}/api/preview-character",
        json={"auth": auth(api_key), "choices": choices},
    )
    pp("PREVIEW CHARACTER", resp)

    # ------------------------------------------------------------------
    # 5. Confirm character
    # ------------------------------------------------------------------
    resp = client.post(
        f"{AUTH_URL}/api/confirm-character",
        json={"auth": auth(api_key), "choices": choices},
    )
    pp("CONFIRM CHARACTER", resp)

    if resp.status_code == 201:
        entity_id = resp.json().get("entity_id")
        print(f"\n  >> Character created! entity_id: {entity_id}")

    # ------------------------------------------------------------------
    # 6. Legacy create-character (needs a fresh account)
    # ------------------------------------------------------------------
    print("\n\n--- Testing legacy /api/create-character ---")

    resp = client.post(f"{AUTH_URL}/api/register")
    pp("REGISTER (legacy account)", resp)
    if resp.status_code != 201:
        print("Legacy registration failed, skipping.")
        return
    legacy_key = resp.json()["api_key"]

    # Reuse same choices but with a different name
    legacy_choices = dict(choices)
    legacy_choices["name"] = "Legacy Test Character"

    resp = client.post(
        f"{AUTH_URL}/api/create-character",
        json={"auth": auth(legacy_key), "choices": legacy_choices},
    )
    pp("CREATE CHARACTER (legacy)", resp)

    if resp.status_code == 201:
        entity_id = resp.json().get("entity_id")
        print(f"\n  >> Legacy character created! entity_id: {entity_id}")

    print("\n\nDone.")


def build_choices_from_details(creature_id, background_id, class_id, race_id, details):
    """Build a valid CharacterChoices dict from the creation-details response.
    Picks the first valid option for every choice field."""

    ability_assignments = {
        "strength": 15,
        "dexterity": 14,
        "constitution": 13,
        "intelligence": 12,
        "wisdom": 10,
        "charisma": 8,
    }

    # --- Skill choices ---
    # Collect all chooseable skill pools from class and background
    cls_detail = details.get("class", {})
    bg_detail = details.get("background", {})

    auto_skills = set(
        cls_detail.get("skill_proficiencies_auto", [])
        + bg_detail.get("skill_proficiencies_auto", [])
    )

    skill_choices = []
    for pool_group in [cls_detail.get("skill_proficiencies_choose", []),
                       bg_detail.get("skill_proficiencies_choose", [])]:
        for entry in pool_group:
            count = entry["count"]
            available = [s for s in entry["from"] if s not in auto_skills and s not in skill_choices]
            skill_choices.extend(available[:count])

    # --- Language choices ---
    species_detail = details.get("species", {})
    race_detail = species_detail.get("race", {})
    base_languages = set(species_detail.get("languages", []))
    base_languages.update(race_detail.get("languages", []))

    bonus_count = (
        species_detail.get("bonus_languages", 0)
        + race_detail.get("bonus_languages", 0)
        + bg_detail.get("bonus_languages", 0)
    )

    common_languages = details.get("common_languages", [])
    language_choices = [
        lang for lang in common_languages
        if lang not in base_languages
    ][:bonus_count]

    # --- Cantrips ---
    level_1 = cls_detail.get("level_1", {})
    num_cantrips = level_1.get("cantrips_knowable", 0)
    cantrips_available = cls_detail.get("cantrips", [])
    cantrip_choices = [c["spell_id"] for c in cantrips_available[:num_cantrips]]

    # --- Spells ---
    num_spells = level_1.get("spells_knowable") or 0
    spells_available = cls_detail.get("level_1_spells", [])
    spell_choices = [s["spell_id"] for s in spells_available[:num_spells]]

    # --- Racial ASI choices ---
    racial_asi_choices = []
    choice_count = race_detail.get("choice_asi_count", 0)
    # Pick abilities not already boosted by fixed ASIs
    fixed_asis = {
        asi["ability"] for asi in race_detail.get("ability_score_increases", [])
        if asi["ability"] != "choice"
    }
    filler_abilities = [a for a in ["strength", "dexterity", "constitution",
                                     "intelligence", "wisdom", "charisma"]
                        if a not in fixed_asis]
    racial_asi_choices = filler_abilities[:choice_count]

    return {
        "name": "Test Character",
        "description": "A test character created by the test client.",
        "alignment": "True Neutral",
        "creature_id": creature_id,
        "race_id": race_id,
        "background_id": background_id,
        "class_id": class_id,
        "subclass_id": "",
        "level": 1,
        "ability_method": "standard_array",
        "ability_assignments": ability_assignments,
        "racial_asi_choices": racial_asi_choices,
        "skill_choices": skill_choices,
        "language_choices": language_choices,
        "cantrip_choices": cantrip_choices,
        "spell_choices": spell_choices,
    }


if __name__ == "__main__":
    main()
