"""Browse and prune the persistent memory pipeline's recorded messages.

Admin only: the pipeline keeps one store for every account, with no user ids, so
nothing here can be scoped to the caller. See utils/memory_store.py.
"""

import asyncio
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from open_webui.utils.auth import get_admin_user
from open_webui.utils.memory_store import MAX_BULK_DELETE, MemoryStore


router = APIRouter()


async def _run(fn, *args, **kwargs):
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/summary")
async def memory_summary(user=Depends(get_admin_user)):
    store = MemoryStore()
    return {"path": store.path, **(await _run(store.summary))}


@router.get("/messages")
async def list_memories(
    q: str = "",
    role: Literal["", "user", "assistant"] = "",
    source: Literal["", "recorded", "imported"] = "",
    since: Optional[str] = None,
    until: Optional[str] = None,
    conversation_id: str = "",
    order: Literal["newest", "oldest"] = "newest",
    offset: int = 0,
    limit: int = 50,
    user=Depends(get_admin_user),
):
    return await _run(
        MemoryStore().search,
        q=q,
        role=role,
        source=source,
        since=since or None,
        until=until or None,
        conversation_id=conversation_id,
        order=order,
        offset=offset,
        limit=limit,
    )


class DeleteForm(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=MAX_BULK_DELETE)


@router.post("/delete")
async def delete_memories(form_data: DeleteForm, user=Depends(get_admin_user)):
    """Permanently delete memories. The first deletion each day backs the store up."""
    return await _run(MemoryStore().delete, form_data.ids)
