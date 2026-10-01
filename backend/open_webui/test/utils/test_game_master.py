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


def test_table_talk_is_a_conversation_ending_on_the_players_words():
    from open_webui.utils.game_master import build_talk_messages

    messages = build_talk_messages(
        characters=[{"id": "c1", "name": "Sam", "description": "A courier.", "kind": "character"}],
        player_character_id="",
        config={"intensity": "firm"},
        chat_instructions="",
        state={"secrets": [{"id": "s1", "text": "Sam has the key"}]},
        conversation=[{"role": "user", "content": "Hello."}],
        talk_history=[{"player": "Where is this going?", "gm": "Somewhere dark."}],
        talk="Can Sam's brother come back?",
    )
    assert messages[0]["role"] == "system"
    assert "out of character" in messages[0]["content"]
    assert "never reveal secrets" in messages[0]["content"]
    assert "Sam has the key" in messages[0]["content"]
    assert messages[1:] == [
        {"role": "user", "content": "Where is this going?"},
        {"role": "assistant", "content": "Somewhere dark."},
        {"role": "user", "content": "Can Sam's brother come back?"},
    ]


def test_talk_answers_lose_stray_markup():
    from open_webui.utils.game_master import clean_talk_reply

    raw = "<think>hm</think><gm_reply>Not yet: the betrayal needs room.</gm_reply><gm_plan>{}</gm_plan>"
    assert clean_talk_reply(raw) == "Not yet: the betrayal needs room."
    assert clean_talk_reply("Plain answer.") == "Plain answer."


def test_pass_kind_survives_attached_characters():
    # Regression: the character loop once reused the name `kind`, so with characters
    # attached every consult, reroll and table-talk pass ran as a plain turn review.
    base = dict(
        characters=[
            {"id": "c1", "name": "Sam", "description": "A courier.", "kind": "character"},
            {"id": "l1", "name": "Harbour", "description": "Cold.", "kind": "location"},
        ],
        player_character_id="",
        config={},
        chat_instructions="",
        state={"premise": "x"},
        last_note="",
        conversation=[],
    )
    consult = build_gm_messages(**base, kind="consult")[1]["content"]
    assert "take another look" in consult
    talk_plan = build_gm_messages(
        **base, kind="talk_plan", talk="Bring back the brother.", talk_reply="Soon."
    )[1]["content"]
    assert "Bring back the brother." in talk_plan
    assert "## You answered them\nSoon." in talk_plan
    reroll = build_gm_messages(**base, kind="reroll", rejected_note="Sam: hold firm.")[1]["content"]
    assert "rerolled" in reroll


def test_prompt_task_follows_the_kind_of_pass():
    base = dict(
        characters=[],
        player_character_id="",
        config={},
        chat_instructions="",
        last_note="Sam: hold firm.",
        conversation=[],
    )
    reroll = build_gm_messages(**base, state={"premise": "x"}, kind="reroll", rejected_note="Sam: hold firm.")
    assert "rerolled" in reroll[1]["content"]

    consult = build_gm_messages(**base, state={"premise": "x"}, kind="consult")
    assert "take another look" in consult[1]["content"]

    first_talk_plan = build_gm_messages(**base, state=None, kind="talk_plan", talk="hi", talk_reply="hello")
    assert "session zero" in first_talk_plan[1]["content"]
    assert "you answered them" in first_talk_plan[1]["content"]


def test_a_planless_reply_gets_one_repair_turn():
    from open_webui.utils.game_master import REPAIR_PROMPT, build_repair_messages

    pass_messages = [{"role": "system", "content": "GM"}, {"role": "user", "content": "plan"}]
    repair = build_repair_messages(pass_messages, "Sam is wavering; she should hold firm.")
    assert repair[:2] == pass_messages
    assert repair[2] == {"role": "assistant", "content": "Sam is wavering; she should hold firm."}
    assert repair[3] == {"role": "user", "content": REPAIR_PROMPT}
    # The recovered plan parses on its own.
    _, plan = parse_gm_reply('<gm_plan>{"note": "Sam: hold firm."}</gm_plan>')
    assert plan == {"note": "Sam: hold firm."}


def test_every_pass_ends_by_restating_the_reply_shape():
    common = dict(
        characters=[], player_character_id="", config={}, chat_instructions="",
        state=None, last_note="", conversation=[],
    )
    turn = build_gm_messages(**common)[1]["content"]
    assert turn.rstrip().endswith("without it nothing you decide reaches the story.")
    talk_plan = build_gm_messages(**common, kind="talk_plan", talk="hi", talk_reply="hello")
    assert talk_plan[1]["content"].rstrip().endswith("reaches the story.")
