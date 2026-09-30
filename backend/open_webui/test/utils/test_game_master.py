from open_webui.utils.game_master import (
    apply_plan,
    build_gm_messages,
    find_state_entry_id,
    parse_gm_reply,
    render_director_notes,
    resolve_gm_model,
)

# A small branching chat:
#   u1 -> a1 -> u2 -> a2
#               u2 -> a2b   (a regenerated reply)
MESSAGES = {
    "u1": {"id": "u1", "parentId": None, "role": "user"},
    "a1": {"id": "a1", "parentId": "u1", "role": "assistant"},
    "u2": {"id": "u2", "parentId": "a1", "role": "user"},
    "a2": {"id": "a2", "parentId": "u2", "role": "assistant"},
    "a2b": {"id": "a2b", "parentId": "u2", "role": "assistant"},
}


def entry(id, message_id, created_at, has_state=True, error=""):
    return {
        "id": id,
        "message_id": message_id,
        "created_at": created_at,
        "has_state": has_state,
        "error": error,
    }


def test_reply_uses_the_nearest_earlier_pass_on_its_branch():
    index = [entry("root", "", 1), entry("e1", "a1", 2), entry("e2", "a2", 3)]
    # Replying to u2 (whichever branch) uses the pass made after a1.
    assert find_state_entry_id(index, MESSAGES, "u2") == "e1"
    # A pass redoing a2b starts from a1's state, not from its sibling a2.
    assert find_state_entry_id(index, MESSAGES, "a2b", inclusive=False) == "e1"
    # And a2's own entry is what applies after a2.
    assert find_state_entry_id(index, MESSAGES, "a2") == "e2"


def test_root_entry_covers_a_chat_before_any_turn_pass():
    index = [entry("root", "", 1)]
    assert find_state_entry_id(index, MESSAGES, "u1") == "root"
    assert find_state_entry_id(index, MESSAGES, None) == "root"
    # Nothing sits strictly above the root.
    assert find_state_entry_id(index, MESSAGES, None, inclusive=False) is None


def test_failed_passes_are_skipped_and_the_newest_success_wins():
    index = [
        entry("old", "a1", 1),
        entry("reroll", "a1", 5),
        entry("failed", "a1", 9, has_state=False, error="timeout"),
    ]
    assert find_state_entry_id(index, MESSAGES, "u2") == "reroll"


def test_no_entries_means_no_state():
    assert find_state_entry_id([], MESSAGES, "a2") is None


def test_plan_is_a_patch_that_keeps_what_it_leaves_out():
    state = {
        "premise": "A debt comes due.",
        "characters": {
            "Sam": {
                "want": "get out of the city",
                "stance": {"leaving together": {"position": "no", "price": "the truth"}},
            }
        },
        "threads": [{"id": "t1", "title": "the ledger", "status": "rising"}],
    }
    plan = {
        "update": {
            "characters": {"Sam": {"fear": "being found", "stance": {"the key": {"position": "hide it"}}}},
            "threads": [{"id": "t1", "status": "climax"}, {"title": "a rival"}],
        }
    }

    result = apply_plan(state, plan)

    assert result["premise"] == "A debt comes due."
    sam = result["characters"]["Sam"]
    assert sam["want"] == "get out of the city"
    assert sam["fear"] == "being found"
    assert sam["stance"]["leaving together"]["price"] == "the truth"
    assert sam["stance"]["the key"]["position"] == "hide it"
    assert result["threads"][0] == {"id": "t1", "title": "the ledger", "status": "climax"}
    assert result["threads"][1] == {"title": "a rival", "id": "t2"}
    # The input is never mutated.
    assert state["threads"][0]["status"] == "rising"


def test_plan_removals_and_null_stances():
    state = {
        "characters": {"Sam": {"stance": {"a": {"position": "x"}, "b": {"position": "y"}}}},
        "npcs": [{"id": "n1", "name": "Marek"}, {"id": "n2", "name": "Ivo"}],
        "player_directives": ["no violence against animals"],
    }
    plan = {
        "update": {"characters": {"Sam": {"stance": {"a": None}}}},
        "remove": {"npcs": ["n2"], "player_directives": ["no violence against animals"]},
    }

    result = apply_plan(state, plan)

    assert result["characters"]["Sam"]["stance"] == {"b": {"position": "y"}}
    assert [n["id"] for n in result["npcs"]] == ["n1"]
    assert "player_directives" not in result


def test_clocks_and_npc_statuses_are_kept_in_range():
    plan = {
        "update": {
            "clocks": [{"id": "c1", "label": "patience", "filled": 9, "size": 6}],
            "npcs": [{"id": "n1", "name": "Marek", "status": "lurking"}],
            "tension": {"current": 14, "target": "6"},
        }
    }
    result = apply_plan(None, plan)
    assert result["clocks"][0]["filled"] == 6
    assert result["npcs"][0]["status"] == "planned"
    assert result["tension"] == {"current": 10, "target": 6}


def test_directives_accumulate_without_duplicates():
    result = apply_plan(
        {"player_directives": ["keep it light"]},
        {"update": {"player_directives": ["keep it light", "no spiders"]}},
    )
    assert result["player_directives"] == ["keep it light", "no spiders"]


def test_parse_reads_tagged_reasoning_and_plan():
    raw = (
        "<gm_reasoning>\nSam gave in too easily.\n</gm_reasoning>\n"
        '<gm_plan>\n{"observations": ["cave: Sam"], "note": "Sam: pull back.",}\n</gm_plan>'
    )
    reasoning, plan = parse_gm_reply(raw)
    assert reasoning == "Sam gave in too easily."
    assert plan["note"] == "Sam: pull back."
    assert plan["observations"] == ["cave: Sam"]


def test_parse_falls_back_to_a_fenced_block():
    raw = 'Thinking it over.\n```json\n{"note": "Hold your ground.", "update": {}}\n```'
    reasoning, plan = parse_gm_reply(raw)
    assert reasoning == "Thinking it over."
    assert plan["note"] == "Hold your ground."


def test_parse_rejects_a_reply_without_a_plan():
    reasoning, plan = parse_gm_reply("I would rather not.")
    assert plan is None
    assert reasoning == "I would rather not."


def test_notes_carry_on_stage_npc_cards_only():
    state = {
        "npcs": [
            {"id": "n1", "name": "Marek", "status": "on_stage", "card": "the landlord's nephew",
             "want": "the ledger", "voice": "formal", "look": "grey wool overcoat"},
            {"id": "n2", "name": "Ivo", "status": "planned", "card": "a buyer"},
        ]
    }
    notes = render_director_notes("Sam: hold your ground.", state)
    assert notes.startswith("<director_notes>")
    assert notes.endswith("</director_notes>")
    assert "Sam: hold your ground." in notes
    assert "Marek (NPC, in the scene): the landlord's nephew." in notes
    assert "Looks: grey wool overcoat." in notes
    assert "Ivo" not in notes


def test_no_notes_when_the_gm_has_nothing_to_say():
    assert render_director_notes("", {"npcs": []}) is None


def test_gm_messages_mark_the_player_and_state_the_task():
    messages = build_gm_messages(
        characters=[
            {"id": "c1", "name": "Sam", "description": "A courier.", "kind": "character"},
            {"id": "c2", "name": "Alex", "description": "The player.", "kind": "character",
             "state": "soaked through"},
            {"id": "l1", "name": "Harbour", "description": "Cold and loud.", "kind": "location"},
        ],
        player_character_id="c2",
        config={"intensity": "ruthless", "agenda": "a slow betrayal"},
        chat_instructions="Keep it PG-13.",
        state=None,
        last_note="",
        conversation=[
            {"role": "user", "content": "Let's go to your place."},
            {"role": "assistant", "content": "<think>hmm</think>Sam shrugs."},
        ],
    )
    system, user = messages[0]["content"], messages[1]["content"]
    assert "Ruthless." in system
    assert "Alex [PLAYER" in user
    assert "Sam [PLAYER" not in user
    assert "currently: soaked through" in user
    assert "- Harbour: Cold and loud." in user
    assert "a slow betrayal" in user
    assert "Keep it PG-13." in user
    assert "[Player (Alex)]\nLet's go to your place." in user
    assert "[Story]\nSam shrugs." in user
    assert "hmm" not in user
    assert "session zero" in user


def test_gm_uses_the_base_model_behind_a_persona():
    models = {
        "rp-persona": {"info": {"base_model_id": "@preset/rp"}},
        "@preset/rp": {},
    }
    assert resolve_gm_model(models, "rp-persona") == "@preset/rp"
    assert resolve_gm_model(models, "@preset/rp") == "@preset/rp"
    # A base model that is no longer registered falls back to the chat's own id.
    assert resolve_gm_model({"x": {"info": {"base_model_id": "gone"}}}, "x") == "x"
