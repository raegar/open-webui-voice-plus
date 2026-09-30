"""Per-chat Game Master settings, status and journal. See docs/game-master-design.md."""

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from open_webui.models.chats import Chats
from open_webui.models.game_master import GameMaster, normalize_config
from open_webui.utils.auth import get_verified_user
from open_webui.utils.game_master import (
    find_state_entry_id,
    is_running,
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
    return {
        "enabled": bool(session and session.enabled),
        "config": session.config if session else normalize_config({}),
        "running": is_running(chat_id),
        "current": _entry_view(current),
        "last_error": last_error,
        "entries": len(index),
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


@router.get("/chats/{chat_id}/journal")
async def get_game_master_journal(
    chat_id: str, limit: int = 100, user=Depends(get_verified_user)
):
    _chat_or_404(chat_id, user)
    limit = max(1, min(limit, 500))
    return [_entry_view(entry) for entry in GameMaster.get_journal(user.id, chat_id, limit)]
