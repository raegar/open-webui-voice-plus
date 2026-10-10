import asyncio
import base64
import binascii
import io
import logging
import mimetypes
import random
import time
from pathlib import Path
from typing import Literal, Optional
from uuid import UUID, uuid4

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
from open_webui.storage.provider import Storage
from open_webui.utils.access_control import has_permission
from open_webui.utils.auth import get_verified_user
from open_webui.utils.videos.comfyui import ComfyUIVideoClient


router = APIRouter()
log = logging.getLogger(__name__)
NUMBER_SAFE_INTEGER_MAX = 2**53 - 1
VIDEO_GENERATION_JOBS: dict[str, dict] = {}
VIDEO_GENERATION_TASKS: set[asyncio.Task] = set()
# There is one ComfyUI behind this, so generations run one at a time. Submissions
# wait here in order and a single worker drains them; without it a second submission
# raced the first into ComfyUI and usually died against the request timeout while it
# sat behind the other one in ComfyUI's own queue.
VIDEO_GENERATION_QUEUE: "asyncio.Queue[str]" = asyncio.Queue()
VIDEO_GENERATION_WORKER: Optional[asyncio.Task] = None
# Terminal states, which is also what makes a job eligible for pruning.
VIDEO_JOB_DONE_STATES = {"completed", "failed", "cancelled"}
VIDEO_JOB_ACTIVE_STATES = {"queued", "running"}


class ReferenceImageSource(BaseModel):
    """One reference slot: a file already on the server, or a fresh upload.

    Library pictures go by file id so a phone on a weak signal does not have to
    send back megabytes of images the server already holds.
    """

    file_id: Optional[str] = Field(default=None, max_length=64)
    data_url: Optional[str] = Field(default=None, max_length=36_000_000)


class CreateVideoForm(BaseModel):
    prompt: str
    mode: Literal["text", "reference"] = "text"
    first_frame_data_url: Optional[str] = Field(default=None, max_length=36_000_000)
    last_frame_data_url: Optional[str] = Field(default=None, max_length=36_000_000)
    reference_image_data_urls: list[str] = Field(default_factory=list, max_length=9)
    # Ordered like <Picture N>; takes the place of reference_image_data_urls.
    reference_images: list[ReferenceImageSource] = Field(
        default_factory=list, max_length=9
    )
    reference_audio_data_urls: list[str] = Field(default_factory=list, max_length=3)
    reference_audio_file_ids: list[str] = Field(default_factory=list, max_length=3)
    aspect_ratio: Literal["16:9", "9:16", "1:1"] = "9:16"
    megapixels: Literal[0.2, 0.4] = 0.2
    duration: Literal[3, 5, 10, 15] = 3
    # LoRA path (euler / 12 steps / shift 6 whenever either is on).
    # Turbo defaults on: it is the speed win and has tested clean.
    motion_lora: bool = False
    # Which file fills the motion slot when motion_lora is on.
    motion_lora_variant: Literal["hmmotion", "m3_unlocked"] = "hmmotion"
    # Optional look LoRA in its own slot; combines with motion and turbo.
    style_lora: Optional[Literal["flat_anime", "astro_realism"]] = None
    turbo_lora: bool = True
    seed: Optional[int] = Field(default=None, ge=0, le=2**63 - 1)
    # Filming style the brief was drafted with. The studio owns the catalogue; the
    # server only records it, so a chat can restore its style on another device.
    style: Optional[str] = Field(default=None, max_length=64)
    # Whose eyes a point-of-view style films from, recorded for the same reason.
    pov_subject: Optional[str] = Field(default=None, max_length=200)
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


IMAGE_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}


def _frame_from_bytes(
    image_data: bytes, content_type: str, role: str
) -> tuple[bytes, str, str]:
    if not image_data or len(image_data) > 25 * 1024 * 1024:
        raise HTTPException(
            status_code=400,
            detail=f"{role} must be no larger than 25 MB",
        )
    extension = mimetypes.guess_extension(content_type) or ".png"
    return image_data, f"{role.lower().replace(' ', '-')}{extension}", content_type


def _load_frame_data_url(value: str, role: str) -> tuple[bytes, str, str]:
    if len(value) > 36_000_000:
        raise HTTPException(
            status_code=400,
            detail=f"{role} must be no larger than 25 MB",
        )
    try:
        header, encoded = value.split(",", 1)
        content_type = header.removeprefix("data:").split(";", 1)[0].lower()
        if not header.endswith(";base64") or content_type not in IMAGE_CONTENT_TYPES:
            raise ValueError
        image_data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{role} must be a base64 PNG, JPEG, or WebP image",
        ) from exc
    return _frame_from_bytes(image_data, content_type, role)


def _owned_file(user, file_id: str, role: str):
    file_item = Files.get_file_by_id(file_id)
    if not file_item or file_item.user_id != user.id:
        raise HTTPException(status_code=400, detail=f"{role} could not be found")
    return file_item


def _read_owned_file(user, file_id: str, role: str) -> tuple[bytes, str]:
    """Bytes and content type of a file the caller uploaded earlier."""
    file_item = _owned_file(user, file_id, role)
    meta = file_item.meta if isinstance(file_item.meta, dict) else {}
    # By extension first, as the file content route serves it, which is what the
    # studio used to read and upload back.
    content_type = (
        mimetypes.guess_type(file_item.filename or "")[0]
        or meta.get("content_type")
        or ""
    ).split(";", 1)[0].strip().lower()
    try:
        data = Path(Storage.get_file(file_item.path)).read_bytes()
    except Exception as exc:
        log.exception("Could not read file %s for %s", file_id, role)
        raise HTTPException(
            status_code=400, detail=f"{role} could not be read"
        ) from exc
    return data, content_type


def _load_frame_file(user, file_id: str, role: str) -> tuple[bytes, str, str]:
    data, content_type = _read_owned_file(user, file_id, role)
    if content_type == "image/jpg":
        content_type = "image/jpeg"
    if content_type not in IMAGE_CONTENT_TYPES:
        raise HTTPException(
            status_code=400, detail=f"{role} must be a PNG, JPEG, or WebP image"
        )
    return _frame_from_bytes(data, content_type, role)


def _reference_image_sources(form_data: "CreateVideoForm") -> list[ReferenceImageSource]:
    """The ordered reference slots, from whichever field the caller used."""
    if form_data.reference_images and form_data.reference_image_data_urls:
        raise HTTPException(
            status_code=400,
            detail="Send reference_images or reference_image_data_urls, not both",
        )
    sources = form_data.reference_images or [
        ReferenceImageSource(data_url=value)
        for value in form_data.reference_image_data_urls
    ]
    for index, source in enumerate(sources):
        if bool(source.file_id) == bool(source.data_url):
            raise HTTPException(
                status_code=400,
                detail=f"Reference image {index + 1} needs a file id or an image, not both",
            )
    return sources


def _check_reference_files(form_data: "CreateVideoForm", user) -> None:
    """Fail at submission, not minutes later in the queue, on a bad file id."""
    for index, source in enumerate(_reference_image_sources(form_data)):
        if source.file_id:
            _owned_file(user, source.file_id, f"Reference image {index + 1}")
    if len(form_data.reference_audio_data_urls) + len(form_data.reference_audio_file_ids) > 3:
        raise HTTPException(status_code=400, detail="At most 3 voice references")
    for index, file_id in enumerate(form_data.reference_audio_file_ids):
        _owned_file(user, file_id, f"Voice reference {index + 1}")


AUDIO_CONTENT_TYPES = {
    "audio/wav",
    "audio/x-wav",
    "audio/mpeg",
    "audio/mp3",
    "audio/mp4",
    "audio/m4a",
    "audio/x-m4a",
    "audio/ogg",
    "audio/flac",
    "audio/webm",
}


def _load_audio_data_url(value: str, index: int) -> tuple[bytes, str, str]:
    """Validate a voice reference. LoadAudio reads by extension, so the filename
    must carry one that matches the declared content type."""
    if len(value) > 36_000_000:
        raise HTTPException(status_code=400, detail="Voice reference must be under 25 MB")
    try:
        header, encoded = value.split(",", 1)
        content_type = header.removeprefix("data:").split(";", 1)[0].lower()
        if not header.endswith(";base64") or content_type not in AUDIO_CONTENT_TYPES:
            raise ValueError
        audio_data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(
            status_code=400,
            detail="Voice reference must be a base64 WAV, MP3, M4A, OGG, FLAC, or WebM file",
        ) from exc
    return _audio_from_bytes(audio_data, content_type, index)


def _load_audio_file(user, file_id: str, index: int) -> tuple[bytes, str, str]:
    audio_data, content_type = _read_owned_file(user, file_id, f"Voice reference {index}")
    if content_type not in AUDIO_CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Voice reference must be a WAV, MP3, M4A, OGG, FLAC, or WebM file",
        )
    return _audio_from_bytes(audio_data, content_type, index)


def _audio_from_bytes(
    audio_data: bytes, content_type: str, index: int
) -> tuple[bytes, str, str]:
    if not audio_data or len(audio_data) > 25 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Voice reference must be under 25 MB")
    extension = mimetypes.guess_extension(content_type) or ".wav"
    if content_type in {"audio/mpeg", "audio/mp3"}:
        extension = ".mp3"
    elif content_type in {"audio/m4a", "audio/x-m4a", "audio/mp4"}:
        extension = ".m4a"
    return audio_data, f"voice-{index}{extension}", content_type


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

def _get_video_history_items(
    request: Request, user, limit: int, chat_id: Optional[str] = None
) -> list[dict]:
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
        if chat_id is not None and generation.get("chat_id") != chat_id:
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
                "style": generation.get("style") or "default",
                "pov_subject": generation.get("pov_subject") or "",
                # Absent for clips made before render time was recorded.
                "generation_seconds": generation.get("generation_seconds"),
                "chat_id": generation.get("chat_id"),
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
    # Only videos generated for this chat, so a chat can find its own latest seed.
    chat_id: Optional[str] = Query(default=None),
    user=Depends(get_verified_user),
):
    _check_video_access(request, user)
    return _get_video_history_items(request, user, limit, chat_id)


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
    user=Depends(get_verified_user),
):
    """The caller's whole character library."""
    _check_video_access(request, user)
    return VideoCharacters.get_library(user.id)


@router.get("/characters/chat/{chat_id}")
async def list_chat_video_characters(
    request: Request,
    chat_id: str,
    user=Depends(get_verified_user),
):
    """Only the characters attached to this chat, in attachment order."""
    _check_video_access(request, user)
    return VideoCharacters.get_for_chat(user.id, chat_id)


def _verify_applies_to(user, applies_to_id: str) -> None:
    """An outfit may only reference a character the caller owns."""
    if not applies_to_id:
        return
    target = VideoCharacters.get_by_id(user.id, applies_to_id)
    if not target:
        raise HTTPException(status_code=400, detail="Unknown character for this outfit")


@router.post("/characters")
async def create_video_character(
    request: Request,
    form_data: VideoCharacterForm,
    user=Depends(get_verified_user),
):
    _check_video_access(request, user)
    _verify_owns_files(user, form_data.image_file_ids)
    if form_data.voice_file_id:
        _verify_owns_files(user, [form_data.voice_file_id])
    _verify_applies_to(user, form_data.applies_to_id)
    return VideoCharacters.insert(user.id, form_data)


@router.post("/characters/chat/{chat_id}/attach/{character_id}")
async def attach_video_character(
    request: Request,
    chat_id: str,
    character_id: str,
    user=Depends(get_verified_user),
):
    _check_video_access(request, user)
    target = VideoCharacters.get_by_id(user.id, character_id)
    if target and target.kind == "outfit":
        raise HTTPException(
            status_code=400, detail="Outfits are worn by a character, not attached alone"
        )
    if not VideoCharacters.attach(user.id, chat_id, character_id):
        raise HTTPException(status_code=404, detail="Character not found")
    return {"attached": True}


class VideoCharacterStateForm(BaseModel):
    state: str = Field(default="", max_length=2000)


@router.post("/characters/chat/{chat_id}/state/{character_id}")
async def set_video_character_state(
    request: Request,
    chat_id: str,
    character_id: str,
    form_data: VideoCharacterStateForm,
    user=Depends(get_verified_user),
):
    """Record what an attached character is currently wearing in this chat."""
    _check_video_access(request, user)
    if not VideoCharacters.set_state(user.id, chat_id, character_id, form_data.state):
        raise HTTPException(status_code=404, detail="Character is not attached to this chat")
    return {"updated": True}


class VideoCharacterOutfitForm(BaseModel):
    outfit_id: str = ""


@router.post("/characters/chat/{chat_id}/outfit/{character_id}")
async def set_video_character_outfit(
    request: Request,
    chat_id: str,
    character_id: str,
    form_data: VideoCharacterOutfitForm,
    user=Depends(get_verified_user),
):
    """Dress an attached character in one of the caller's outfits, or clear it."""
    _check_video_access(request, user)
    if form_data.outfit_id:
        outfit = VideoCharacters.get_by_id(user.id, form_data.outfit_id)
        if not outfit or outfit.kind != "outfit":
            raise HTTPException(status_code=400, detail="Unknown outfit")
    if not VideoCharacters.set_outfit(user.id, chat_id, character_id, form_data.outfit_id):
        raise HTTPException(status_code=404, detail="Character is not attached to this chat")
    return {"updated": True}


@router.delete("/characters/chat/{chat_id}/attach/{character_id}")
async def detach_video_character(
    request: Request,
    chat_id: str,
    character_id: str,
    user=Depends(get_verified_user),
):
    _check_video_access(request, user)
    VideoCharacters.detach(user.id, chat_id, character_id)
    return {"detached": True}


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
    if form_data.voice_file_id:
        _verify_owns_files(user, [form_data.voice_file_id])
    if form_data.applies_to_id:
        if form_data.applies_to_id == character_id:
            raise HTTPException(status_code=400, detail="An outfit cannot apply to itself")
        _verify_applies_to(user, form_data.applies_to_id)
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
    reference_sources = _reference_image_sources(form_data)
    _check_reference_files(form_data, user)
    if form_data.mode == "reference" and (
        not reference_sources
        or form_data.first_frame_data_url
        or form_data.last_frame_data_url
    ):
        raise HTTPException(
            status_code=400,
            detail="Reference mode requires 1-9 reference images and cannot use frame anchors",
        )
    if form_data.mode != "reference" and reference_sources:
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
        (
            _load_frame_file(user, source.file_id, f"Reference image {index + 1}")
            if source.file_id
            else _load_frame_data_url(source.data_url, f"Reference image {index + 1}")
        )
        for index, source in enumerate(reference_sources)
    ]
    reference_audios = [
        _load_audio_data_url(value, index + 1)
        for index, value in enumerate(form_data.reference_audio_data_urls)
    ]
    reference_audios += [
        _load_audio_file(user, file_id, len(reference_audios) + index + 1)
        for index, file_id in enumerate(form_data.reference_audio_file_ids)
    ]
    client = ComfyUIVideoClient(
        request.app.state.config.COMFYUI_VIDEO_BASE_URL,
        request.app.state.config.COMFYUI_VIDEO_API_KEY,
        request.app.state.config.COMFYUI_VIDEO_TIMEOUT,
    )
    # Render time only: the job queue wait happens before this function is called.
    render_started = time.monotonic()
    video_data, filename, content_type = await client.generate(
        form_data.prompt,
        form_data.aspect_ratio,
        form_data.megapixels,
        form_data.duration,
        seed,
        first_frame,
        last_frame,
        reference_images,
        reference_audios,
        form_data.motion_lora,
        form_data.turbo_lora,
        motion_lora_variant=form_data.motion_lora_variant,
        style_lora=form_data.style_lora,
    )
    generation_seconds = round(time.monotonic() - render_started, 1)
    generation_metadata = {
        **form_data.model_dump(
            exclude={
                "first_frame_data_url",
                "last_frame_data_url",
                "reference_image_data_urls",
                "reference_images",
                "reference_audio_data_urls",
                "reference_audio_file_ids",
            },
            exclude_none=True,
        ),
        **metadata,
        "seed": seed,
        "content_type": content_type,
        "has_first_frame": first_frame is not None,
        "has_last_frame": last_frame is not None,
        "reference_image_count": len(reference_images),
        "reference_audio_count": len(reference_audios),
        "motion_lora": form_data.motion_lora,
        "motion_lora_variant": form_data.motion_lora_variant,
        "turbo_lora": form_data.turbo_lora,
        "style_lora": form_data.style_lora,
        "generation_seconds": generation_seconds,
    }
    file_item, url = _upload_video(
        request,
        video_data,
        filename,
        content_type,
        generation_metadata,
        user,
    )
    return [
        {
            # The same identity fields a history item carries, so a clip archived
            # straight from a finished job can be deleted and dated without a refresh.
            "id": file_item.id,
            "created_at": file_item.created_at,
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
            "style": form_data.style or "default",
            "pov_subject": form_data.pov_subject or "",
            "generation_seconds": generation_seconds,
        }
    ]


def _jobs_ahead(job_id: str) -> Optional[int]:
    """How many generations are in front of this one.

    0 means it is the one running, or next to start. None once it is finished.
    Insertion order is submission order, which is the order the worker takes them.
    """
    job = VIDEO_GENERATION_JOBS.get(job_id)
    if not job or job["status"] not in VIDEO_JOB_ACTIVE_STATES:
        return None
    ahead = 0
    for other_id, other in VIDEO_GENERATION_JOBS.items():
        if other_id == job_id:
            break
        if other["status"] in VIDEO_JOB_ACTIVE_STATES:
            ahead += 1
    return ahead


def _public_video_job(job_id: str, job: dict) -> dict:
    return {
        "job_id": job_id,
        "status": job["status"],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
        "result": job.get("result"),
        "error": job.get("error"),
        "position": _jobs_ahead(job_id),
        # When it actually started rendering, so elapsed time excludes the wait.
        "started_at": job.get("started_at"),
        "label": job.get("label"),
    }


def _prune_video_jobs() -> None:
    cutoff = time.time() - 24 * 60 * 60
    stale = [
        job_id
        for job_id, job in VIDEO_GENERATION_JOBS.items()
        if job["status"] in VIDEO_JOB_DONE_STATES and job["updated_at"] < cutoff
    ]
    for job_id in stale:
        VIDEO_GENERATION_JOBS.pop(job_id, None)


def _release_job_inputs(job: dict) -> None:
    """Drop the submitted payload once it can no longer be needed.

    A queued job holds its own reference images as data URLs, which run to tens of
    megabytes each, so a finished or cancelled job must not keep them alive.
    """
    job.pop("request", None)
    job.pop("form_data", None)
    job.pop("user", None)
    job.pop("runner", None)


async def _run_video_generation_job(job_id: str) -> None:
    job = VIDEO_GENERATION_JOBS[job_id]
    job["status"] = "running"
    job["started_at"] = time.time()
    job["updated_at"] = time.time()
    try:
        # Other ComfyUI work (Game Master portraits) shares this queue so it never
        # lands on the GPU alongside a video; it brings its own runner.
        if job.get("runner"):
            job["result"] = await job["runner"]()
            job["status"] = "completed"
            return
        request = job["request"]
        form_data = job["form_data"]
        user = job["user"]
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
        _release_job_inputs(job)


async def _video_generation_worker() -> None:
    while True:
        job_id = await VIDEO_GENERATION_QUEUE.get()
        try:
            job = VIDEO_GENERATION_JOBS.get(job_id)
            # Cancelled while it waited, or already pruned. Nothing left to run.
            if not job or job["status"] != "queued":
                continue
            await _run_video_generation_job(job_id)
        except Exception:
            # A crash here would drain no further jobs, so the loop swallows it.
            log.exception("Video generation worker failed on job %s", job_id)
        finally:
            VIDEO_GENERATION_QUEUE.task_done()


def _ensure_video_generation_worker() -> None:
    """Start the drain loop on first use, and restart it if it ever died.

    Started here rather than from a lifespan hook because main.py is assembled by
    sed at image build time, where another patch is a good deal more fragile.
    """
    global VIDEO_GENERATION_WORKER
    if VIDEO_GENERATION_WORKER is None or VIDEO_GENERATION_WORKER.done():
        VIDEO_GENERATION_WORKER = asyncio.create_task(_video_generation_worker())
        VIDEO_GENERATION_TASKS.add(VIDEO_GENERATION_WORKER)
        VIDEO_GENERATION_WORKER.add_done_callback(VIDEO_GENERATION_TASKS.discard)


async def enqueue_comfyui_job(user_id: str, kind: str, label: str, runner) -> str:
    """Queue non-video ComfyUI work behind any videos already waiting.

    runner is an async callable taking no arguments. These jobs never appear in the
    studio's job list: it adopts every listed job as a video it should show.
    """
    _prune_video_jobs()
    job_id = str(uuid4())
    now = time.time()
    VIDEO_GENERATION_JOBS[job_id] = {
        "user_id": user_id,
        "kind": kind,
        "status": "queued",
        "created_at": now,
        "updated_at": now,
        "result": None,
        "error": None,
        "label": label[:120],
        "runner": runner,
    }
    _ensure_video_generation_worker()
    await VIDEO_GENERATION_QUEUE.put(job_id)
    return job_id


@router.get("/generations/jobs")
async def list_video_generation_jobs(
    request: Request,
    user=Depends(get_verified_user),
):
    """Everything of the caller's that is still waiting or running, in queue order."""
    _check_video_access(request, user)
    _prune_video_jobs()
    return [
        _public_video_job(job_id, job)
        for job_id, job in VIDEO_GENERATION_JOBS.items()
        if job["user_id"] == user.id
        and job["status"] in VIDEO_JOB_ACTIVE_STATES
        and job.get("kind", "video") == "video"
    ]


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
    generation_form = CreateVideoForm.model_validate(
        form_data.model_dump(exclude={"job_id"})
    )
    _check_reference_files(generation_form, user)
    VIDEO_GENERATION_JOBS[job_id] = {
        "user_id": user.id,
        "status": "queued",
        "created_at": now,
        "updated_at": now,
        "result": None,
        "error": None,
        # Enough of the brief to tell one queued item from another in the UI.
        "label": generation_form.prompt.strip()[:120],
        "request": request,
        "form_data": generation_form,
        "user": user,
    }
    _ensure_video_generation_worker()
    await VIDEO_GENERATION_QUEUE.put(job_id)
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


@router.delete("/generations/jobs/{job_id}")
async def cancel_video_generation_job(
    request: Request,
    job_id: UUID,
    user=Depends(get_verified_user),
):
    """Drop a generation that has not started yet.

    A running one is left alone: it is already inside ComfyUI, and interrupting it
    there is not something this endpoint can honestly promise.
    """
    _check_video_access(request, user)
    key = str(job_id)
    job = VIDEO_GENERATION_JOBS.get(key)
    if not job or job["user_id"] != user.id:
        raise HTTPException(status_code=404, detail="Video generation job not found")
    if job["status"] != "queued":
        raise HTTPException(
            status_code=409,
            detail="Only a generation that is still waiting can be cancelled.",
        )
    # The worker skips anything no longer queued, so the entry can stay on the
    # asyncio queue rather than being fished out of the middle of it.
    job["status"] = "cancelled"
    job["updated_at"] = time.time()
    _release_job_inputs(job)
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
