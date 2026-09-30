"""Which chats are private, for work mode.

The browser does the hiding: it loads these ids once and leaves matching chats out
of every list while the user's hidePrivate setting is on. Characters carry their
own `private` flag on `video_character` instead, since that table is the fork's.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from open_webui.models.chats import Chats
from open_webui.models.private_chats import PrivateChats
from open_webui.utils.auth import get_verified_user


router = APIRouter()


@router.get("/chats")
async def list_private_chats(user=Depends(get_verified_user)) -> list[str]:
    return PrivateChats.get_chat_ids(user.id)


class PrivateChatForm(BaseModel):
    private: bool


@router.post("/chats/{chat_id}")
async def set_chat_private(
    chat_id: str,
    form_data: PrivateChatForm,
    user=Depends(get_verified_user),
):
    if not Chats.get_chat_by_id_and_user_id(chat_id, user.id):
        raise HTTPException(status_code=404, detail="Chat not found")
    PrivateChats.set_private(user.id, chat_id, form_data.private)
    return {"private": form_data.private}
