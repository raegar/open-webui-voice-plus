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


# --- Stages 2-6 ---------------------------------------------------------------

from open_webui.utils.game_master import (  # noqa: E402
    count_replies_since,
    describe_changes,
    npcs_needing_portraits,
    parse_gm_talk,
    scene_npcs,
)


def test_changes_read_as_lines_with_spoilers_flagged():
    before = {
        "characters": {"Sam": {"stance": {"leaving": {"position": "maybe"}}}},
        "clocks": [{"id": "c1", "label": "patience", "filled": 3, "size": 6}],
        "npcs": [{"id": "n1", "name": "Marek", "status": "planned", "card": "a nephew"}],
    }
    after = {
        "characters": {
            "Sam": {"fear": "being found", "stance": {"leaving": {"position": "no", "price": "the truth"}}}
        },
        "clocks": [{"id": "c1", "label": "patience", "filled": 6, "size": 6, "on_full": "police"}],
        "npcs": [
            {"id": "n1", "name": "Marek", "status": "on_stage", "card": "a nephew"},
            {"id": "n2", "name": "Ivo", "status": "planned", "card": "a buyer"},
        ],
        "secrets": [{"id": "s1", "text": "Sam has the key", "known_by": ["Sam"]}],
        "threads": [{"id": "t1", "title": "the buyer", "status": "seeded", "gm_only": True}],
        "player_requests": [{"id": "r1", "text": "a rival", "status": "planned"}],
    }
    lines = {line["text"]: line["spoiler"] for line in describe_changes(before, after)}

    assert lines["Sam on leaving: maybe → no (moves only if: the truth)"] is False
    assert lines["Sam fears: being found"] is True
    assert lines["patience: 3 → 6 of 6"] is False
    assert lines["patience is full: police"] is False
    assert lines["Marek: planned → on stage"] is False
    assert lines["New NPC waiting in the wings: Ivo: a buyer"] is True
    assert lines["Secret: Sam has the key"] is True
    assert lines["New thread: the buyer (seeded)"] is True
    assert lines["Your request: a rival (planned)"] is False


def test_no_changes_no_lines():
    state = {"clocks": [{"id": "c1", "label": "x", "filled": 1, "size": 4}]}
    assert describe_changes(state, state) == []


def test_cadence_counts_replies_back_to_the_last_pass():
    # a1 had a pass; a2 and a2b are replies after it.
    assert count_replies_since(MESSAGES, "a2", "a1") == 1
    assert count_replies_since(MESSAGES, "u2", "a1") == 0
    assert count_replies_since(MESSAGES, "a2", None) == 2


def test_portraits_are_wanted_for_new_faces_on_stage_only():
    state = {
        "npcs": [
            {"id": "n1", "name": "Marek", "status": "on_stage", "look": "grey coat"},
            {"id": "n2", "name": "Ivo", "status": "planned", "look": "tall"},
            {"id": "n3", "name": "Nell", "status": "on_stage", "look": ""},
            {"id": "n4", "name": "Rook", "status": "on_stage", "look": "bald"},
        ]
    }
    # n4's portrait was drawn for someone else who had that id on another branch.
    needed = npcs_needing_portraits(state, {"n4": "Someone else"})
    assert [npc["id"] for npc in needed] == ["n1", "n4"]
    assert npcs_needing_portraits(state, {"n1": "Marek", "n4": "Rook"}) == []


def test_scene_npcs_carry_ready_portraits_only():
    state = {
        "npcs": [
            {"id": "n1", "name": "Marek", "status": "on_stage", "card": "a nephew", "look": "grey coat"},
            {"id": "n2", "name": "Ivo", "status": "on_stage", "look": "tall"},
            {"id": "n3", "name": "Nell", "status": "off_stage", "look": "red scarf"},
        ]
    }
    portraits = {
        "n1": {"name": "Marek", "status": "ready", "file_id": "f1"},
        "n2": {"name": "Ivo", "status": "running", "file_id": ""},
    }
    scene = scene_npcs(state, portraits)
    assert [(n["name"], n["file_id"]) for n in scene] == [("Marek", "f1"), ("Ivo", "")]
    assert scene[0]["look"] == "grey coat"


def test_table_talk_answer_is_split_from_the_plan():
    raw = (
        "<gm_reply>\nGood idea, but not yet: the betrayal needs room first.\n</gm_reply>\n"
        "<gm_reasoning>They want a rival.</gm_reasoning>\n"
        '<gm_plan>{"update": {"player_requests": [{"text": "a rival"}]}, "note": "Marek: watch Alex."}</gm_plan>'
    )
    answer, rest = parse_gm_talk(raw)
    assert answer == "Good idea, but not yet: the betrayal needs room first."
    reasoning, plan = parse_gm_reply(rest)
    assert reasoning == "They want a rival."
    assert "Good idea" not in reasoning
    assert apply_plan(None, plan)["player_requests"] == [{"text": "a rival", "id": "r1"}]


def test_prompt_task_follows_the_kind_of_pass():
    base = dict(
        characters=[],
        player_character_id="",
        config={},
        chat_instructions="",
        last_note="Sam: hold firm.",
        conversation=[],
    )
    talk = build_gm_messages(
        **base,
        state={"premise": "x"},
        kind="table_talk",
        talk="Can Sam's brother come back?",
        talk_history=[{"player": "Where is this going?", "gm": "Somewhere dark."}],
    )
    assert "<gm_reply>" in talk[0]["content"]
    assert "Can Sam's brother come back?" in talk[1]["content"]
    assert "[You]\nSomewhere dark." in talk[1]["content"]

    reroll = build_gm_messages(**base, state={"premise": "x"}, kind="reroll", rejected_note="Sam: hold firm.")
    assert "rerolled" in reroll[1]["content"]
    assert "<gm_reply>" not in reroll[0]["content"]

    consult = build_gm_messages(**base, state={"premise": "x"}, kind="consult")
    assert "take another look" in consult[1]["content"]

    first_talk = build_gm_messages(**base, state=None, kind="table_talk", talk="hello")
    assert "session zero" in first_talk[1]["content"]
