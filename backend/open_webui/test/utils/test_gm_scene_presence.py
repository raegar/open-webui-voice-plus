"""NPC presence is relative to the player's current scene, not their old room."""

import copy

import pytest

from open_webui.utils.game_master import (
    apply_plan,
    build_gm_messages,
    describe_changes,
    plan_needs_repair,
    render_director_notes,
    scene_npcs,
)


STATE = {
    "scene": {"location": "the sitting room", "npc_ids": ["n1", "n2", "n3"]},
    "npcs": [
        {"id": "n1", "name": "Marek", "status": "on_stage", "look": "grey coat"},
        {"id": "n2", "name": "Nell", "status": "on_stage", "look": "red scarf"},
        {"id": "n3", "name": "Ivo", "status": "on_stage", "look": "blue cap"},
        {"id": "n4", "name": "Rook", "status": "entering", "look": "bald"},
        {"id": "n5", "name": "Ada", "status": "planned", "look": "dark curls"},
    ],
}


def statuses(state):
    return {npc["name"]: npc["status"] for npc in state["npcs"]}


def test_player_leaving_alone_marks_everyone_left_behind_off_stage_without_explicit_updates():
    before = copy.deepcopy(STATE)
    result = apply_plan(STATE, {"scene": {"location": "the corridor", "npc_ids": []}})

    assert statuses(result) == {
        "Marek": "off_stage", "Nell": "off_stage", "Ivo": "off_stage",
        "Rook": "entering", "Ada": "planned",
    }
    assert result["scene"] == {"location": "the corridor", "npc_ids": []}
    assert result["npcs"][0]["look"] == "grey coat"
    assert STATE == before
    notes = render_director_notes("Continue in the corridor.", result)
    assert "NPCs in the scene, played by you" not in notes
    lines = [change["text"] for change in describe_changes(STATE, result)]
    assert "Marek: on stage → off stage" in lines


def test_companion_who_follows_stays_present_while_the_rest_are_left_behind():
    result = apply_plan(STATE, {"scene": {"location": "outside", "npc_ids": ["n2"]}})
    assert statuses(result)["Nell"] == "on_stage"
    assert statuses(result)["Marek"] == "off_stage"
    assert statuses(result)["Ivo"] == "off_stage"
    assert [npc["name"] for npc in scene_npcs(result, {})] == ["Nell", "Rook"]


def test_silent_npcs_stay_present_when_location_and_full_roster_are_unchanged():
    result = apply_plan(STATE, {"scene": STATE["scene"]})
    assert statuses(result) == statuses(STATE)


def test_returning_to_the_room_restores_its_cast_without_recreating_them():
    away = apply_plan(STATE, {"scene": {"location": "the corridor", "npc_ids": []}})
    returned = apply_plan(away, {"scene": STATE["scene"]})
    assert returned["npcs"] == STATE["npcs"]


def test_current_roster_overrides_a_stale_on_stage_patch():
    result = apply_plan(STATE, {
        "scene": {"location": "the corridor", "npc_ids": []},
        "update": {"npcs": [{"id": "n1", "status": "on_stage", "want": "finish tea"}]},
    })
    assert statuses(result)["Marek"] == "off_stage"
    assert result["npcs"][0]["want"] == "finish tea"


def test_current_roster_can_include_an_npc_added_in_this_plan():
    plan = {
        "scene": {"location": "the corridor", "npc_ids": ["n6"]},
        "update": {"npcs": [{"id": "n6", "name": "Jay", "status": "on_stage"}]},
    }
    assert plan_needs_repair(STATE, plan) is False
    result = apply_plan(STATE, plan)
    assert statuses(result)["Jay"] == "on_stage"
    assert statuses(result)["Marek"] == "off_stage"


@pytest.mark.parametrize("snapshot", [
    None, {}, {"location": "corridor"}, {"location": "", "npc_ids": []},
    {"location": "corridor", "npc_ids": "n2"},
    {"location": "corridor", "npc_ids": [None]},
    {"location": "corridor", "npc_ids": ["unknown"]},
])
def test_missing_or_invalid_roster_needs_repair_and_cannot_clear_existing_presence(snapshot):
    plan = {"scene": snapshot, "note": "Keep going."}
    assert plan_needs_repair(STATE, plan) is True
    assert statuses(apply_plan(STATE, plan)) == statuses(STATE)


def test_no_npcs_means_no_presence_roster_is_needed_for_older_plans():
    assert plan_needs_repair({}, {"note": "Keep going."}) is False
    assert plan_needs_repair({}, None) is True


def test_gm_is_asked_for_player_location_and_a_complete_roster_after_walking_out():
    messages = build_gm_messages(
        characters=[{"id": "player", "name": "Sam", "kind": "character"}],
        player_character_id="player", config={}, chat_instructions="", state=STATE,
        last_note="", conversation=[
            {"role": "user", "content": "I walk out of the room, leaving them to their tea."},
            {"role": "assistant", "content": "You are alone in the corridor."},
        ],
    )
    assert "[Player (Sam)]" in messages[1]["content"]
    assert "complete npc_ids roster there now" in messages[1]["content"]
    assert '"scene" is a complete current snapshot, not a patch' in messages[0]["content"]
    assert "even though they have not moved" in messages[0]["content"]
    assert "Do not assume they follow" in render_director_notes("", STATE)
