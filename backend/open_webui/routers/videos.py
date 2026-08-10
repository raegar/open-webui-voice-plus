import io
import random
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from open_webui.constants import ERROR_MESSAGES
from open_webui.models.chats import Chats
from open_webui.routers.files import upload_file_handler
from open_webui.utils.access_control import has_permission
from open_webui.utils.auth import get_verified_user
from open_webui.utils.videos.comfyui import ComfyUIVideoClient


router = APIRouter()


class CreateVideoForm(BaseModel):
    prompt: str
    mode: Literal["text"] = "text"
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


async def video_generations(
    request: Request,
    form_data: CreateVideoForm,
    metadata: Optional[dict] = None,
    user=None,
):
    metadata = metadata or {}
    seed = form_data.seed if form_data.seed is not None else random.randrange(2**63)
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
    )
    generation_metadata = {
        **form_data.model_dump(exclude_none=True),
        **metadata,
        "seed": seed,
        "content_type": content_type,
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
            "mode": form_data.mode,
            "aspect_ratio": form_data.aspect_ratio,
            "megapixels": form_data.megapixels,
            "duration": form_data.duration,
            "seed": seed,
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
