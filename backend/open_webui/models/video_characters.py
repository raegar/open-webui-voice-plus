"""Reusable character library for MiniMax H3 Ref2VA video generation.

Characters belong to a user, not a chat, and are attached to chats through
`video_chat_character`. Reference images are stored as ordinary Open WebUI files
and referenced by id: holding base64 here (or in the chat JSON) would mean
loading several megabytes every time a roster is read, for data that is only
needed at generation time.

The table originally scoped characters to a single chat via `video_character.chat_id`.
That column is retained but no longer used for scoping, because dropping a column
is awkward on older SQLite; `_migrate_chat_scoped_characters` converts any legacy
row into a library character plus an attachment.
"""

import json
import logging
import time
from typing import Optional
from uuid import uuid4

from open_webui.internal.db import Base, engine, get_db
from pydantic import BaseModel, Field
from sqlalchemy import BigInteger, Column, Integer, String, Text


log = logging.getLogger(__name__)

# Ref2VA addresses images as <Picture 1>..<Picture N> and accepts at most 9.
MAX_REFERENCE_IMAGES = 9
MAX_IMAGES_PER_CHARACTER = 3


class VideoCharacter(Base):
    __tablename__ = "video_character"

    id = Column(String, primary_key=True)
    user_id = Column(String, index=True, nullable=False)
    # Legacy: empty for library characters. See module docstring.
    chat_id = Column(String, index=True, nullable=False, default="")
    name = Column(Text, nullable=False)
    description = Column(Text, nullable=False, default="")
    # JSON array of file ids; order defines this character's picture order.
    image_file_ids = Column(Text, nullable=False, default="[]")
    position = Column(Integer, nullable=False, default=0)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)


class VideoChatCharacter(Base):
    __tablename__ = "video_chat_character"

    id = Column(String, primary_key=True)
    user_id = Column(String, index=True, nullable=False)
    chat_id = Column(String, index=True, nullable=False)
    character_id = Column(String, index=True, nullable=False)
    position = Column(Integer, nullable=False, default=0)
    created_at = Column(BigInteger, nullable=False)


class VideoCharacterModel(BaseModel):
    id: str
    user_id: str
    name: str
    description: str
    image_file_ids: list[str]
    created_at: int
    updated_at: int


class VideoCharacterForm(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    image_file_ids: list[str] = Field(default_factory=list)


class VideoCharacterUpdateForm(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=4000)
    image_file_ids: Optional[list[str]] = None


def _to_model(row: VideoCharacter) -> VideoCharacterModel:
    try:
        file_ids = json.loads(row.image_file_ids or "[]")
    except (TypeError, ValueError):
        file_ids = []
    return VideoCharacterModel(
        id=row.id,
        user_id=row.user_id,
        name=row.name,
        description=row.description or "",
        image_file_ids=[i for i in file_ids if isinstance(i, str)],
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class VideoCharactersTable:
    def get_library(self, user_id: str) -> list[VideoCharacterModel]:
        with get_db() as db:
            rows = (
                db.query(VideoCharacter)
                .filter_by(user_id=user_id)
                .order_by(VideoCharacter.name)
                .all()
            )
            return [_to_model(row) for row in rows]

    def get_by_id(self, user_id: str, id: str) -> Optional[VideoCharacterModel]:
        with get_db() as db:
            row = db.query(VideoCharacter).filter_by(id=id, user_id=user_id).first()
            return _to_model(row) if row else None

    def insert(self, user_id: str, form: VideoCharacterForm) -> VideoCharacterModel:
        now = int(time.time())
        with get_db() as db:
            row = VideoCharacter(
                id=str(uuid4()),
                user_id=user_id,
                chat_id="",
                name=form.name.strip(),
                description=form.description.strip(),
                image_file_ids=json.dumps(form.image_file_ids[:MAX_IMAGES_PER_CHARACTER]),
                position=0,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return _to_model(row)

    def update(
        self, user_id: str, id: str, form: VideoCharacterUpdateForm
    ) -> Optional[VideoCharacterModel]:
        with get_db() as db:
            row = db.query(VideoCharacter).filter_by(id=id, user_id=user_id).first()
            if not row:
                return None
            if form.name is not None:
                row.name = form.name.strip()
            if form.description is not None:
                row.description = form.description.strip()
            if form.image_file_ids is not None:
                row.image_file_ids = json.dumps(
                    form.image_file_ids[:MAX_IMAGES_PER_CHARACTER]
                )
            row.updated_at = int(time.time())
            db.commit()
            db.refresh(row)
            return _to_model(row)

    def delete(self, user_id: str, id: str) -> bool:
        with get_db() as db:
            # Drop attachments too, or chats keep pointing at a character that is gone.
            db.query(VideoChatCharacter).filter_by(
                user_id=user_id, character_id=id
            ).delete()
            deleted = db.query(VideoCharacter).filter_by(id=id, user_id=user_id).delete()
            db.commit()
            return bool(deleted)

    def get_for_chat(self, user_id: str, chat_id: str) -> list[VideoCharacterModel]:
        with get_db() as db:
            links = (
                db.query(VideoChatCharacter)
                .filter_by(user_id=user_id, chat_id=chat_id)
                .order_by(VideoChatCharacter.position, VideoChatCharacter.created_at)
                .all()
            )
            if not links:
                return []
            rows = {
                row.id: row
                for row in db.query(VideoCharacter)
                .filter(
                    VideoCharacter.user_id == user_id,
                    VideoCharacter.id.in_([link.character_id for link in links]),
                )
                .all()
            }
            return [
                _to_model(rows[link.character_id])
                for link in links
                if link.character_id in rows
            ]

    def attach(self, user_id: str, chat_id: str, character_id: str) -> bool:
        with get_db() as db:
            character = (
                db.query(VideoCharacter)
                .filter_by(id=character_id, user_id=user_id)
                .first()
            )
            if not character:
                return False
            existing = (
                db.query(VideoChatCharacter)
                .filter_by(user_id=user_id, chat_id=chat_id, character_id=character_id)
                .first()
            )
            if existing:
                return True
            position = (
                db.query(VideoChatCharacter)
                .filter_by(user_id=user_id, chat_id=chat_id)
                .count()
            )
            db.add(
                VideoChatCharacter(
                    id=str(uuid4()),
                    user_id=user_id,
                    chat_id=chat_id,
                    character_id=character_id,
                    position=position,
                    created_at=int(time.time()),
                )
            )
            db.commit()
            return True

    def detach(self, user_id: str, chat_id: str, character_id: str) -> bool:
        with get_db() as db:
            deleted = (
                db.query(VideoChatCharacter)
                .filter_by(user_id=user_id, chat_id=chat_id, character_id=character_id)
                .delete()
            )
            db.commit()
            return bool(deleted)


VideoCharacters = VideoCharactersTable()

VideoCharacter.__table__.create(bind=engine, checkfirst=True)
VideoChatCharacter.__table__.create(bind=engine, checkfirst=True)


def _migrate_chat_scoped_characters() -> None:
    """Convert pre-library rows into library characters plus attachments.

    Idempotent: a converted row has an empty chat_id and is skipped thereafter.
    """
    try:
        with get_db() as db:
            legacy = (
                db.query(VideoCharacter)
                .filter(VideoCharacter.chat_id != "")
                .filter(VideoCharacter.chat_id.isnot(None))
                .all()
            )
            if not legacy:
                return
            now = int(time.time())
            for row in legacy:
                already = (
                    db.query(VideoChatCharacter)
                    .filter_by(
                        user_id=row.user_id, chat_id=row.chat_id, character_id=row.id
                    )
                    .first()
                )
                if not already:
                    db.add(
                        VideoChatCharacter(
                            id=str(uuid4()),
                            user_id=row.user_id,
                            chat_id=row.chat_id,
                            character_id=row.id,
                            position=row.position or 0,
                            created_at=now,
                        )
                    )
                row.chat_id = ""
            db.commit()
            log.info("Migrated %d chat-scoped video characters to the library", len(legacy))
    except Exception:
        # A failed migration must not stop the app from booting.
        log.exception("Video character migration failed")


_migrate_chat_scoped_characters()
