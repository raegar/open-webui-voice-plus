"""Per-chat Game Master settings, status and journal. See docs/game-master-design.md."""

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from open_webui.models.chats import Chats
from open_webui.models.game_master import GameMaster, normalize_config
from open_webui.utils.auth import get_verified_user
from open_webui.utils.game_master import (
    find_state_entry_id,
    get_scene_npcs,
    is_running,
    open_story,
    queue_npc_portrait,
    run_table_talk,
    schedule_gm_pass,
)


router = APIRouter()


def _chat_or_404(chat_id: str, user) -> object:
    chat = Chats.get_chat_by_id_and_user_id(chat_id, user.id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    return chat


def _leaf(chat) -> tuple[Optional[str], dict]:
    history = (getattr(chat, "chat", None) or {}).get("history") or {}
    return history.get("currentId"), history.get("messages") or {}


def _chat_model(chat, leaf: Optional[str], messages: dict) -> str:
    """The model the chat is using: the latest reply's, else the chat's first model."""
    current = leaf
    seen = set()
    while current and current not in seen:
        seen.add(current)
        message = messages.get(current) or {}
        if message.get("role") == "assistant" and message.get("model"):
            return message["model"]
        current = message.get("parentId")
    models = (getattr(chat, "chat", None) or {}).get("models") or []
    return models[0] if models else ""


def _entry_view(entry) -> Optional[dict]:
    if not entry:
        return None
    return {
        "id": entry.id,
        "message_id": entry.message_id,
        "kind": entry.kind,
        "prior_id": entry.prior_id,
        "user_message": entry.user_message,
        "gm_reply": entry.gm_reply,
        "reasoning": entry.reasoning,
        "model_reasoning": entry.model_reasoning,
        "observations": entry.observations,
        "patch": entry.patch,
        "state": entry.state,
        "note": entry.note,
        "model": entry.model,
        "tokens": entry.tokens,
        "duration_ms": entry.duration_ms,
        "error": entry.error,
        "created_at": entry.created_at,
    }


def _status(user, chat_id: str, chat) -> dict:
    session = GameMaster.get_session(user.id, chat_id)
    leaf, messages = _leaf(chat)
    index = GameMaster.get_entry_index(user.id, chat_id)
    current_id = find_state_entry_id(index, messages, leaf, inclusive=True)
    current = GameMaster.get_entry(user.id, current_id) if current_id else None
    # A failure newer than the entry in use is worth showing; older ones are history.
    latest = index[-1] if index else None
    last_error = None
    if latest and latest["error"] and (not current or latest["created_at"] > current.created_at):
        failed = GameMaster.get_entry(user.id, latest["id"])
        last_error = {"error": failed.error, "created_at": failed.created_at} if failed else None
    portraits = [p.model_dump() for p in GameMaster.get_portraits(user.id, chat_id)]
    return {
        "enabled": bool(session and session.enabled),
        "config": session.config if session else normalize_config({}),
        "running": is_running(chat_id),
        "current": _entry_view(current),
        "last_error": last_error,
        "entries": len(index),
        "portraits": portraits,
        "talk": [
            {"id": t.id, "player": t.user_message, "gm": t.gm_reply, "created_at": t.created_at}
            for t in GameMaster.get_table_talk(user.id, chat_id, 20)
        ],
    }


@router.get("/chats/{chat_id}")
async def get_game_master(chat_id: str, user=Depends(get_verified_user)):
    chat = _chat_or_404(chat_id, user)
    return _status(user, chat_id, chat)


class GameMasterForm(BaseModel):
    enabled: Optional[bool] = None
    agenda: Optional[str] = Field(default=None, max_length=4000)
    intensity: Optional[Literal["light", "firm", "ruthless"]] = None
    player_character_id: Optional[str] = Field(default=None, max_length=200)
    cadence: Optional[Literal[1, 2, 3]] = None
    auto_portraits: Optional[bool] = None


@router.post("/chats/{chat_id}")
async def update_game_master(
    request: Request,
    chat_id: str,
    form_data: GameMasterForm,
    user=Depends(get_verified_user),
):
    chat = _chat_or_404(chat_id, user)
    before = GameMaster.get_session(user.id, chat_id)
    config = {
        key: value
        for key, value in form_data.model_dump(exclude={"enabled"}).items()
        if value is not None
    }
    GameMaster.update_session(
        user.id, chat_id, enabled=form_data.enabled, config=config or None
    )

    # Switching on spawns the GM: a session-zero pass over the chat so far, unless a
    # campaign already exists here, in which case it simply resumes.
    turned_on = form_data.enabled and not (before and before.enabled)
    if turned_on:
        leaf, messages = _leaf(chat)
        index = GameMaster.get_entry_index(user.id, chat_id)
        if not find_state_entry_id(index, messages, leaf, inclusive=True):
            model_id = _chat_model(chat, leaf, messages)
            schedule_gm_pass(request, user, chat_id, leaf, model_id, kind="setup")
    return _status(user, chat_id, chat)


class ConsultForm(BaseModel):
    model: Optional[str] = None


@router.post("/chats/{chat_id}/consult")
async def consult_game_master(
    request: Request,
    chat_id: str,
    form_data: ConsultForm,
    user=Depends(get_verified_user),
):
    """Redo the GM's pass for the current point in the chat, with current settings."""
    chat = _chat_or_404(chat_id, user)
    leaf, messages = _leaf(chat)
    model_id = form_data.model or _chat_model(chat, leaf, messages)
    if not schedule_gm_pass(request, user, chat_id, leaf, model_id, kind="consult"):
        raise HTTPException(status_code=400, detail="The Game Master is off for this chat")
    return _status(user, chat_id, chat)


class OpeningForm(BaseModel):
    # The model the chat will use; the chat's own first model if left out.
    model: Optional[str] = None


@router.post("/chats/{chat_id}/opening")
async def open_game_master_story(
    request: Request,
    chat_id: str,
    form_data: OpeningForm,
    user=Depends(get_verified_user),
):
    """Have the GM open a new, empty chat: plan session zero and write the scene card
    the player reads as the first message. Waits for the plan."""
    chat = _chat_or_404(chat_id, user)
    session = GameMaster.get_session(user.id, chat_id)
    if not session or not session.enabled:
        raise HTTPException(status_code=400, detail="The Game Master is off for this chat")
    leaf, messages = _leaf(chat)
    if leaf or messages:
        raise HTTPException(status_code=400, detail="This story has already started")
    model_id = form_data.model or _chat_model(chat, leaf, messages)
    scene = await open_story(request, user, chat_id, model_id)
    if not scene:
        raise HTTPException(
            status_code=502,
            detail="The Game Master did not write an opening. Try again, or type your own first message.",
        )
    return {"scene": scene, "status": _status(user, chat_id, chat)}


@router.get("/chats/{chat_id}/journal")
async def get_game_master_journal(
    chat_id: str, limit: int = 100, user=Depends(get_verified_user)
):
    _chat_or_404(chat_id, user)
    limit = max(1, min(limit, 500))
    return [_entry_view(entry) for entry in GameMaster.get_journal(user.id, chat_id, limit)]


@router.get("/chats/{chat_id}/index")
async def get_game_master_index(chat_id: str, user=Depends(get_verified_user)):
    """Which messages have GM passes, for the markers on steered replies."""
    _chat_or_404(chat_id, user)
    session = GameMaster.get_session(user.id, chat_id)
    return {
        "enabled": bool(session and session.enabled),
        "entries": GameMaster.get_entry_index(user.id, chat_id),
    }


class TalkForm(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


@router.post("/chats/{chat_id}/talk")
async def talk_to_game_master(
    request: Request,
    chat_id: str,
    form_data: TalkForm,
    user=Depends(get_verified_user),
):
    """Table talk: speak to the GM out of character. Waits for its answer; the plan
    update it leads to runs afterwards in the background."""
    chat = _chat_or_404(chat_id, user)
    session = GameMaster.get_session(user.id, chat_id)
    if not session or not session.enabled:
        raise HTTPException(status_code=400, detail="The Game Master is off for this chat")
    leaf, messages = _leaf(chat)
    entry = await run_table_talk(
        request,
        user,
        chat_id,
        leaf,
        _chat_model(chat, leaf, messages),
        form_data.message.strip(),
    )
    if not entry or not entry.gm_reply:
        raise HTTPException(
            status_code=502, detail="The Game Master did not answer. Check the transcript."
        )
    return {"reply": entry.gm_reply, "status": _status(user, chat_id, chat)}


@router.post("/chats/{chat_id}/reroll")
async def reroll_game_master(
    request: Request,
    chat_id: str,
    user=Depends(get_verified_user),
):
    """Replace the direction in force with a fresh take from the same starting point."""
    chat = _chat_or_404(chat_id, user)
    leaf, messages = _leaf(chat)
    index = GameMaster.get_entry_index(user.id, chat_id)
    current_id = find_state_entry_id(index, messages, leaf, inclusive=True)
    current = GameMaster.get_entry(user.id, current_id) if current_id else None
    # Table talk and the plan it led to answer something the player said; rerolling
    # them would throw that away. Ask again at the table instead.
    if not current or current.kind in ("table_talk", "talk_plan"):
        raise HTTPException(status_code=400, detail="There is no direction to reroll")
    if not schedule_gm_pass(
        request,
        user,
        chat_id,
        current.message_id or None,
        _chat_model(chat, leaf, messages),
        kind="reroll",
        prior_override=current.prior_id,
        rejected_note=current.note,
    ):
        raise HTTPException(status_code=400, detail="The Game Master is off for this chat")
    return _status(user, chat_id, chat)


class PortraitForm(BaseModel):
    # What should change, e.g. "more menacing, less human". Optional.
    direction: str = Field(default="", max_length=500)


@router.post("/chats/{chat_id}/npcs/{npc_id}/portrait")
async def regenerate_npc_portrait(
    request: Request,
    chat_id: str,
    npc_id: str,
    form_data: Optional[PortraitForm] = None,
    user=Depends(get_verified_user),
):
    """Draw an NPC again with a new seed and a freshly written prompt, from their look
    in the current plan and any direction the player gives."""
    chat = _chat_or_404(chat_id, user)
    leaf, messages = _leaf(chat)
    index = GameMaster.get_entry_index(user.id, chat_id)
    current_id = find_state_entry_id(index, messages, leaf, inclusive=True)
    current = GameMaster.get_entry(user.id, current_id) if current_id else None
    npc = next(
        (n for n in ((current.state or {}).get("npcs") or []) if n.get("id") == npc_id),
        None,
    ) if current else None
    if not npc:
        raise HTTPException(status_code=404, detail="No such NPC in the current plan")
    if not await queue_npc_portrait(
        request,
        user,
        chat_id,
        npc,
        model_id=_chat_model(chat, leaf, messages),
        direction=form_data.direction.strip() if form_data else "",
    ):
        raise HTTPException(
            status_code=400,
            detail="Portraits need video generation to be enabled, and the NPC needs a look.",
        )
    return _status(user, chat_id, chat)


# The image types Video Studio can use as a reference picture.
PORTRAIT_UPLOAD_TYPES = ("image/png", "image/jpeg", "image/webp")


class PortraitUploadForm(BaseModel):
    # An Open WebUI file the caller has already uploaded.
    file_id: str = Field(min_length=1, max_length=200)


@router.post("/chats/{chat_id}/npcs/{npc_id}/portrait/upload")
async def upload_npc_portrait(
    chat_id: str,
    npc_id: str,
    form_data: PortraitUploadForm,
    user=Depends(get_verified_user),
):
    """Use the player's own picture as an NPC's portrait in this chat.

    It replaces the generated one for good: it is what the Cast shows and what Video
    Studio uses as their reference, and automatic portraits never draw over it.
    Regenerating draws a new one in its place.
    """
    from open_webui.models.files import Files

    chat = _chat_or_404(chat_id, user)
    leaf, messages = _leaf(chat)
    index = GameMaster.get_entry_index(user.id, chat_id)
    current_id = find_state_entry_id(index, messages, leaf, inclusive=True)
    current = GameMaster.get_entry(user.id, current_id) if current_id else None
    npcs = ((current.state or {}).get("npcs") or []) if current else []
    npc = next((n for n in npcs if isinstance(n, dict) and n.get("id") == npc_id), None)
    if not npc or not (npc.get("name") or "").strip():
        raise HTTPException(status_code=404, detail="No such NPC in the current plan")

    file_item = Files.get_file_by_id(form_data.file_id)
    if not file_item or file_item.user_id != user.id:
        raise HTTPException(status_code=400, detail="Unknown image")
    content_type = ((file_item.meta or {}).get("content_type") or "").lower()
    if content_type not in PORTRAIT_UPLOAD_TYPES:
        raise HTTPException(status_code=400, detail="Use a PNG, JPEG or WebP image")

    GameMaster.set_portrait(
        user.id,
        chat_id,
        npc_id,
        name=npc["name"].strip(),
        look=(npc.get("look") or "").strip(),
        status="ready",
        file_id=file_item.id,
        seed="",
        prompt="",
        error="",
        source="uploaded",
    )
    return _status(user, chat_id, chat)


@router.get("/chats/{chat_id}/scene-npcs")
async def get_game_master_scene_npcs(
    request: Request,
    chat_id: str,
    message_id: Optional[str] = None,
    user=Depends(get_verified_user),
):
    """NPCs on stage at a message, with portrait file ids, for the Video Studio."""
    _chat_or_404(chat_id, user)
    return await get_scene_npcs(request, user, chat_id, message_id)
