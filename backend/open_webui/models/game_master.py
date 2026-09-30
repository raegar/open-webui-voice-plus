"""Storage for the per-chat Game Master: its settings and its journal.

The fork's own tables, like `private_chat`, because the upstream chat model ships
unmodified in the base image. Rows can outlive their chat; an id that matches no
chat is never read, so that is harmless.

Every GM pass writes one journal entry, keyed to the message it reacted to, and a
successful entry carries the GM's full state after that pass. The state that applies
to any point in the conversation is the one on the nearest ancestor message with an
entry, which is what keeps regenerated replies and switched branches from sharing or
double-applying the GM's plans. See docs/game-master-design.md.
"""

import json
import time
from typing import Any, Optional
from uuid import uuid4

from open_webui.internal.db import Base, engine, get_db
from pydantic import BaseModel
from sqlalchemy import BigInteger, Boolean, Column, Integer, String, Text

INTENSITIES = ("light", "firm", "ruthless")
DEFAULT_CONFIG = {
    "agenda": "",
    "intensity": "firm",
    "player_character_id": "",
}


class GMSession(Base):
    __tablename__ = "gm_session"

    chat_id = Column(String, primary_key=True)
    user_id = Column(String, index=True, nullable=False)
    enabled = Column(Boolean, nullable=False, default=False)
    # JSON object; see DEFAULT_CONFIG.
    config = Column(Text, nullable=False, default="{}")
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)


class GMJournal(Base):
    __tablename__ = "gm_journal"

    id = Column(String, primary_key=True)
    chat_id = Column(String, index=True, nullable=False)
    user_id = Column(String, index=True, nullable=False)
    # The message this pass reacted to; "" is the root, before any message.
    message_id = Column(String, index=True, nullable=False, default="")
    # setup | turn | consult
    kind = Column(String, nullable=False, default="turn")
    reasoning = Column(Text, nullable=False, default="")
    # A reasoning model's own thinking, when the provider returns it separately.
    model_reasoning = Column(Text, nullable=False, default="")
    observations = Column(Text, nullable=False, default="[]")
    patch = Column(Text, nullable=False, default="{}")
    # Full state after this pass, or "" when the pass failed.
    state = Column(Text, nullable=False, default="")
    note = Column(Text, nullable=False, default="")
    model = Column(String, nullable=False, default="")
    tokens = Column(Integer, nullable=False, default=0)
    duration_ms = Column(Integer, nullable=False, default=0)
    error = Column(Text, nullable=False, default="")
    created_at = Column(BigInteger, nullable=False)


class GMSessionModel(BaseModel):
    chat_id: str
    user_id: str
    enabled: bool
    config: dict


class GMJournalModel(BaseModel):
    id: str
    chat_id: str
    message_id: str
    kind: str
    reasoning: str
    model_reasoning: str
    observations: list
    patch: dict
    state: Optional[dict]
    note: str
    model: str
    tokens: int
    duration_ms: int
    error: str
    created_at: int


def _loads(value: str, fallback: Any) -> Any:
    try:
        parsed = json.loads(value) if value else fallback
    except (TypeError, ValueError):
        return fallback
    return parsed if isinstance(parsed, type(fallback)) else fallback


def normalize_config(config: Any) -> dict:
    merged = {**DEFAULT_CONFIG, **(config if isinstance(config, dict) else {})}
    if merged["intensity"] not in INTENSITIES:
        merged["intensity"] = DEFAULT_CONFIG["intensity"]
    for key in ("agenda", "player_character_id"):
        if not isinstance(merged[key], str):
            merged[key] = ""
    return {key: merged[key] for key in DEFAULT_CONFIG}


def _session_model(row: GMSession) -> GMSessionModel:
    return GMSessionModel(
        chat_id=row.chat_id,
        user_id=row.user_id,
        enabled=bool(row.enabled),
        config=normalize_config(_loads(row.config, {})),
    )


def _journal_model(row: GMJournal) -> GMJournalModel:
    return GMJournalModel(
        id=row.id,
        chat_id=row.chat_id,
        message_id=row.message_id or "",
        kind=row.kind,
        reasoning=row.reasoning or "",
        model_reasoning=row.model_reasoning or "",
        observations=_loads(row.observations, []),
        patch=_loads(row.patch, {}),
        state=_loads(row.state, {}) if row.state else None,
        note=row.note or "",
        model=row.model or "",
        tokens=row.tokens or 0,
        duration_ms=row.duration_ms or 0,
        error=row.error or "",
        created_at=row.created_at,
    )


class GameMasterTable:
    def get_session(self, user_id: str, chat_id: str) -> Optional[GMSessionModel]:
        with get_db() as db:
            row = db.query(GMSession).filter_by(chat_id=chat_id, user_id=user_id).first()
            return _session_model(row) if row else None

    def update_session(
        self,
        user_id: str,
        chat_id: str,
        enabled: Optional[bool] = None,
        config: Optional[dict] = None,
    ) -> GMSessionModel:
        now = int(time.time())
        with get_db() as db:
            row = db.query(GMSession).filter_by(chat_id=chat_id, user_id=user_id).first()
            if not row:
                row = GMSession(
                    chat_id=chat_id,
                    user_id=user_id,
                    enabled=False,
                    config=json.dumps(DEFAULT_CONFIG),
                    created_at=now,
                    updated_at=now,
                )
                db.add(row)
            if enabled is not None:
                row.enabled = enabled
            if config is not None:
                current = normalize_config(_loads(row.config, {}))
                row.config = json.dumps(normalize_config({**current, **config}))
            row.updated_at = now
            db.commit()
            db.refresh(row)
            return _session_model(row)

    def add_entry(self, user_id: str, chat_id: str, **fields) -> GMJournalModel:
        with get_db() as db:
            row = GMJournal(
                id=str(uuid4()),
                chat_id=chat_id,
                user_id=user_id,
                message_id=fields.get("message_id") or "",
                kind=fields.get("kind", "turn"),
                reasoning=fields.get("reasoning", ""),
                model_reasoning=fields.get("model_reasoning", ""),
                observations=json.dumps(fields.get("observations") or []),
                patch=json.dumps(fields.get("patch") or {}),
                state=json.dumps(fields["state"]) if fields.get("state") is not None else "",
                note=fields.get("note", ""),
                model=fields.get("model", ""),
                tokens=int(fields.get("tokens") or 0),
                duration_ms=int(fields.get("duration_ms") or 0),
                error=fields.get("error", ""),
                created_at=int(time.time() * 1000),
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return _journal_model(row)

    def get_entry_index(self, user_id: str, chat_id: str) -> list[dict]:
        """Light listing for ancestor lookup: no reasoning or state columns."""
        with get_db() as db:
            rows = (
                db.query(
                    GMJournal.id,
                    GMJournal.message_id,
                    GMJournal.created_at,
                    GMJournal.error,
                    GMJournal.state != "",
                )
                .filter_by(chat_id=chat_id, user_id=user_id)
                .order_by(GMJournal.created_at)
                .all()
            )
            return [
                {
                    "id": row[0],
                    "message_id": row[1] or "",
                    "created_at": row[2],
                    "error": row[3] or "",
                    "has_state": bool(row[4]),
                }
                for row in rows
            ]

    def get_entry(self, user_id: str, entry_id: str) -> Optional[GMJournalModel]:
        with get_db() as db:
            row = db.query(GMJournal).filter_by(id=entry_id, user_id=user_id).first()
            return _journal_model(row) if row else None

    def get_journal(
        self, user_id: str, chat_id: str, limit: int = 100
    ) -> list[GMJournalModel]:
        with get_db() as db:
            rows = (
                db.query(GMJournal)
                .filter_by(chat_id=chat_id, user_id=user_id)
                .order_by(GMJournal.created_at.desc())
                .limit(limit)
                .all()
            )
            return [_journal_model(row) for row in rows]


GameMaster = GameMasterTable()

GMSession.__table__.create(bind=engine, checkfirst=True)
GMJournal.__table__.create(bind=engine, checkfirst=True)
