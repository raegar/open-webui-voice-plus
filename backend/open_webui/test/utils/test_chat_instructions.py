from types import SimpleNamespace

from open_webui.utils.character_personality import inject_character_personality
from open_webui.utils.chat_instructions import (
    get_stored_chat_instructions,
    inject_chat_instructions,
)


def test_instructions_follow_the_character_profiles():
    messages = [
        {"role": "system", "content": "Answer accurately."},
        {"role": "user", "content": "Hello"},
    ]
    messages = inject_character_personality(
        messages, [{"name": "Brian", "description": "A dry, understated geek."}]
    )

    result = inject_chat_instructions(messages, "Always reply in iambic pentameter.")

    content = result[0]["content"]
    assert content.startswith("Answer accurately.\n")
    assert content.index("</attached_character_profiles>") < content.index(
        "<chat_instructions>"
    )
    assert "Always reply in iambic pentameter." in content
    assert content.endswith("</chat_instructions>")
    assert result[1] == {"role": "user", "content": "Hello"}


def test_instructions_create_a_system_message_when_there_is_none():
    result = inject_chat_instructions(
        [{"role": "user", "content": "Hello"}], "Keep replies short."
    )

    assert result[0]["role"] == "system"
    assert "Keep replies short." in result[0]["content"]
    assert result[1]["role"] == "user"


def test_blank_instructions_leave_the_request_alone():
    messages = [{"role": "user", "content": "Hello"}]

    assert inject_chat_instructions(list(messages), "   \n") == messages
    assert inject_chat_instructions(list(messages), None) == messages


def test_stored_instructions_are_read_from_the_chat_params():
    chat = SimpleNamespace(chat={"params": {"chat_instructions": "Speak as a pirate."}})

    assert get_stored_chat_instructions(chat) == "Speak as a pirate."
    assert get_stored_chat_instructions(SimpleNamespace(chat={})) is None
    assert get_stored_chat_instructions(None) is None
