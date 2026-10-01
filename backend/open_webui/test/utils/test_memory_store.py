import sqlite3
from datetime import date

import pytest

from open_webui.utils.memory_store import MemoryStore, build_filter

SCHEMA = """
CREATE TABLE sessions (session_id TEXT PRIMARY KEY, start_timestamp TEXT NOT NULL);
CREATE TABLE conversations (conversation_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
    start_timestamp TEXT NOT NULL);
CREATE TABLE messages (message_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
    timestamp TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, source_type TEXT,
    embedding BLOB);
"""

ROWS = [
    # id, conversation, timestamp, role, content, source_type
    ("m1", "c1", "2026-09-01T10:00:00+00:00", "user", "Sam hides the key", None),
    ("m2", "c1", "2026-09-01T10:01:00+00:00", "assistant", "Alex finds a 50% discount", None),
    ("m3", "c2", "2026-09-02T23:59:59+00:00", "user", "The harbour at night", "jsonl_import"),
    ("m4", "c3", "2026-09-03T00:00:00+00:00", "assistant", "Sam leaves the harbour", None),
]


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "conversations.db"
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    connection.executemany(
        "INSERT INTO sessions VALUES (?, ?)", [("s1", "x"), ("s2", "x")]
    )
    connection.executemany(
        "INSERT INTO conversations VALUES (?, ?, ?)",
        [("c1", "s1", "x"), ("c2", "s1", "x"), ("c3", "s2", "x")],
    )
    connection.executemany(
        "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, x'00')", ROWS
    )
    connection.commit()
    connection.close()
    return MemoryStore(str(path))


def ids(result):
    return [item["message_id"] for item in result["items"]]


def test_newest_first_with_a_total(store):
    result = store.search()
    assert result["total"] == 4
    assert ids(result) == ["m4", "m3", "m2", "m1"]
    assert ids(store.search(order="oldest", limit=2, offset=1)) == ["m2", "m3"]


def test_text_search_matches_every_word_and_treats_wildcards_literally(store):
    assert ids(store.search(q="sam harbour")) == ["m4"]
    assert ids(store.search(q="SAM")) == ["m4", "m1"]
    assert ids(store.search(q="50%")) == ["m2"]
    assert ids(store.search(q="5_%")) == []


def test_role_source_and_conversation_filters(store):
    assert ids(store.search(role="assistant")) == ["m4", "m2"]
    assert ids(store.search(source="imported")) == ["m3"]
    assert ids(store.search(source="recorded")) == ["m4", "m2", "m1"]
    assert ids(store.search(conversation_id="c1")) == ["m2", "m1"]
    assert store.search(source="imported")["items"][0]["source"] == "imported"


def test_date_range_is_inclusive_whole_days(store):
    assert ids(store.search(since="2026-09-02", until="2026-09-02")) == ["m3"]
    assert ids(store.search(since="2026-09-03")) == ["m4"]
    assert ids(store.search(until="2026-09-01")) == ["m2", "m1"]


def test_a_bad_date_is_a_value_error():
    with pytest.raises(ValueError):
        build_filter(since="last tuesday")


def test_delete_removes_emptied_conversations_and_sessions(store, tmp_path):
    result = store.delete(["m4", "m1", "missing"])
    assert result["deleted"] == 2
    # c3 had only m4, and s2 only c3; c1 still holds m2.
    assert result["conversations_removed"] == 1
    connection = sqlite3.connect(store.path)
    assert [r[0] for r in connection.execute("SELECT conversation_id FROM conversations ORDER BY 1")] == ["c1", "c2"]
    assert [r[0] for r in connection.execute("SELECT session_id FROM sessions")] == ["s1"]
    connection.close()
    assert ids(store.search()) == ["m3", "m2"]


def test_first_delete_of_the_day_backs_up_and_only_three_are_kept(store):
    first = store.delete(["m1"])
    assert first["backup"] == f"conversations-{date.today().strftime('%Y%m%d')}.db"
    assert store.delete(["m2"])["backup"] is None
    # The backup holds the store as it was before the first deletion.
    backup = sqlite3.connect(store.backup_dir() / first["backup"])
    assert backup.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 4
    backup.close()
    for day in (date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)):
        store.backup_if_due(day)
    kept = sorted(p.name for p in store.backup_dir().glob("conversations-*.db"))
    assert len(kept) == 3
    assert "conversations-20260101.db" not in kept


def test_a_missing_store_says_where_it_looked(tmp_path):
    with pytest.raises(FileNotFoundError, match="nowhere.db"):
        MemoryStore(str(tmp_path / "nowhere.db")).summary()


def test_summary_counts_by_source(store):
    summary = store.summary()
    assert (summary["total"], summary["recorded"], summary["imported"]) == (4, 3, 1)


def fake_sources():
    from open_webui.utils.memory_characters import content_key

    # m1 and m2 are in a chat with Sam and Alex attached; m4's chat talks to a persona.
    index = {
        content_key("Sam hides the key"): "chat-1",
        content_key("Alex finds a 50% discount"): "chat-1",
        content_key("Sam leaves the harbour"): "chat-2",
    }
    return index, {"chat-1": ["Sam", "Alex"], "chat-2": ["Marek"]}, ["Sam", "Alex", "Marek"]


def test_memories_carry_their_characters_and_filter_by_them(store):
    assert store.sync_characters(fake_sources) == 4
    # Already linked: nothing new to do.
    assert store.sync_characters(fake_sources) == 0

    by_id = {item["message_id"]: item["characters"] for item in store.search()["items"]}
    assert by_id["m1"] == [{"name": "Alex", "how": "chat"}, {"name": "Sam", "how": "chat"}]
    assert by_id["m4"] == [{"name": "Marek", "how": "chat"}]
    assert by_id["m3"] == []

    assert ids(store.search(include=["Marek"])) == ["m4"]
    assert ids(store.search(include=["Marek", "Alex"])) == ["m4", "m2", "m1"]
    assert ids(store.search(exclude=["Sam"])) == ["m4", "m3"]
    assert ids(store.search(include=["Sam"], exclude=["Marek"], role="assistant")) == ["m2"]

    summary = store.summary()
    assert summary["unindexed"] == 0
    assert {c["name"]: c["count"] for c in summary["characters"]} == {"Alex": 2, "Sam": 2, "Marek": 1}


def test_deleting_a_memory_drops_its_links(store):
    store.sync_characters(fake_sources)
    store.delete(["m4"])
    assert ids(store.search(include=["Marek"])) == []
    assert all(c["name"] != "Marek" for c in store.summary()["characters"])


def test_a_full_relink_replaces_old_links(store):
    store.sync_characters(fake_sources)

    def relinked():
        index, _, names = fake_sources()
        return index, {"chat-1": ["Nell"], "chat-2": []}, names

    assert store.sync_characters(relinked, full=True) == 4
    assert ids(store.search(include=["Nell"])) == ["m2", "m1"]
    assert ids(store.search(include=["Sam"])) == []


def test_a_date_range_is_deleted_whole_and_narrowed_by_other_filters(store):
    store.sync_characters(fake_sources)
    # 1 September holds m1 (Sam, you) and m2 (Alex, AI); only the AI's goes.
    result = store.delete_matching(1, since="2026-09-01", until="2026-09-01", role="assistant")
    assert result["deleted"] == 1
    assert ids(store.search()) == ["m4", "m3", "m1"]
    # Everything up to the 2nd, with no other filter.
    assert store.delete_matching(2, until="2026-09-02")["deleted"] == 2
    assert ids(store.search()) == ["m4"]


def test_a_range_delete_needs_a_date_and_the_confirmed_count(store):
    with pytest.raises(ValueError, match="date range"):
        store.delete_matching(4)
    with pytest.raises(ValueError, match="2 memories match now, not the 1"):
        store.delete_matching(1, since="2026-09-01", until="2026-09-01")
    # Nothing was deleted by either refusal.
    assert store.search()["total"] == 4


def test_large_ranges_delete_in_chunks(tmp_path):
    path = tmp_path / "conversations.db"
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    connection.execute("INSERT INTO sessions VALUES ('s1', 'x')")
    connection.execute("INSERT INTO conversations VALUES ('c1', 's1', 'x')")
    connection.executemany(
        "INSERT INTO messages VALUES (?, 'c1', '2026-09-01T10:00:00+00:00', 'user', 'x', NULL, NULL)",
        [(f"m{i}",) for i in range(1300)],
    )
    connection.commit()
    connection.close()
    big = MemoryStore(str(path))
    result = big.delete_matching(1300, since="2026-09-01")
    assert (result["deleted"], result["conversations_removed"]) == (1300, 1)
