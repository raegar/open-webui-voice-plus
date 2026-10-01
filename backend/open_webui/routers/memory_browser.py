"""Browse and prune the persistent memory pipeline's recorded messages.

Admin only: the pipeline keeps one store for every account, with no user ids, so
nothing here can be scoped to the caller. See utils/memory_store.py, and
utils/memory_characters.py for how memories are linked to characters.
"""

import asyncio
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from open_webui.utils.auth import get_admin_user
from open_webui.utils.memory_characters import load_owui_sources
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
    """Counts and the character list, after linking any memories recorded since."""
    store = MemoryStore()
    linked = await _run(store.sync_characters, load_owui_sources)
    return {"path": store.path, "linked": linked, **(await _run(store.summary))}


@router.post("/characters/relink")
async def relink_memory_characters(user=Depends(get_admin_user)):
    """Rebuild every memory's character links, e.g. after attaching characters to
    chats that already had memories."""
    store = MemoryStore()
    linked = await _run(store.sync_characters, load_owui_sources, True)
    return {"linked": linked, **(await _run(store.summary))}


@router.get("/messages")
async def list_memories(
    q: str = "",
    role: Literal["", "user", "assistant"] = "",
    source: Literal["", "recorded", "imported"] = "",
    since: Optional[str] = None,
    until: Optional[str] = None,
    conversation_id: str = "",
    include: list[str] = Query(default=[]),
    exclude: list[str] = Query(default=[]),
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
        include=include,
        exclude=exclude,
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


class DeleteRangeForm(BaseModel):
    """A date range plus the browser's other filters, and the count confirmed."""

    expected: int = Field(ge=0)
    since: Optional[str] = None
    until: Optional[str] = None
    q: str = ""
    role: Literal["", "user", "assistant"] = ""
    source: Literal["", "recorded", "imported"] = ""
    conversation_id: str = ""
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)


@router.post("/delete-range")
async def delete_memory_range(form_data: DeleteRangeForm, user=Depends(get_admin_user)):
    """Permanently delete every memory in a date range that matches the filters.
    Refuses if the number matching has changed since it was confirmed."""
    filters = form_data.model_dump()
    expected = filters.pop("expected")
    filters["since"] = filters["since"] or None
    filters["until"] = filters["until"] or None
    return await _run(MemoryStore().delete_matching, expected, **filters)
