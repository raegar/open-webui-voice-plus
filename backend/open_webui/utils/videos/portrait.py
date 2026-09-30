"""NPC portraits: one realistic still from Z-Image-Turbo, as a character reference.

The Game Master gives each NPC a fixed written look. A portrait pins that look down
as a picture, so an NPC can go to Video Studio as a reference the way a library
character does.

Z-Image-Turbo is small (about 12 GB with its text encoder) but the MiniMax H3 models
are about 42 GB. Left to itself ComfyUI would page H3 out to system RAM to make room,
which is the memory pressure its known crashes come from, so a portrait clears
ComfyUI's memory before it starts and again when it is done. Portraits run through
the video job queue, so one never shares the GPU with a video.

Settings are ComfyUI's own image_z_image_turbo_int8 template: 8 steps, CFG 1,
res_multistep / simple, AuraFlow shift 3, text encoder type lumina2. Measured
2026-09-30: 5.8 s including model load, peak 12.8 GB VRAM.
"""

import logging
import mimetypes
import time
from pathlib import PurePosixPath
from typing import Optional
from urllib.parse import urlencode
from uuid import uuid4

from open_webui.utils.videos.comfyui import ComfyUIVideoClient, ComfyUIVideoError, _execution_error

log = logging.getLogger(__name__)

PORTRAIT_UNET = "z_image_turbo_int8_convrot.safetensors"
PORTRAIT_TEXT_ENCODER = "qwen_3_4b_fp8_mixed.safetensors"
PORTRAIT_VAE = "z_image_ae.safetensors"
PORTRAIT_WIDTH = 832
PORTRAIT_HEIGHT = 1216
PORTRAIT_STEPS = 8
PORTRAIT_TIMEOUT = 300
SAVE_NODE = "9"
MAX_LOOK_CHARS = 1200


def build_portrait_prompt(look: str) -> str:
    """A reference-friendly photograph of the look: framed, lit and neutral.

    No name: an image model can do nothing with one. No style: the video's own style
    settings decide how a clip is rendered, and a styled reference would fight them.
    """
    look = " ".join(look.split())[:MAX_LOOK_CHARS].rstrip(".")
    return (
        f"Realistic photograph, three-quarter length portrait from head to mid-thigh of "
        f"{look}. They stand still and face the camera with a natural, composed "
        "expression, both hands visible. Even soft studio lighting, plain light grey "
        "background, sharp focus, natural skin texture, 50mm lens."
    )


def build_portrait_workflow(prompt: str, seed: int) -> dict:
    return {
        "28": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": PORTRAIT_UNET, "weight_dtype": "default"},
        },
        "30": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": PORTRAIT_TEXT_ENCODER,
                "type": "lumina2",
                "device": "default",
            },
        },
        "29": {"class_type": "VAELoader", "inputs": {"vae_name": PORTRAIT_VAE}},
        "11": {
            "class_type": "ModelSamplingAuraFlow",
            "inputs": {"model": ["28", 0], "shift": 3},
        },
        "27": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["30", 0], "text": prompt}},
        "33": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["27", 0]}},
        "13": {
            "class_type": "EmptySD3LatentImage",
            "inputs": {"width": PORTRAIT_WIDTH, "height": PORTRAIT_HEIGHT, "batch_size": 1},
        },
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["11", 0],
                "positive": ["27", 0],
                "negative": ["33", 0],
                "latent_image": ["13", 0],
                "seed": seed,
                "steps": PORTRAIT_STEPS,
                "cfg": 1,
                "sampler_name": "res_multistep",
                "scheduler": "simple",
                "denoise": 1,
            },
        },
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["29", 0]}},
        SAVE_NODE: {
            "class_type": "SaveImage",
            "inputs": {"images": ["8", 0], "filename_prefix": "owui-gm-portrait"},
        },
    }


def find_image_output(history_entry: dict) -> Optional[dict]:
    for item in (history_entry.get("outputs", {}).get(SAVE_NODE) or {}).get("images", []):
        suffix = PurePosixPath(item.get("filename", "")).suffix.lower()
        if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
            return item
    return None


async def free_comfyui_memory(client: ComfyUIVideoClient) -> None:
    """Unload every model and drop cached outputs. Best effort: never raises."""
    try:
        response = await client._request(
            "POST", "/free", json={"unload_models": True, "free_memory": True}
        )
        response.raise_for_status()
    except Exception:
        log.warning("Could not free ComfyUI memory around a portrait", exc_info=True)


async def generate_portrait(
    client: ComfyUIVideoClient, look: str, seed: int
) -> tuple[bytes, str, str]:
    """Render one portrait. Returns (image bytes, filename, content type)."""
    workflow = build_portrait_workflow(build_portrait_prompt(look), seed)
    await free_comfyui_memory(client)
    try:
        response = await client._request(
            "POST", "/prompt", json={"prompt": workflow, "client_id": str(uuid4())}
        )
        response.raise_for_status()
        prompt_id = response.json().get("prompt_id")
        if not prompt_id:
            raise ComfyUIVideoError(f"ComfyUI rejected the portrait workflow: {response.text[:300]}")

        deadline = time.monotonic() + PORTRAIT_TIMEOUT
        output = None
        while time.monotonic() < deadline:
            response = await client._request("GET", f"/history/{prompt_id}")
            response.raise_for_status()
            entry = response.json().get(prompt_id)
            if entry:
                error = _execution_error(entry)
                if error:
                    raise ComfyUIVideoError(error)
                output = find_image_output(entry)
                if output:
                    break
            await client.sleep(1.0)
        if not output:
            raise ComfyUIVideoError(
                f"The portrait did not finish within {PORTRAIT_TIMEOUT} seconds"
            )

        query = urlencode(
            {
                "filename": output["filename"],
                "subfolder": output.get("subfolder", ""),
                "type": output.get("type", "output"),
            }
        )
        response = await client._request("GET", f"/view?{query}")
        response.raise_for_status()
        content_type = (
            response.headers.get("content-type")
            or mimetypes.guess_type(output["filename"])[0]
            or "image/png"
        )
        return response.content, output["filename"], content_type
    finally:
        await free_comfyui_memory(client)
