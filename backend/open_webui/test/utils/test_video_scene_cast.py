import asyncio
import json
import sys
from types import SimpleNamespace

import pytest

from open_webui.utils import game_master
from open_webui.utils.videos import scene_cast


NPCS = [
    {"npc_id": "n1", "name": "Marek", "card": "a nephew", "file_id": "portrait-marek"},
    {"npc_id": "n2", "name": "Nell", "card": "a courier", "file_id": "portrait-nell"},
]
CONVERSATION = [
    {"role": "assistant", "content": "Marek leaves. Nell stays beside Sam."},
    {"role": "user", "content": "Sam asks her about Marek."},
    {"role": "assistant", "content": "She shrugs. 'He has gone home.'"},
]


def test_cast_check_uses_the_selected_scene_and_context_without_portraits_or_plans():
    messages = scene_cast.build_scene_cast_messages(NPCS, CONVERSATION)
    data = json.loads(messages[1]["content"])
    assert data["selected_scene"] == CONVERSATION[-1]["content"]
    assert data["earlier_conversation"][0]["text"] == CONVERSATION[0]["content"]
    assert [n["npc_id"] for n in data["candidates"]] == ["n1", "n2"]
    assert "portrait-marek" not in messages[1]["content"]
    assert "left before this scene" in messages[0]["content"]
    assert "planned to arrive in a future reply" in messages[0]["content"]


def test_cast_check_strips_hidden_thinking_and_uses_only_nearby_context():
    conversation = [{"role": "assistant", "content": "old scene"}] * 10 + [
        {"role": "assistant", "content": "<think>Marek might return.</think>Nell waits."}
    ]
    data = json.loads(scene_cast.build_scene_cast_messages(NPCS, conversation)[1]["content"])
    assert len(data["earlier_conversation"]) == 8
    assert data["selected_scene"] == "Nell waits."


def test_departed_npc_is_not_sent_and_the_remaining_portrait_keeps_its_owner():
    assert scene_cast.parse_scene_cast('{"npc_ids": ["n2"]}', NPCS) == [NPCS[1]]
    assert scene_cast.parse_scene_cast('{"npc_ids": []}', NPCS) == []


def test_cast_answer_preserves_roster_order_and_deduplicates_ids():
    assert scene_cast.parse_scene_cast(
        '```json\n{"npc_ids": ["n2", "n1", "n2"]}\n```', NPCS
    ) == NPCS


@pytest.mark.parametrize(
    "raw",
    ["{}", '{"npc_ids": "n1"}', '{"npc_ids": [null]}',
     '{"npc_ids": ["unknown"]}', "not JSON"],
)
def test_malformed_cast_cannot_restore_the_entire_roster(raw):
    with pytest.raises(ValueError):
        scene_cast.parse_scene_cast(raw, NPCS)


@pytest.mark.parametrize(
    "answer, expected",
    [
        ('{"npc_ids": ["n2"]}', [NPCS[1]]),
        ('{"npc_ids": []}', []),
        ('{"npc_ids": ["unknown"]}', []),
        ("no readable cast", []),
        (None, []),
    ],
)
def test_runtime_checks_cast_with_base_model_and_never_falls_back_to_stale_refs(
    monkeypatch, answer, expected
):
    calls = []

    async def complete(request, **kwargs):
        calls.append(kwargs)
        if answer is None:
            raise RuntimeError("model unavailable")
        return {"choices": [{"message": {"content": answer}}]}

    monkeypatch.setitem(
        sys.modules, "open_webui.utils.chat",
        SimpleNamespace(generate_chat_completion=complete),
    )
    models = {
        "persona": {"info": {"base_model_id": "base"}},
        "base": {"owned_by": "ollama"},
    }
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(MODELS=models)))
    result = asyncio.run(
        scene_cast.filter_scene_npcs(
            request, SimpleNamespace(id="user"), "persona", NPCS, CONVERSATION
        )
    )
    assert result == expected
    assert calls[0]["form_data"]["model"] == "base"
    assert calls[0]["bypass_filter"] is True
    assert calls[0]["bypass_system_prompt"] is True


def test_departure_scene_can_still_use_a_portrait_but_future_planned_npcs_are_not_candidates():
    state = {
        "npcs": [
            {"id": "n1", "name": "Marek", "status": "off_stage"},
            {"id": "n2", "name": "Nell", "status": "planned"},
        ]
    }
    portraits = {"n1": {"name": "Marek", "status": "ready", "file_id": "f1"}}
    assert game_master.scene_npcs(state, portraits) == []
    candidates = game_master.scene_npcs(state, portraits, include_departed=True)
    assert [(n["npc_id"], n["file_id"]) for n in candidates] == [("n1", "f1")]


@pytest.mark.parametrize(
    "message_id, expected, attached",
    [
        ("a2", [], []), ("a2b", ["Marek"], []), ("missing", [], []), (None, [], []),
        ("a2b", [], [{"name": " marek ", "kind": "character"}]),
    ],
)
def test_scene_lookup_stays_on_selected_branch_and_does_not_mutate_the_plan(
    monkeypatch, message_id, expected, attached
):
    messages = {
        "a1": {
            "id": "a1", "parentId": None, "role": "assistant",
            "content": "Marek arrives.",
        },
        "a2": {
            "id": "a2", "parentId": "a1", "role": "assistant",
            "content": "Marek left. Sam waits alone.", "model": "base",
        },
        "a2b": {
            "id": "a2b", "parentId": "a1", "role": "assistant",
            "content": "Marek sits beside Sam.", "model": "base",
        },
    }
    state = {"npcs": [{"id": "n1", "name": "Marek", "status": "on_stage"}]}
    index = [{"id": "e1", "message_id": "a1", "has_state": True, "created_at": 1}]
    gm = SimpleNamespace(
        get_entry_index=lambda *_: index,
        get_entry=lambda *_: SimpleNamespace(state=state, model="base"),
        get_portraits=lambda *_: [
            SimpleNamespace(npc_id="n1", name="Marek", status="ready", file_id="f1")
        ],
    )
    monkeypatch.setitem(
        sys.modules, "open_webui.models.game_master", SimpleNamespace(GameMaster=gm)
    )
    monkeypatch.setitem(
        sys.modules, "open_webui.models.video_characters",
        SimpleNamespace(VideoCharacters=SimpleNamespace(get_for_chat=lambda *_: attached)),
    )
    monkeypatch.setitem(
        sys.modules, "open_webui.models.chats",
        SimpleNamespace(Chats=SimpleNamespace(get_messages_map_by_chat_id=lambda *_: messages)),
    )

    def chain(mapping, leaf):
        result = []
        while leaf:
            result.insert(0, mapping[leaf])
            leaf = mapping[leaf]["parentId"]
        return result

    monkeypatch.setitem(
        sys.modules, "open_webui.utils.misc", SimpleNamespace(get_message_list=chain)
    )

    async def check(request, user, model_id, candidates, conversation):
        assert model_id == "base"
        assert [m["id"] for m in conversation] == ["a1", message_id]
        if attached:
            assert candidates == []
            return []
        answer = '{"npc_ids": ["n1"]}' if message_id == "a2b" else '{"npc_ids": []}'
        return scene_cast.parse_scene_cast(answer, candidates)

    monkeypatch.setattr(scene_cast, "filter_scene_npcs", check)
    result = asyncio.run(
        game_master.get_scene_npcs(object(), SimpleNamespace(id="user"), "chat", message_id)
    )
    assert [n["name"] for n in result] == expected
    assert state["npcs"][0]["status"] == "on_stage"
