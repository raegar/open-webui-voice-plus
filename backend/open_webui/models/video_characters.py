"""Per-chat character roster for MiniMax H3 Ref2VA video generation.

Reference images are stored as ordinary Open WebUI files and referenced here by
id. Holding base64 in this table (or in the chat JSON) would mean loading several
megabytes every time a chat or roster is read, for data that is only needed at
generation time.
"""

import json
import time
from typing import Optional
from uuid import uuid4

from open_webui.internal.db import Base, engine, get_db
from pydantic import BaseModel, Field
from sqlalchemy import BigInteger, Column, Integer, String, Text


# Ref2VA addresses images as <Picture 1>..<Picture N> and accepts at most 9.
MAX_REFERENCE_IMAGES = 9
MAX_IMAGES_PER_CHARACTER = 3


class VideoCharacter(Base):
    __tablename__ = "video_character"

    id = Column(String, primary_key=True)
    user_id = Column(String, index=True, nullable=False)
    chat_id = Column(String, index=True, nullable=False)
    name = Column(Text, nullable=False)
    description = Column(Text, nullable=False, default="")
    # JSON array of file ids; order defines the <Picture N> numbering.
    image_file_ids = Column(Text, nullable=False, default="[]")
    position = Column(Integer, nullable=False, default=0)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)


class VideoCharacterModel(BaseModel):
    id: str
    user_id: str
    chat_id: str
    name: str
    description: str
    image_file_ids: list[str]
    position: int
    created_at: int
    updated_at: int


class VideoCharacterForm(BaseModel):
    chat_id: str
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    image_file_ids: list[str] = Field(default_factory=list)
    position: int = 0


class VideoCharacterUpdateForm(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=4000)
    image_file_ids: Optional[list[str]] = None
    position: Optional[int] = None


def _to_model(row: VideoCharacter) -> VideoCharacterModel:
    try:
        file_ids = json.loads(row.image_file_ids or "[]")
    except (TypeError, ValueError):
        file_ids = []
    return VideoCharacterModel(
        id=row.id,
        user_id=row.user_id,
        chat_id=row.chat_id,
        name=row.name,
        description=row.description or "",
        image_file_ids=[i for i in file_ids if isinstance(i, str)],
        position=row.position or 0,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class VideoCharactersTable:
    def get_by_chat_id(self, user_id: str, chat_id: str) -> list[VideoCharacterModel]:
        with get_db() as db:
            rows = (
                db.query(VideoCharacter)
                .filter_by(user_id=user_id, chat_id=chat_id)
                .order_by(VideoCharacter.position, VideoCharacter.created_at)
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
                chat_id=form.chat_id,
                name=form.name.strip(),
                description=form.description.strip(),
                image_file_ids=json.dumps(
                    form.image_file_ids[:MAX_IMAGES_PER_CHARACTER]
                ),
                position=form.position,
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
            if form.position is not None:
                row.position = form.position
            row.updated_at = int(time.time())
            db.commit()
            db.refresh(row)
            return _to_model(row)

    def delete(self, user_id: str, id: str) -> bool:
        with get_db() as db:
            deleted = db.query(VideoCharacter).filter_by(id=id, user_id=user_id).delete()
            db.commit()
            return bool(deleted)


VideoCharacters = VideoCharactersTable()

VideoCharacter.__table__.create(bind=engine, checkfirst=True)
