import asyncio
import base64
import binascii
import io
import logging
import mimetypes
import random
import time
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field

from open_webui.constants import ERROR_MESSAGES
from open_webui.models.chats import Chats
from open_webui.models.files import Files
from open_webui.models.video_characters import (
    VideoCharacterForm,
    VideoCharacterUpdateForm,
    VideoCharacters,
)
from open_webui.routers.files import upload_file_handler
from open_webui.utils.access_control import has_permission
from open_webui.utils.auth import get_verified_user
from open_webui.utils.videos.comfyui import ComfyUIVideoClient


router = APIRouter()
log = logging.getLogger(__name__)
NUMBER_SAFE_INTEGER_MAX = 2**53 - 1
VIDEO_GENERATION_JOBS: dict[str, dict] = {}
VIDEO_GENERATION_TASKS: set[asyncio.Task] = set()


class CreateVideoForm(BaseModel):
    prompt: str
    mode: Literal["text", "reference"] = "text"
    first_frame_data_url: Optional[str] = Field(default=None, max_length=36_000_000)
    last_frame_data_url: Optional[str] = Field(default=None, max_length=36_000_000)
    reference_image_data_urls: list[str] = Field(default_factory=list, max_length=9)
    aspect_ratio: Literal["16:9", "9:16", "1:1"] = "16:9"
    megapixels: Literal[0.2, 0.4] = 0.2
    duration: Literal[3, 5, 10, 15] = 3
    seed: Optional[int] = Field(default=None, ge=0, le=2**63 - 1)
    chat_id: Optional[str] = None
    message_id: Optional[str] = None


class CreateVideoJobForm(CreateVideoForm):
    job_id: UUID


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
    if len(value) > 36_000_000:
        raise HTTPException(
            status_code=400,
            detail=f"{role} must be no larger than 25 MB",
        )
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
                "reference_image_count": generation.get("reference_image_count", 0),
                "created_at": file_item.created_at,
            }
        )
        if len(items) >= limit:
            break
    return items


def _check_video_access(request: Request, user) -> None:
    if not request.app.state.config.ENABLE_VIDEO_GENERATION:
        raise HTTPException(status_code=403, detail=ERROR_MESSAGES.ACCESS_PROHIBITED)
    if user.role != "admin" and not has_permission(
        user.id, "features.image_generation", request.app.state.config.USER_PERMISSIONS
    ):
        raise HTTPException(status_code=403, detail=ERROR_MESSAGES.ACCESS_PROHIBITED)


@router.get("/history")
async def get_video_history(
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    user=Depends(get_verified_user),
):
    _check_video_access(request, user)
    return _get_video_history_items(request, user, limit)


def _verify_owns_files(user, file_ids: list[str]) -> None:
    """Reject file ids the caller does not own, so a roster cannot reference
    another user's uploads and surface them through the studio."""
    for file_id in file_ids:
        file_item = Files.get_file_by_id(file_id)
        if not file_item or file_item.user_id != user.id:
            raise HTTPException(status_code=400, detail="Unknown reference image")


@router.get("/characters")
async def list_video_characters(
    request: Request,
    chat_id: str = Query(...),
    user=Depends(get_verified_user),
):
    _check_video_access(request, user)
    return VideoCharacters.get_by_chat_id(user.id, chat_id)


@router.post("/characters")
async def create_video_character(
    request: Request,
    form_data: VideoCharacterForm,
    user=Depends(get_verified_user),
):
    _check_video_access(request, user)
    _verify_owns_files(user, form_data.image_file_ids)
    return VideoCharacters.insert(user.id, form_data)


@router.post("/characters/{character_id}")
async def update_video_character(
    request: Request,
    character_id: str,
    form_data: VideoCharacterUpdateForm,
    user=Depends(get_verified_user),
):
    _check_video_access(request, user)
    if form_data.image_file_ids is not None:
        _verify_owns_files(user, form_data.image_file_ids)
    character = VideoCharacters.update(user.id, character_id, form_data)
    if not character:
        raise HTTPException(status_code=404, detail="Character not found")
    return character


@router.delete("/characters/{character_id}")
async def delete_video_character(
    request: Request,
    character_id: str,
    user=Depends(get_verified_user),
):
    _check_video_access(request, user)
    if not VideoCharacters.delete(user.id, character_id):
        raise HTTPException(status_code=404, detail="Character not found")
    return {"deleted": True}



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
    if form_data.mode == "reference" and (
        not form_data.reference_image_data_urls
        or form_data.first_frame_data_url
        or form_data.last_frame_data_url
    ):
        raise HTTPException(
            status_code=400,
            detail="Reference mode requires 1-9 reference images and cannot use frame anchors",
        )
    if form_data.mode != "reference" and form_data.reference_image_data_urls:
        raise HTTPException(
            status_code=400,
            detail="Reference images require Reference-to-Video mode",
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
    reference_images = [
        _load_frame_data_url(value, f"Reference image {index + 1}")
        for index, value in enumerate(form_data.reference_image_data_urls)
    ]
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
        reference_images,
    )
    generation_metadata = {
        **form_data.model_dump(
            exclude={
                "first_frame_data_url",
                "last_frame_data_url",
                "reference_image_data_urls",
            },
            exclude_none=True,
        ),
        **metadata,
        "seed": seed,
        "content_type": content_type,
        "has_first_frame": first_frame is not None,
        "has_last_frame": last_frame is not None,
        "reference_image_count": len(reference_images),
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
            "mode": (
                "reference"
                if reference_images
                else "first-last"
                if last_frame
                else "image"
                if first_frame
                else "text"
            ),
            "aspect_ratio": form_data.aspect_ratio,
            "megapixels": form_data.megapixels,
            "duration": form_data.duration,
            "seed": str(seed),
        }
    ]


def _public_video_job(job_id: str, job: dict) -> dict:
    return {
        "job_id": job_id,
        "status": job["status"],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
        "result": job.get("result"),
        "error": job.get("error"),
    }


def _prune_video_jobs() -> None:
    cutoff = time.time() - 24 * 60 * 60
    stale = [
        job_id
        for job_id, job in VIDEO_GENERATION_JOBS.items()
        if job["status"] in {"completed", "failed"} and job["updated_at"] < cutoff
    ]
    for job_id in stale:
        VIDEO_GENERATION_JOBS.pop(job_id, None)


async def _run_video_generation_job(
    request: Request,
    job_id: str,
    form_data: CreateVideoForm,
    user,
) -> None:
    job = VIDEO_GENERATION_JOBS[job_id]
    job["status"] = "running"
    job["updated_at"] = time.time()
    try:
        regeneration_prompt = _resolve_regeneration_prompt(form_data, user)
        if regeneration_prompt:
            form_data.prompt = regeneration_prompt
        job["result"] = await video_generations(request, form_data, user=user)
        job["status"] = "completed"
    except Exception as exc:
        log.exception("Video generation job %s failed", job_id)
        job["status"] = "failed"
        job["error"] = (
            str(exc.detail)
            if isinstance(exc, HTTPException)
            else str(exc) or "Video generation failed"
        )
    finally:
        job["updated_at"] = time.time()


@router.post("/generations/jobs")
async def create_video_generation_job(
    request: Request,
    form_data: CreateVideoJobForm,
    user=Depends(get_verified_user),
):
    _check_video_access(request, user)
    _prune_video_jobs()
    job_id = str(form_data.job_id)
    existing = VIDEO_GENERATION_JOBS.get(job_id)
    if existing:
        if existing["user_id"] != user.id:
            raise HTTPException(status_code=404, detail="Video generation job not found")
        return _public_video_job(job_id, existing)

    now = time.time()
    VIDEO_GENERATION_JOBS[job_id] = {
        "user_id": user.id,
        "status": "queued",
        "created_at": now,
        "updated_at": now,
        "result": None,
        "error": None,
    }
    generation_form = CreateVideoForm.model_validate(
        form_data.model_dump(exclude={"job_id"})
    )
    task = asyncio.create_task(
        _run_video_generation_job(request, job_id, generation_form, user)
    )
    VIDEO_GENERATION_TASKS.add(task)
    task.add_done_callback(VIDEO_GENERATION_TASKS.discard)
    return _public_video_job(job_id, VIDEO_GENERATION_JOBS[job_id])


@router.get("/generations/jobs/{job_id}")
async def get_video_generation_job(
    request: Request,
    job_id: UUID,
    user=Depends(get_verified_user),
):
    _check_video_access(request, user)
    key = str(job_id)
    job = VIDEO_GENERATION_JOBS.get(key)
    if not job or job["user_id"] != user.id:
        raise HTTPException(status_code=404, detail="Video generation job not found")
    return _public_video_job(key, job)


@router.post("/generations")
async def generate_videos(
    request: Request,
    form_data: CreateVideoForm,
    user=Depends(get_verified_user),
):
    _check_video_access(request, user)
    regeneration_prompt = _resolve_regeneration_prompt(form_data, user)
    if regeneration_prompt:
        form_data.prompt = regeneration_prompt
    return await video_generations(request, form_data, user=user)
