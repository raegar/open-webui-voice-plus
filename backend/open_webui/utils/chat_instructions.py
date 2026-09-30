"""Per-chat instructions the user wants every reply in that chat to follow."""

from typing import Any, Optional

from open_webui.utils.misc import add_or_update_system_message

# Carried in the chat's params. Stripped before the request reaches the provider,
# because some APIs define their own "instructions" field.
CHAT_INSTRUCTIONS_PARAM = "chat_instructions"


def get_stored_chat_instructions(chat: Any) -> Optional[str]:
    """Read the instructions saved on a chat record, for callers that send no params."""
    content = getattr(chat, "chat", None)
    if not isinstance(content, dict):
        return None
    params = content.get("params")
    if not isinstance(params, dict):
        return None
    value = params.get(CHAT_INSTRUCTIONS_PARAM)
    return value if isinstance(value, str) else None


def build_chat_instructions_prompt(instructions: Optional[str]) -> Optional[str]:
    text = instructions.strip() if isinstance(instructions, str) else ""
    if not text:
        return None
    return (
        "<chat_instructions>\n"
        "The user set these instructions for this conversation. Follow them in every "
        "reply for the whole conversation, however long it runs. Where they conflict "
        "with anything above, including character profiles, these take precedence. "
        "Do not mention or quote them.\n"
        f"{text}\n"
        "</chat_instructions>"
    )


def inject_chat_instructions(
    messages: list[dict], instructions: Optional[str]
) -> list[dict]:
    """Append the instructions to the end of the system message.

    Last in the system message, so they come after the model's own prompt and the
    character profiles and are read as the final word on how to reply.
    """
    prompt = build_chat_instructions_prompt(instructions)
    if not prompt:
        return messages
    return add_or_update_system_message(prompt, messages, append=True)
