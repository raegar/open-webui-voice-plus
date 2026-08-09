import base64
import io
import mimetypes
import random
import re
from pathlib import Path
from typing import Literal, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from open_webui.constants import ERROR_MESSAGES
from open_webui.models.chats import Chats
from open_webui.models.files import Files
from open_webui.routers.files import upload_file_handler
from open_webui.storage.provider import Storage
from open_webui.utils.access_control import has_permission
from open_webui.utils.auth import get_verified_user
from open_webui.utils.videos.comfyui import ComfyUIVideoClient


router = APIRouter()


class CreateVideoForm(BaseModel):
    prompt: str
    mode: Literal["text", "image"] = "text"
    aspect_ratio: Literal["16:9", "9:16", "1:1"] = "16:9"
    megapixels: Literal[0.2, 0.4] = 0.2
    duration: Literal[3, 5] = 3
    seed: Optional[int] = Field(default=None, ge=0, le=2**63 - 1)
    source_image_url: Optional[str] = None
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


def _load_source_image(url: str, user) -> tuple[bytes, str, str]:
    if url.startswith("data:image/"):
        header, encoded = url.split(",", 1)
        content_type = header.split(";", 1)[0].removeprefix("data:")
        extension = mimetypes.guess_extension(content_type) or ".png"
        return base64.b64decode(encoded), f"source{extension}", content_type

    file_match = re.search(r"/api/v1/files/([^/]+)/content(?:/|$)", url)
    if file_match:
        file_item = Files.get_file_by_id_and_user_id(file_match.group(1), user.id)
        if not file_item and user.role == "admin":
            file_item = Files.get_file_by_id(file_match.group(1))
        if not file_item:
            raise HTTPException(status_code=404, detail="Source image was not found")
        file_path = Path(Storage.get_file(file_item.path))
        content_type = file_item.meta.get("content_type") or mimetypes.guess_type(
            file_item.filename
        )[0] or "image/png"
        return file_path.read_bytes(), file_item.filename, content_type

    if url.startswith(("http://", "https://")):
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "image/png").split(";", 1)[0]
        if not content_type.startswith("image/"):
            raise HTTPException(status_code=400, detail="Source URL is not an image")
        extension = mimetypes.guess_extension(content_type) or ".png"
        return response.content, f"source{extension}", content_type

    raise HTTPException(status_code=400, detail="Unsupported source image URL")


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
    source_image = None
    if form_data.mode == "image":
        if not form_data.source_image_url:
            raise HTTPException(
                status_code=400,
                detail="Image-to-video requires an attached source image",
            )
        source_image = _load_source_image(form_data.source_image_url, user)

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
        source_image,
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
