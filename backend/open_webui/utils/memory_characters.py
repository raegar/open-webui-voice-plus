"""Which character each recorded memory belongs to.

The memory pipeline stores only a message's text and role: nothing says which chat
or character it came from. This derives it, in two ways:

- chat: the memory's text matches a message in an Open WebUI chat (the pipeline
  records messages verbatim). The chat's characters are its attached character
  profiles, or, when it has none, the workspace model it was talking to, which is
  how a persona model is set up. Exact.
- named: imported memories came from a chat log with no chat behind it. Their
  conversation is attributed to the library characters it names often enough.
  Inferred, and labelled as such. Library names with a parenthetical ("X (A.I.)")
  cannot be told apart from their base name in plain text, so they are skipped.

The links live in a sidecar SQLite file next to the memory store, never in the
pipeline's own database, and the store attaches it to filter by character.
"""

import hashlib
import re
import sqlite3
import time
from pathlib import Path
from typing import Iterable, Optional

# Mentions an imported conversation needs before it counts as being with someone.
NAMED_MIN_MENTIONS = 3
SIDECAR_NAME = "memory_characters.db"

SIDECAR_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_character (
    message_id TEXT NOT NULL,
    character TEXT NOT NULL,
    how TEXT NOT NULL,
    PRIMARY KEY (message_id, character)
);
CREATE INDEX IF NOT EXISTS memory_character_by_name ON memory_character (character);
CREATE TABLE IF NOT EXISTS memory_indexed (
    message_id TEXT PRIMARY KEY,
    chat_id TEXT NOT NULL DEFAULT '',
    how TEXT NOT NULL,
    indexed_at INTEGER NOT NULL
);
"""


def normalize(content) -> str:
    """Message text as both sides store it: text parts only, details blocks out,
    whitespace collapsed."""
    if isinstance(content, list):
        content = " ".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    if not isinstance(content, str):
        return ""
    content = re.sub(r"<details\b[^>]*>.*?</details>", "", content, flags=re.S | re.I)
    return " ".join(content.split())


def content_key(content) -> Optional[str]:
    text = normalize(content)
    return hashlib.sha1(text.encode("utf-8")).hexdigest() if len(text) >= 2 else None


def inferable_names(names: Iterable[str]) -> list[str]:
    """Library names that can be recognised in plain text."""
    return sorted({n.strip() for n in names if n and n.strip() and "(" not in n})


def named_in(text: str, names: list[str], minimum: int = NAMED_MIN_MENTIONS) -> list[str]:
    """Names mentioned at least `minimum` times, as whole words, ignoring case."""
    found = []
    for name in names:
        pattern = r"(?<![\w])" + re.escape(name) + r"(?![\w])"
        if len(re.findall(pattern, text, flags=re.I)) >= minimum:
            found.append(name)
    return found


def attribute(
    memories: Iterable[tuple],
    chat_index: dict,
    chat_characters: dict,
    library_names: Iterable[str],
) -> dict:
    """Characters for each memory.

    memories: (message_id, conversation_id, content, source_type) rows.
    chat_index: content_key -> chat_id, over Open WebUI chat messages.
    chat_characters: chat_id -> list of character names for that chat.
    Returns message_id -> (characters, how, chat_id), how being chat, named or none.
    """
    names = inferable_names(library_names)
    result = {}
    imported: dict[str, list[tuple]] = {}
    for message_id, conversation_id, content, source_type in memories:
        if source_type:
            imported.setdefault(conversation_id, []).append((message_id, content))
            continue
        chat_id = chat_index.get(content_key(content) or "")
        characters = chat_characters.get(chat_id, []) if chat_id else []
        result[message_id] = (characters, "chat" if chat_id else "none", chat_id or "")
    for conversation_id, rows in imported.items():
        text = "\n".join(content for _, content in rows if isinstance(content, str))
        characters = named_in(text, names)
        for message_id, _ in rows:
            result[message_id] = (characters, "named" if characters else "none", "")
    return result


class MemoryCharacterIndex:
    """The sidecar of memory -> character links, kept beside the memory store."""

    def __init__(self, store_path: str):
        self.path = str(Path(store_path).parent / SIDECAR_NAME)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.executescript(SIDECAR_SCHEMA)
        return connection

    def indexed_ids(self) -> set:
        connection = self.connect()
        try:
            return {row[0] for row in connection.execute("SELECT message_id FROM memory_indexed")}
        finally:
            connection.close()

    def save(self, attributions: dict, replace_all: bool = False) -> None:
        now = int(time.time())
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if replace_all:
                connection.execute("DELETE FROM memory_character")
                connection.execute("DELETE FROM memory_indexed")
            for message_id, (characters, how, chat_id) in attributions.items():
                connection.execute("DELETE FROM memory_character WHERE message_id = ?", (message_id,))
                connection.executemany(
                    "INSERT OR IGNORE INTO memory_character VALUES (?, ?, ?)",
                    [(message_id, name, how) for name in characters],
                )
                connection.execute(
                    "INSERT OR REPLACE INTO memory_indexed VALUES (?, ?, ?, ?)",
                    (message_id, chat_id, how, now),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def forget(self, message_ids: list[str]) -> None:
        """Drop links for deleted memories."""
        if not message_ids:
            return
        connection = self.connect()
        try:
            marks = ",".join("?" * len(message_ids))
            connection.execute(f"DELETE FROM memory_character WHERE message_id IN ({marks})", message_ids)
            connection.execute(f"DELETE FROM memory_indexed WHERE message_id IN ({marks})", message_ids)
            connection.commit()
        finally:
            connection.close()


def load_owui_sources() -> tuple[dict, dict, list[str]]:
    """Open WebUI's side: a content index over every chat message, each chat's
    characters, and the character library's names. Reads the app database."""
    import json

    from sqlalchemy import text

    from open_webui.internal.db import get_db

    with get_db() as db:
        library = {
            row[0]: row[1]
            for row in db.execute(
                text("SELECT id, name FROM video_character WHERE kind = 'character'")
            )
        }
        attached: dict[str, list[str]] = {}
        for chat_id, character_id in db.execute(
            text("SELECT chat_id, character_id FROM video_chat_character ORDER BY position")
        ):
            name = library.get(character_id)
            if name and name not in attached.setdefault(chat_id, []):
                attached[chat_id].append(name)
        # A workspace model is a persona: the character a chat without profiles talks to.
        personas = {
            row[0]: row[1]
            for row in db.execute(
                text("SELECT id, name FROM model WHERE base_model_id IS NOT NULL AND base_model_id != ''")
            )
        }
        chat_index: dict[str, str] = {}
        chat_characters: dict[str, list[str]] = {}
        for chat_id, data in db.execute(text("SELECT id, chat FROM chat")):
            try:
                chat = json.loads(data) if isinstance(data, str) else (data or {})
            except ValueError:
                continue
            messages = ((chat.get("history") or {}).get("messages") or {}).values()
            models = set()
            for message in messages:
                key = content_key(message.get("content"))
                if key:
                    chat_index.setdefault(key, chat_id)
                if message.get("model"):
                    models.add(message["model"])
            for model_id in chat.get("models") or []:
                models.add(model_id)
            characters = list(attached.get(chat_id, []))
            if not characters:
                characters = sorted({personas[m] for m in models if m in personas})
            chat_characters[chat_id] = characters
    return chat_index, chat_characters, list(library.values())
