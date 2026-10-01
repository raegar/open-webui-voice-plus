from open_webui.utils.memory_characters import (
    attribute,
    content_key,
    inferable_names,
    named_in,
    normalize,
)


def test_text_is_compared_as_both_sides_store_it():
    assert normalize("  Sam   hides\nthe key ") == "Sam hides the key"
    assert normalize([{"type": "text", "text": "Sam"}, {"type": "image_url"}]) == "Sam"
    assert normalize('<details type="reasoning">hm</details>Sam') == "Sam"
    assert content_key("Sam  hides") == content_key("Sam hides")
    assert content_key("") is None


def test_parenthetical_names_are_not_inferred():
    assert inferable_names(["Sam", "Alex (A.I.)", " Marek ", "", "Sam"]) == ["Marek", "Sam"]


def test_named_needs_enough_whole_word_mentions():
    text = "Sam waits. sam turns. SAM leaves. Samantha arrives. Alex once."
    assert named_in(text, ["Sam", "Alex", "Samantha"], minimum=3) == ["Sam"]
    assert named_in("Samuel and Samuel and Samuel", ["Sam"]) == []


def test_recorded_memories_take_their_chats_characters():
    memories = [
        ("m1", "c1", "Sam hides the key", None),
        ("m2", "c1", "No such message anywhere", None),
        ("m3", "c9", "Sam waits. Sam turns. Sam leaves.", "jsonl_import"),
        ("m4", "c9", "Alex shrugs.", "jsonl_import"),
        ("m5", "c8", "Nobody is named here.", "jsonl_import"),
    ]
    chat_index = {content_key("Sam hides the key"): "chat-1"}
    chat_characters = {"chat-1": ["Sam", "Alex"]}
    result = attribute(memories, chat_index, chat_characters, ["Sam", "Alex", "Marek (A.I.)"])

    assert result["m1"] == (["Sam", "Alex"], "chat", "chat-1")
    assert result["m2"] == ([], "none", "")
    # A whole imported conversation shares what it names; Alex appears only once.
    assert result["m3"] == (["Sam"], "named", "")
    assert result["m4"] == (["Sam"], "named", "")
    assert result["m5"] == ([], "none", "")
