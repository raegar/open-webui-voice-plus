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
from sqlalchemy import BigInteger, Boolean, Column, Integer, String, Text, inspect, text


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
    # Legacy: an outfit used to name the one character it belonged to. Outfits are
    # now chosen per character in each chat (video_chat_character.outfit_id), so
    # several characters can share one; this is read only by the migration.
    applies_to_id = Column(String, nullable=False, default="")
    # Hidden everywhere while the user's work mode (hidePrivate setting) is on.
    private = Column(Boolean, nullable=False, default=False)
    # Kept out of the library and the chat pickers, but still works in any chat it
    # is already attached to; unlike delete, nothing is detached.
    archived = Column(Boolean, nullable=False, default=False)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)


class VideoChatCharacter(Base):
    __tablename__ = "video_chat_character"

    id = Column(String, primary_key=True)
    user_id = Column(String, index=True, nullable=False)
    chat_id = Column(String, index=True, nullable=False)
    character_id = Column(String, index=True, nullable=False)
    position = Column(Integer, nullable=False, default=0)
    # What this character is currently wearing / their state of dress in this chat.
    # Mutable per chat, unlike the library description, which is their default look.
    state = Column(Text, nullable=False, default="")
    # The library outfit this character wears in this chat, or "" for their own
    # clothes. Sits between the description (default look) and state (what has
    # changed during the scene), so either can be edited without losing the other.
    outfit_id = Column(String, nullable=False, default="")
    created_at = Column(BigInteger, nullable=False)


class VideoOutfitModel(BaseModel):
    """The parts of an outfit a chat needs: text for the model, images for video."""

    id: str
    name: str
    description: str
    image_file_ids: list[str]


class VideoCharacterModel(BaseModel):
    id: str
    user_id: str
    name: str
    description: str
    image_file_ids: list[str]
    voice_file_id: str
    kind: str
    applies_to_id: str
    private: bool = False
    archived: bool = False
    # Only populated by get_for_chat; a library listing has no per-chat state.
    state: str = ""
    outfit: Optional[VideoOutfitModel] = None
    created_at: int
    updated_at: int


class VideoCharacterForm(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    image_file_ids: list[str] = Field(default_factory=list)
    voice_file_id: str = ""
    kind: Literal["character", "location", "outfit"] = "character"
    applies_to_id: str = ""
    private: bool = False
    archived: bool = False


class VideoCharacterUpdateForm(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=4000)
    image_file_ids: Optional[list[str]] = None
    voice_file_id: Optional[str] = None
    kind: Optional[Literal["character", "location", "outfit"]] = None
    applies_to_id: Optional[str] = None
    private: Optional[bool] = None
    archived: Optional[bool] = None


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
        private=bool(row.private),
        archived=bool(row.archived),
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
                private=form.private,
                archived=form.archived,
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
            if form.private is not None:
                row.private = form.private
            if form.archived is not None:
                row.archived = form.archived
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
            # Characters wearing a deleted outfit go back to their own clothes.
            db.query(VideoChatCharacter).filter_by(
                user_id=user_id, outfit_id=id
            ).update({"outfit_id": ""})
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
            wanted = {link.character_id for link in links} | {
                link.outfit_id for link in links if link.outfit_id
            }
            rows = {
                row.id: row
                for row in db.query(VideoCharacter)
                .filter(
                    VideoCharacter.user_id == user_id,
                    VideoCharacter.id.in_(wanted),
                )
                .all()
            }
            models = []
            for link in links:
                row = rows.get(link.character_id)
                if row is None:
                    continue
                model = _to_model(row)
                model.state = link.state or ""
                outfit = rows.get(link.outfit_id) if link.outfit_id else None
                # An entry whose kind was changed away from outfit is no longer worn.
                if outfit is not None and outfit.kind == "outfit":
                    worn = _to_model(outfit)
                    model.outfit = VideoOutfitModel(
                        id=worn.id,
                        name=worn.name,
                        description=worn.description,
                        image_file_ids=worn.image_file_ids,
                    )
                models.append(model)
            return models

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

    def set_state(
        self, user_id: str, chat_id: str, character_id: str, state: str
    ) -> bool:
        """Record what a character is currently wearing in this chat."""
        with get_db() as db:
            link = (
                db.query(VideoChatCharacter)
                .filter_by(user_id=user_id, chat_id=chat_id, character_id=character_id)
                .first()
            )
            if not link:
                return False
            link.state = state.strip()
            db.commit()
            return True

    def set_outfit(
        self, user_id: str, chat_id: str, character_id: str, outfit_id: str
    ) -> bool:
        """Dress an attached character in a library outfit for this chat, or "" to clear."""
        with get_db() as db:
            link = (
                db.query(VideoChatCharacter)
                .filter_by(user_id=user_id, chat_id=chat_id, character_id=character_id)
                .first()
            )
            if not link:
                return False
            link.outfit_id = outfit_id
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
            "private": "BOOLEAN NOT NULL DEFAULT FALSE",
            "archived": "BOOLEAN NOT NULL DEFAULT FALSE",
        }
        missing = {n: d for n, d in wanted.items() if n not in existing}
        link_existing = {
            c["name"] for c in inspect(engine).get_columns("video_chat_character")
        }
        link_wanted = {
            "state": "TEXT NOT NULL DEFAULT ''",
            "outfit_id": "VARCHAR NOT NULL DEFAULT ''",
        }
        link_missing = {n: d for n, d in link_wanted.items() if n not in link_existing}
        if not missing and not link_missing:
            return
        with engine.begin() as connection:
            for name, definition in missing.items():
                connection.execute(
                    text(f"ALTER TABLE video_character ADD COLUMN {name} {definition}")
                )
            for name, definition in link_missing.items():
                connection.execute(
                    text(f"ALTER TABLE video_chat_character ADD COLUMN {name} {definition}")
                )
        log.info(
            "Added columns: video_character(%s) video_chat_character(%s)",
            ", ".join(missing) or "-",
            ", ".join(link_missing) or "-",
        )
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


def _migrate_outfit_attachments() -> None:
    """Turn outfits attached to a chat directly into outfits worn by a character.

    An outfit used to be attached to a chat like a person and name its wearer through
    applies_to_id. Now the wearer's own attachment carries outfit_id. Where the named
    wearer is attached to the same chat, they are dressed in the outfit; either way the
    outfit's own attachment is removed, since outfits are no longer attached alone.
    Idempotent: once converted, no chat has an outfit attached.
    """
    try:
        with get_db() as db:
            outfits = {
                row.id: row
                for row in db.query(VideoCharacter).filter_by(kind="outfit").all()
            }
            if not outfits:
                return
            outfit_links = (
                db.query(VideoChatCharacter)
                .filter(VideoChatCharacter.character_id.in_(list(outfits)))
                .all()
            )
            if not outfit_links:
                return
            dressed = 0
            for outfit_link in outfit_links:
                wearer_id = outfits[outfit_link.character_id].applies_to_id
                wearer = (
                    db.query(VideoChatCharacter)
                    .filter_by(
                        user_id=outfit_link.user_id,
                        chat_id=outfit_link.chat_id,
                        character_id=wearer_id,
                    )
                    .first()
                    if wearer_id
                    else None
                )
                if wearer is not None and not wearer.outfit_id:
                    wearer.outfit_id = outfit_link.character_id
                    dressed += 1
                db.delete(outfit_link)
            db.commit()
            log.info(
                "Converted %d chat-attached outfits (%d now worn by a character)",
                len(outfit_links),
                dressed,
            )
    except Exception:
        log.exception("Outfit attachment migration failed")


_add_missing_columns()
_migrate_chat_scoped_characters()
_migrate_outfit_attachments()
