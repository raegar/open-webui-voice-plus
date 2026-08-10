import base64
import binascii
import mimetypes
import io
import random
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field

from open_webui.constants import ERROR_MESSAGES
from open_webui.models.chats import Chats
from open_webui.models.files import Files
from open_webui.routers.files import upload_file_handler
from open_webui.utils.access_control import has_permission
from open_webui.utils.auth import get_verified_user
from open_webui.utils.videos.comfyui import ComfyUIVideoClient


router = APIRouter()
NUMBER_SAFE_INTEGER_MAX = 2**53 - 1


class CreateVideoForm(BaseModel):
    prompt: str
    mode: Literal["text"] = "text"
    first_frame_data_url: Optional[str] = Field(default=None, max_length=36_000_000)
    last_frame_data_url: Optional[str] = Field(default=None, max_length=36_000_000)
    aspect_ratio: Literal["16:9", "9:16", "1:1"] = "16:9"
    megapixels: Literal[0.2, 0.4] = 0.2
    duration: Literal[3, 5, 10] = 3
    seed: Optional[int] = Field(default=None, ge=0, le=2**63 - 1)
    chat_id: Optional[str] = None
    message_id: Optional[str] = None


def _get_text_content(content) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return " ".join(
            part.get("text", "").strip()
            for part in content
            if isinstance(part, dict)
            and part.get("type") == "text"
            and isinstance(part.get("text"), str)
        ).strip()
    return ""


def _resolve_regeneration_prompt(form_data: CreateVideoForm, user) -> Optional[str]:
    if not form_data.chat_id or not form_data.message_id:
        return None
    chat = Chats.get_chat_by_id_and_user_id(form_data.chat_id, user.id)
    if not chat:
        return None
    message = (
        chat.chat.get("history", {})
        .get("messages", {})
        .get(form_data.message_id, {})
    )
    for file in message.get("files", []):
        if (
            isinstance(file, dict)
            and file.get("type") == "video"
            and isinstance(file.get("prompt"), str)
            and file["prompt"].strip()
        ):
            return file["prompt"].strip()
    return _get_text_content(message.get("content")) or None


def _load_frame_data_url(value: str, role: str) -> tuple[bytes, str, str]:
    try:
        header, encoded = value.split(",", 1)
        content_type = header.removeprefix("data:").split(";", 1)[0].lower()
        if not header.endswith(";base64") or content_type not in {
            "image/jpeg",
            "image/png",
            "image/webp",
        }:
            raise ValueError
        image_data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{role} must be a base64 PNG, JPEG, or WebP image",
        ) from exc

    if not image_data or len(image_data) > 25 * 1024 * 1024:
        raise HTTPException(
            status_code=400,
            detail=f"{role} must be no larger than 25 MB",
        )
    extension = mimetypes.guess_extension(content_type) or ".png"
    return image_data, f"{role.lower().replace(' ', '-')}{extension}", content_type


def _upload_video(request, video_data, filename, content_type, metadata, user):
    file = UploadFile(
        file=io.BytesIO(video_data),
        filename=filename,
        headers={"content-type": content_type},
    )
    file_item = upload_file_handler(
        request,
        file=file,
        metadata=metadata,
        process=False,
        user=user,
    )
    if metadata.get("chat_id") and metadata.get("message_id"):
        Chats.insert_chat_files(
            chat_id=metadata["chat_id"],
            message_id=metadata["message_id"],
            file_ids=[file_item.id],
            user_id=user.id,
        )
    return file_item, request.app.url_path_for("get_file_content_by_id", id=file_item.id)

def _get_video_history_items(request: Request, user, limit: int) -> list[dict]:
    files = sorted(
        Files.get_files_by_user_id(user.id),
        key=lambda item: (item.created_at or 0, item.id),
        reverse=True,
    )
    items = []
    for file_item in files:
        meta = file_item.meta if isinstance(file_item.meta, dict) else {}
        generation = meta.get("data") if isinstance(meta.get("data"), dict) else {}
        content_type = meta.get("content_type") or generation.get("content_type")
        prompt = generation.get("prompt")
        if not (
            isinstance(content_type, str)
            and content_type.startswith("video/")
            and isinstance(prompt, str)
            and prompt.strip()
        ):
            continue
        seed = generation.get("seed")
        items.append(
            {
                "id": file_item.id,
                "url": str(
                    request.app.url_path_for(
                        "get_file_content_by_id", id=file_item.id
                    )
                ),
                "filename": file_item.filename,
                "content_type": content_type,
                "prompt": prompt,
                "mode": generation.get("mode", "text"),
                "aspect_ratio": generation.get("aspect_ratio", "16:9"),
                "megapixels": generation.get("megapixels", 0.2),
                "duration": generation.get("duration", 3),
                "seed": str(seed) if seed is not None else "",
                "has_first_frame": bool(generation.get("has_first_frame", False)),
                "has_last_frame": bool(generation.get("has_last_frame", False)),
                "created_at": file_item.created_at,
            }
        )
        if len(items) >= limit:
            break
    return items


@router.get("/history")
async def get_video_history(
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    user=Depends(get_verified_user),
):
    if not request.app.state.config.ENABLE_VIDEO_GENERATION:
        raise HTTPException(status_code=403, detail=ERROR_MESSAGES.ACCESS_PROHIBITED)
    if user.role != "admin" and not has_permission(
        user.id, "features.image_generation", request.app.state.config.USER_PERMISSIONS
    ):
        raise HTTPException(status_code=403, detail=ERROR_MESSAGES.ACCESS_PROHIBITED)
    return _get_video_history_items(request, user, limit)



async def video_generations(
    request: Request,
    form_data: CreateVideoForm,
    metadata: Optional[dict] = None,
    user=None,
):
    metadata = metadata or {}
    seed = (
        form_data.seed
        if form_data.seed is not None
        else random.randrange(NUMBER_SAFE_INTEGER_MAX + 1)
    )
    if form_data.last_frame_data_url and not form_data.first_frame_data_url:
        raise HTTPException(
            status_code=400,
            detail="A last frame requires a first frame",
        )
    first_frame = (
        _load_frame_data_url(form_data.first_frame_data_url, "First frame")
        if form_data.first_frame_data_url
        else None
    )
    last_frame = (
        _load_frame_data_url(form_data.last_frame_data_url, "Last frame")
        if form_data.last_frame_data_url
        else None
    )
    client = ComfyUIVideoClient(
        request.app.state.config.COMFYUI_VIDEO_BASE_URL,
        request.app.state.config.COMFYUI_VIDEO_API_KEY,
        request.app.state.config.COMFYUI_VIDEO_TIMEOUT,
    )
    video_data, filename, content_type = await client.generate(
        form_data.prompt,
        form_data.aspect_ratio,
        form_data.megapixels,
        form_data.duration,
        seed,
        first_frame,
        last_frame,
    )
    generation_metadata = {
        **form_data.model_dump(
            exclude={"first_frame_data_url", "last_frame_data_url"}, exclude_none=True
        ),
        **metadata,
        "seed": seed,
        "content_type": content_type,
        "has_first_frame": first_frame is not None,
        "has_last_frame": last_frame is not None,
    }
    _, url = _upload_video(
        request,
        video_data,
        filename,
        content_type,
        generation_metadata,
        user,
    )
    return [
        {
            "url": url,
            "content_type": content_type,
            "prompt": form_data.prompt,
            "mode": "first-last" if last_frame else "image" if first_frame else "text",
            "aspect_ratio": form_data.aspect_ratio,
            "megapixels": form_data.megapixels,
            "duration": form_data.duration,
            "seed": str(seed),
        }
    ]


@router.post("/generations")
async def generate_videos(
    request: Request,
    form_data: CreateVideoForm,
    user=Depends(get_verified_user),
):
    if not request.app.state.config.ENABLE_VIDEO_GENERATION:
        raise HTTPException(status_code=403, detail=ERROR_MESSAGES.ACCESS_PROHIBITED)
    if user.role != "admin" and not has_permission(
        user.id, "features.image_generation", request.app.state.config.USER_PERMISSIONS
    ):
        raise HTTPException(status_code=403, detail=ERROR_MESSAGES.ACCESS_PROHIBITED)

    regeneration_prompt = _resolve_regeneration_prompt(form_data, user)
    if regeneration_prompt:
        form_data.prompt = regeneration_prompt
    return await video_generations(request, form_data, user=user)
