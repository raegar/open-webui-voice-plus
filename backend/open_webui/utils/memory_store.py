"""Read and prune the persistent memory pipeline's recorded messages.

The pipeline (persistent-ai-memory, running in the `pipelines` container) records
chat messages into conversations.db and later retrieves them by embedding search to
inject as "long-term memories". Each recorded message is one memory: deleting its
row removes its text and its embedding together, so it can never be retrieved again.

The pipelines container writes this file while we read it. It is a rollback-journal
SQLite database shared over a bind mount; both containers run in Docker's VM, so
SQLite's file locks coordinate them. Every access here is one short transaction with
a busy timeout, and deletion takes the write lock up front.

Deleting is permanent, so the first deletion of each day copies the database aside
first (SQLite's backup API, safe while the pipeline is writing). The newest
BACKUPS_KEPT copies are kept.
"""

import os
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

BUSY_TIMEOUT_SECONDS = 15
BACKUPS_KEPT = 3
MAX_PAGE = 200
MAX_BULK_DELETE = 500
ORDERS = {"newest": "timestamp DESC", "oldest": "timestamp ASC"}


def default_db_path() -> str:
    return os.environ.get("MEMORY_BROWSER_DB", "/app/ai_memory/conversations.db")


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def build_filter(
    q: str = "",
    role: str = "",
    source: str = "",
    since: Optional[str] = None,
    until: Optional[str] = None,
    conversation_id: str = "",
) -> tuple[str, list]:
    """A WHERE clause and its parameters for the given filters.

    since and until are whole days (YYYY-MM-DD), both inclusive. Timestamps are
    stored as ISO strings in UTC, so day boundaries compare as strings.
    """
    clauses, params = [], []
    for word in q.split():
        clauses.append("content LIKE ? ESCAPE '\\'")
        params.append(f"%{_escape_like(word)}%")
    if role in ("user", "assistant"):
        clauses.append("role = ?")
        params.append(role)
    # No source_type means the filter recorded it from a chat; anything else came in
    # through an importer.
    if source == "recorded":
        clauses.append("(source_type IS NULL OR source_type = '')")
    elif source == "imported":
        clauses.append("(source_type IS NOT NULL AND source_type != '')")
    if since:
        clauses.append("timestamp >= ?")
        params.append(date.fromisoformat(since).isoformat())
    if until:
        clauses.append("timestamp < ?")
        params.append((date.fromisoformat(until) + timedelta(days=1)).isoformat())
    if conversation_id:
        clauses.append("conversation_id = ?")
        params.append(conversation_id)
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


class MemoryStore:
    def __init__(self, path: Optional[str] = None):
        self.path = path or default_db_path()

    def _connect(self) -> sqlite3.Connection:
        if not Path(self.path).is_file():
            raise FileNotFoundError(f"No memory database at {self.path}")
        connection = sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_SECONDS)
        connection.row_factory = sqlite3.Row
        return connection

    def summary(self) -> dict:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT COUNT(*) AS total, MIN(timestamp) AS first, MAX(timestamp) AS last, "
                "SUM(CASE WHEN source_type IS NULL OR source_type = '' THEN 1 ELSE 0 END) "
                "AS recorded FROM messages"
            ).fetchone()
            return {
                "total": row["total"] or 0,
                "recorded": row["recorded"] or 0,
                "imported": (row["total"] or 0) - (row["recorded"] or 0),
                "first": row["first"],
                "last": row["last"],
            }
        finally:
            connection.close()

    def search(
        self, *, order: str = "newest", offset: int = 0, limit: int = 50, **filters
    ) -> dict:
        where, params = build_filter(**filters)
        limit = max(1, min(limit, MAX_PAGE))
        connection = self._connect()
        try:
            total = connection.execute(f"SELECT COUNT(*) FROM messages{where}", params).fetchone()[0]
            rows = connection.execute(
                "SELECT message_id, conversation_id, timestamp, role, content, source_type "
                f"FROM messages{where} ORDER BY {ORDERS.get(order, ORDERS['newest'])} "
                "LIMIT ? OFFSET ?",
                [*params, limit, max(0, offset)],
            ).fetchall()
        finally:
            connection.close()
        return {
            "total": total,
            "items": [
                {
                    "message_id": row["message_id"],
                    "conversation_id": row["conversation_id"],
                    "timestamp": row["timestamp"],
                    "role": row["role"],
                    "content": row["content"],
                    "source": "imported" if row["source_type"] else "recorded",
                }
                for row in rows
            ],
        }

    def delete(self, message_ids: list[str]) -> dict:
        """Delete messages, then any conversation or session they leave empty."""
        ids = list(dict.fromkeys(i for i in message_ids if isinstance(i, str) and i))
        if not ids:
            return {"deleted": 0, "conversations_removed": 0, "backup": None}
        if len(ids) > MAX_BULK_DELETE:
            raise ValueError(f"At most {MAX_BULK_DELETE} memories can be deleted at once")
        backup = self.backup_if_due()
        connection = self._connect()
        try:
            # Take the write lock now rather than part way through.
            connection.execute("BEGIN IMMEDIATE")
            marks = ",".join("?" * len(ids))
            conversations = [
                row[0]
                for row in connection.execute(
                    f"SELECT DISTINCT conversation_id FROM messages WHERE message_id IN ({marks})",
                    ids,
                )
            ]
            deleted = connection.execute(
                f"DELETE FROM messages WHERE message_id IN ({marks})", ids
            ).rowcount
            removed = 0
            for conversation_id in conversations:
                left = connection.execute(
                    "SELECT 1 FROM messages WHERE conversation_id = ? LIMIT 1", (conversation_id,)
                ).fetchone()
                if left:
                    continue
                session = connection.execute(
                    "SELECT session_id FROM conversations WHERE conversation_id = ?",
                    (conversation_id,),
                ).fetchone()
                removed += connection.execute(
                    "DELETE FROM conversations WHERE conversation_id = ?", (conversation_id,)
                ).rowcount
                if session and not connection.execute(
                    "SELECT 1 FROM conversations WHERE session_id = ? LIMIT 1", (session[0],)
                ).fetchone():
                    connection.execute("DELETE FROM sessions WHERE session_id = ?", (session[0],))
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return {"deleted": deleted, "conversations_removed": removed, "backup": backup}

    def backup_dir(self) -> Path:
        return Path(self.path).parent / "backups"

    def backup_if_due(self, today: Optional[date] = None) -> Optional[str]:
        """Copy the database aside once per day before deleting. Returns the new
        backup's file name, or None if today's copy already exists."""
        today = today or datetime.now().date()
        folder = self.backup_dir()
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"conversations-{today.strftime('%Y%m%d')}.db"
        if target.exists():
            return None
        source = self._connect()
        partial = target.with_suffix(".db.part")
        try:
            destination = sqlite3.connect(partial)
            try:
                source.backup(destination)
            finally:
                destination.close()
        finally:
            source.close()
        partial.replace(target)
        backups = sorted(folder.glob("conversations-*.db"))
        for old in backups[:-BACKUPS_KEPT]:
            old.unlink(missing_ok=True)
        return target.name
