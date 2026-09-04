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
from typing import Literal, Optional
from uuid import uuid4

from open_webui.internal.db import Base, engine, get_db
from pydantic import BaseModel, Field
from sqlalchemy import BigInteger, Column, Integer, String, Text, inspect, text


log = logging.getLogger(__name__)

# Ref2VA addresses images as <Picture 1>..<Picture N> and accepts at most 9.
MAX_REFERENCE_IMAGES = 9
MAX_IMAGES_PER_CHARACTER = 3
# The node accepts at most three standalone ref_audios, a far tighter budget than
# images, so a chat can carry only three voiced characters.
MAX_REFERENCE_AUDIOS = 3

# A library entry is either a person or a scene reference. Ref2VA's
# subject_definitions already covers "identities, scenes, or styles", so these all
# become <Subject N> citations; the kind only decides how we describe them and
# which retention_analysis marker we suggest.
REFERENCE_KINDS = ("character", "location", "outfit")


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
    # Single file id of a voice reference, or "" for none.
    voice_file_id = Column(String, nullable=False, default="")
    # character | location | outfit. See REFERENCE_KINDS.
    kind = Column(String, nullable=False, default="character")
    # For an outfit: the character it belongs to, or "" when unassigned.
    applies_to_id = Column(String, nullable=False, default="")
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
    voice_file_id: str
    kind: str
    applies_to_id: str
    created_at: int
    updated_at: int


class VideoCharacterForm(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    image_file_ids: list[str] = Field(default_factory=list)
    voice_file_id: str = ""
    kind: Literal["character", "location", "outfit"] = "character"
    applies_to_id: str = ""


class VideoCharacterUpdateForm(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=4000)
    image_file_ids: Optional[list[str]] = None
    voice_file_id: Optional[str] = None
    kind: Optional[Literal["character", "location", "outfit"]] = None
    applies_to_id: Optional[str] = None


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
        voice_file_id=row.voice_file_id or "",
        kind=row.kind if row.kind in REFERENCE_KINDS else "character",
        applies_to_id=row.applies_to_id or "",
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
                voice_file_id=form.voice_file_id or "",
                kind=form.kind,
                applies_to_id=form.applies_to_id or "",
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
            if form.voice_file_id is not None:
                row.voice_file_id = form.voice_file_id
            if form.kind is not None:
                row.kind = form.kind
            if form.applies_to_id is not None:
                row.applies_to_id = form.applies_to_id
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
            # Outfits that pointed at this character become unassigned rather than
            # dangling, so they still render as a generic wardrobe reference.
            db.query(VideoCharacter).filter_by(
                user_id=user_id, applies_to_id=id
            ).update({"applies_to_id": ""})
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


def _add_missing_columns() -> None:
    """Add columns introduced after the table first shipped.

    `__table__.create(checkfirst=True)` is a no-op on an existing table, so a new
    column has to be added explicitly or every query against it fails.
    """
    try:
        existing = {c["name"] for c in inspect(engine).get_columns("video_character")}
        wanted = {
            "voice_file_id": "VARCHAR NOT NULL DEFAULT ''",
            "kind": "VARCHAR NOT NULL DEFAULT 'character'",
            "applies_to_id": "VARCHAR NOT NULL DEFAULT ''",
        }
        missing = {n: d for n, d in wanted.items() if n not in existing}
        if not missing:
            return
        with engine.begin() as connection:
            for name, definition in missing.items():
                connection.execute(
                    text(f"ALTER TABLE video_character ADD COLUMN {name} {definition}")
                )
        log.info("Added video_character columns: %s", ", ".join(missing))
    except Exception:
        log.exception("Could not add missing video_character columns")


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


_add_missing_columns()
_migrate_chat_scoped_characters()
