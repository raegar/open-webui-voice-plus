"""Chats the user has marked private, so work mode can hide them.

Kept in a table of the fork's own rather than as a column on `chat`, because the
upstream chat model and routers ship unmodified in the base image. Hiding happens
in the browser; this table only records which chats are private.

A row can outlive its chat. That is harmless: an id that matches no chat hides
nothing.
"""

import time

from open_webui.internal.db import Base, engine, get_db
from sqlalchemy import BigInteger, Column, String


class PrivateChat(Base):
    __tablename__ = "private_chat"

    user_id = Column(String, primary_key=True)
    chat_id = Column(String, primary_key=True)
    created_at = Column(BigInteger, nullable=False)


class PrivateChatsTable:
    def get_chat_ids(self, user_id: str) -> list[str]:
        with get_db() as db:
            rows = db.query(PrivateChat.chat_id).filter_by(user_id=user_id).all()
            return [row.chat_id for row in rows]

    def set_private(self, user_id: str, chat_id: str, private: bool) -> None:
        with get_db() as db:
            existing = (
                db.query(PrivateChat).filter_by(user_id=user_id, chat_id=chat_id).first()
            )
            if private and not existing:
                db.add(
                    PrivateChat(
                        user_id=user_id, chat_id=chat_id, created_at=int(time.time())
                    )
                )
            elif not private and existing:
                db.delete(existing)
            db.commit()


PrivateChats = PrivateChatsTable()

PrivateChat.__table__.create(bind=engine, checkfirst=True)
