import asyncio
import sys
from types import SimpleNamespace

from open_webui.utils import game_master


def test_attached_people_take_precedence_without_confusing_settings_or_similar_names():
    characters = [
        {"name": " Nell ", "kind": "character"},
        SimpleNamespace(name="Alex", kind="character"),
        {"name": "Marek", "kind": "location"},
        {"name": "Rook", "kind": "outfit"},
    ]
    npcs = [
        {"name": "NELL"}, {"name": "Alex"}, {"name": "Alex (2007)"},
        {"name": "Marek"}, {"name": "Rook"}, {"name": " marek "},
    ]
    assert game_master.exclude_attached_npcs(npcs, characters) == npcs[2:5]
    assert len(npcs) == 6


def test_automatic_portraits_skip_attached_character_duplicates(monkeypatch):
    monkeypatch.setitem(sys.modules, "open_webui.models.game_master", SimpleNamespace(
        GameMaster=SimpleNamespace(get_portraits=lambda *_: [])
    ))
    monkeypatch.setitem(sys.modules, "open_webui.models.video_characters", SimpleNamespace(
        VideoCharacters=SimpleNamespace(get_for_chat=lambda *_: [{"name": "Nell"}])
    ))
    drawn = []

    async def queue(request, user, chat_id, npc, **kwargs):
        drawn.append(npc["name"])
        return True

    monkeypatch.setattr(game_master, "queue_npc_portrait", queue)
    state = {"npcs": [
        {"id": "n1", "name": "Nell", "status": "on_stage", "look": "red scarf"},
        {"id": "n2", "name": "Marek", "status": "on_stage", "look": "grey coat"},
    ]}
    asyncio.run(game_master.queue_missing_portraits(
        object(), SimpleNamespace(id="user"), "chat", state
    ))
    assert drawn == ["Marek"]
    assert len(state["npcs"]) == 2
