import asyncio
import mimetypes
import time
from pathlib import Path, PurePosixPath
from typing import Awaitable, Callable, Optional, Sequence
from urllib.parse import urlencode
from uuid import uuid4

import requests


class ComfyUIVideoError(RuntimeError):
    pass


# MiniMaxH3ReferenceToVideo caps standalone ref_audios at 3 (see its Autogrow template).
MAX_REFERENCE_AUDIOS = 3

# Optional acceleration/motion path, off unless a request asks for it. The base
# graph is left exactly as it was so turning the toggle off reproduces the known
# good 20-step res_multistep result without a rebuild.
MOTION_LORA_NAME = "hmmotion_minimax-h3_epoch12.safetensors"
TURBO_LORA_NAME = "minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors"
# Settings recommended alongside the hmmotion LoRA.
MOTION_LORA_STRENGTH = 1.0
TURBO_LORA_STRENGTH = 0.5
MOTION_SAMPLER = "euler"
MOTION_STEPS = 12
MOTION_SHIFT_VIDEO = 6.0
# Not part of the recommendation; this is MiniMaxH3SigmaShift's own default.
MOTION_SHIFT_AUDIO = 3.0
# Node ids for the inserted chain, chosen well clear of the graph's own ids.
_LORA_MOTION_NODE = "901"
_LORA_TURBO_NODE = "902"
_SHIFT_NODE = "903"


def apply_motion_loras(
    workflow: dict,
    unet_node_id: str,
    model_consumer_ids: "list[str]",
    sampler_node_id: str,
    scheduler_node_id: str,
) -> dict:
    """Splice hmmotion + turbo LoRAs and a sigma shift between the UNet and the
    nodes that consume it, then switch the sampler to what those LoRAs expect.

    Both LoRAs are model-only, so the CLIP path is untouched.
    """
    workflow[_LORA_MOTION_NODE] = {
        "inputs": {
            "model": [unet_node_id, 0],
            "lora_name": MOTION_LORA_NAME,
            "strength_model": MOTION_LORA_STRENGTH,
        },
        "class_type": "LoraLoaderModelOnly",
        "_meta": {"title": "Load hmmotion LoRA"},
    }
    workflow[_LORA_TURBO_NODE] = {
        "inputs": {
            "model": [_LORA_MOTION_NODE, 0],
            "lora_name": TURBO_LORA_NAME,
            "strength_model": TURBO_LORA_STRENGTH,
        },
        "class_type": "LoraLoaderModelOnly",
        "_meta": {"title": "Load turbo LoRA"},
    }
    workflow[_SHIFT_NODE] = {
        "inputs": {
            "model": [_LORA_TURBO_NODE, 0],
            "shift_video": MOTION_SHIFT_VIDEO,
            "shift_audio": MOTION_SHIFT_AUDIO,
        },
        "class_type": "MiniMaxH3SigmaShift",
        "_meta": {"title": "MiniMax H3 Sigma Shift"},
    }
    for node_id in model_consumer_ids:
        workflow[node_id]["inputs"]["model"] = [_SHIFT_NODE, 0]
    workflow[sampler_node_id]["inputs"]["sampler_name"] = MOTION_SAMPLER
    workflow[scheduler_node_id]["inputs"]["steps"] = MOTION_STEPS
    return workflow

RESOLUTIONS = {
    ("16:9", 0.2): (608, 352),
    ("16:9", 0.4): (864, 480),
    ("9:16", 0.2): (352, 608),
    ("9:16", 0.4): (480, 864),
    ("1:1", 0.2): (448, 448),
    ("1:1", 0.4): (640, 640),
}


def build_minimax_h3_workflow(
    prompt: str,
    aspect_ratio: str,
    megapixels: float,
    duration: int,
    seed: int,
    first_frame_name: Optional[str] = None,
    last_frame_name: Optional[str] = None,
    motion_loras: bool = False,
) -> dict:
    try:
        width, height = RESOLUTIONS[(aspect_ratio, megapixels)]
    except KeyError as exc:
        raise ValueError("Unsupported video resolution") from exc

    workflow = {
        "105:6": {
            "inputs": {
                "unet_name": "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
                "weight_dtype": "default",
            },
            "class_type": "UNETLoader",
            "_meta": {"title": "Load Diffusion Model"},
        },
        "105:13": {
            "inputs": {
                "clip_name": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
                "type": "minimax",
                "device": "default",
            },
            "class_type": "CLIPLoader",
            "_meta": {"title": "Load CLIP"},
        },
        "105:111": {
            "inputs": {"value": float(duration)},
            "class_type": "PrimitiveFloat",
            "_meta": {"title": "Float (duration)"},
        },
        "105:107": {
            "inputs": {
                "expression": "max(5, round(a * 24)) + (5 - (max(5, round(a * 24)) % 17)) % 17",
                "values.a": ["105:111", 0],
            },
            "class_type": "ComfyMathExpression",
            "_meta": {"title": "Math Expression"},
        },
        "105:104": {
            "inputs": {
                "prompt": prompt,
                "width": width,
                "height": height,
                "length": ["105:107", 1],
                "clip": ["105:13", 0],
                "vae": ["105:11", 0],
            },
            "class_type": "MiniMaxH3ImageToVideo",
            "_meta": {"title": "MiniMax H3 Image to Video"},
        },
        "105:16": {
            "inputs": {"model": ["105:6", 0], "conditioning": ["105:104", 0]},
            "class_type": "BasicGuider",
            "_meta": {"title": "Basic Guider"},
        },
        "105:15": {
            "inputs": {"noise_seed": seed},
            "class_type": "RandomNoise",
            "_meta": {"title": "RandomNoise"},
        },
        "105:9": {
            "inputs": {
                "scheduler": "simple",
                "steps": 20,
                "denoise": 1.0,
                "model": ["105:6", 0],
            },
            "class_type": "BasicScheduler",
            "_meta": {"title": "BasicScheduler"},
        },
        "105:17": {
            "inputs": {"sampler_name": "res_multistep"},
            "class_type": "KSamplerSelect",
            "_meta": {"title": "KSamplerSelect"},
        },
        "105:14": {
            "inputs": {
                "noise": ["105:15", 0],
                "guider": ["105:16", 0],
                "sampler": ["105:17", 0],
                "sigmas": ["105:9", 0],
                "latent_image": ["105:104", 1],
            },
            "class_type": "SamplerCustomAdvanced",
            "_meta": {"title": "SamplerCustomAdvanced"},
        },
        "105:11": {
            "inputs": {"vae_name": "minimax_h3_video_vae_fp16.safetensors"},
            "class_type": "VAELoader",
            "_meta": {"title": "Load VAE"},
        },
        "105:24": {
            "inputs": {"vae_name": "minimax_h3_audio_vae_fp32.safetensors"},
            "class_type": "VAELoader",
            "_meta": {"title": "Load VAE"},
        },
        "105:10": {
            "inputs": {"samples": ["105:14", 0], "vae": ["105:11", 0]},
            "class_type": "VAEDecode",
            "_meta": {"title": "VAE Decode"},
        },
        "105:23": {
            "inputs": {"samples": ["105:14", 0], "vae": ["105:24", 0]},
            "class_type": "VAEDecodeAudio",
            "_meta": {"title": "VAE Decode Audio"},
        },
        "105:91": {
            "inputs": {
                "fps": 24.0,
                "bit_depth": 8,
                "images": ["105:10", 0],
                "audio": ["105:23", 0],
            },
            "class_type": "CreateVideo",
            "_meta": {"title": "Create Video"},
        },
        "92": {
            "inputs": {
                "filename_prefix": "video/MiniMax_H3",
                "format": "auto",
                "codec": "auto",
                "video-preview": "",
                "video": ["105:91", 0],
            },
            "class_type": "SaveVideo",
            "_meta": {"title": "Save Video"},
        },
    }

    if first_frame_name:
        workflow["114"] = {
            "inputs": {"image": first_frame_name},
            "class_type": "LoadImage",
            "_meta": {"title": "Load First Frame"},
        }
        workflow["105:104"]["inputs"]["first_frame"] = ["114", 0]

    if last_frame_name:
        workflow["115"] = {
            "inputs": {"image": last_frame_name},
            "class_type": "LoadImage",
            "_meta": {"title": "Load Last Frame"},
        }
        workflow["105:104"]["inputs"]["last_frame"] = ["115", 0]

    if motion_loras:
        apply_motion_loras(workflow, "105:6", ["105:16", "105:9"], "105:17", "105:9")

    return workflow


def build_minimax_h3_reference_workflow(
    prompt: str,
    aspect_ratio: str,
    megapixels: float,
    duration: int,
    seed: int,
    reference_image_names: Sequence[str],
    reference_audio_names: Sequence[str] = (),
    motion_loras: bool = False,
) -> dict:
    """Build the official MiniMax H3 Ref2VA image-reference graph."""
    if not 1 <= len(reference_image_names) <= 9:
        raise ValueError("Reference-to-Video requires between 1 and 9 images")
    if len(reference_audio_names) > MAX_REFERENCE_AUDIOS:
        raise ValueError(
            f"Reference-to-Video accepts at most {MAX_REFERENCE_AUDIOS} voice references"
        )
    try:
        width, height = RESOLUTIONS[(aspect_ratio, megapixels)]
    except KeyError as exc:
        raise ValueError("Unsupported video resolution") from exc

    workflow = {
        "127": {
            "inputs": {
                "unet_name": "minimax_h3_ref2va_pruned_int8_convrot.safetensors",
                "weight_dtype": "default",
            },
            "class_type": "UNETLoader",
            "_meta": {"title": "Load Ref2VA Diffusion Model"},
        },
        "128": {
            "inputs": {
                "clip_name": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
                "type": "minimax",
                "device": "default",
            },
            "class_type": "CLIPLoader",
            "_meta": {"title": "Load CLIP"},
        },
        "132": {
            "inputs": {"value": float(duration)},
            "class_type": "PrimitiveFloat",
            "_meta": {"title": "Float (duration)"},
        },
        "131": {
            "inputs": {
                "expression": "max(5, round(a * 24)) + (5 - (max(5, round(a * 24)) % 17)) % 17",
                "values.a": ["132", 0],
            },
            "class_type": "ComfyMathExpression",
            "_meta": {"title": "Math Expression"},
        },
        "119": {
            "inputs": {"vae_name": "minimax_h3_video_vae_fp16.safetensors"},
            "class_type": "VAELoader",
            "_meta": {"title": "Load Video VAE"},
        },
        "120": {
            "inputs": {"vae_name": "minimax_h3_audio_vae_fp32.safetensors"},
            "class_type": "VAELoader",
            "_meta": {"title": "Load Audio VAE"},
        },
        "136": {
            "inputs": {
                "prompt": prompt,
                "width": width,
                "height": height,
                "length": ["131", 1],
                "ref_image_size": "match",
                "clip": ["128", 0],
                "vae": ["119", 0],
                "audio_vae": ["120", 0],
            },
            "class_type": "MiniMaxH3ReferenceToVideo",
            "_meta": {"title": "MiniMax H3 Reference to Video"},
        },
        "129": {
            "inputs": {"noise_seed": seed},
            "class_type": "RandomNoise",
            "_meta": {"title": "RandomNoise"},
        },
        "123": {
            "inputs": {"sampler_name": "res_multistep"},
            "class_type": "KSamplerSelect",
            "_meta": {"title": "KSamplerSelect"},
        },
        "124": {
            "inputs": {
                "scheduler": "simple",
                "steps": 20,
                "denoise": 1.0,
                "model": ["127", 0],
            },
            "class_type": "BasicScheduler",
            "_meta": {"title": "BasicScheduler"},
        },
        "126": {
            "inputs": {"model": ["127", 0], "conditioning": ["136", 0]},
            "class_type": "BasicGuider",
            "_meta": {"title": "Basic Guider"},
        },
        "125": {
            "inputs": {
                "noise": ["129", 0],
                "guider": ["126", 0],
                "sampler": ["123", 0],
                "sigmas": ["124", 0],
                "latent_image": ["136", 1],
            },
            "class_type": "SamplerCustomAdvanced",
            "_meta": {"title": "SamplerCustomAdvanced"},
        },
        "122": {
            "inputs": {"samples": ["125", 0], "vae": ["119", 0]},
            "class_type": "VAEDecode",
            "_meta": {"title": "VAE Decode"},
        },
        "121": {
            "inputs": {"samples": ["125", 0], "vae": ["120", 0]},
            "class_type": "VAEDecodeAudio",
            "_meta": {"title": "VAE Decode Audio"},
        },
        "130": {
            "inputs": {
                "fps": 24.0,
                "bit_depth": 8,
                "images": ["122", 0],
                "audio": ["121", 0],
            },
            "class_type": "CreateVideo",
            "_meta": {"title": "Create Video"},
        },
        "92": {
            "inputs": {
                "filename_prefix": "video/MiniMax_H3_Ref2VA",
                "format": "auto",
                "codec": "auto",
                "video-preview": "",
                "video": ["130", 0],
            },
            "class_type": "SaveVideo",
            "_meta": {"title": "Save Video"},
        },
    }

    for index, image_name in enumerate(reference_image_names):
        node_id = f"ref-image-{index}"
        workflow[node_id] = {
            "inputs": {"image": image_name},
            "class_type": "LoadImage",
            "_meta": {"title": f"Load Reference Image {index + 1}"},
        }
        workflow["136"]["inputs"][f"ref_images.ref_image_{index}"] = [node_id, 0]

    # Voice references ride the sibling autogrow input. The key prefix comes from
    # the node's own TemplatePrefix (prefix="ref_audio_"); a wrong key is silently
    # ignored by ComfyUI rather than erroring, so it has to match exactly.
    for index, audio_name in enumerate(reference_audio_names):
        node_id = f"20{index}"
        workflow[node_id] = {
            "inputs": {"audio": audio_name},
            "class_type": "LoadAudio",
            "_meta": {"title": f"Load Reference Audio {index + 1}"},
        }
        workflow["136"]["inputs"][f"ref_audios.ref_audio_{index}"] = [node_id, 0]

    if motion_loras:
        apply_motion_loras(workflow, "127", ["126", "124"], "123", "124")

    return workflow


def find_video_output(history_entry: dict) -> Optional[dict]:
    for node_output in history_entry.get("outputs", {}).values():
        if not isinstance(node_output, dict):
            continue
        for items in node_output.values():
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                suffix = PurePosixPath(item.get("filename", "")).suffix.lower()
                if suffix in {".mp4", ".webm", ".mov", ".mkv"}:
                    return item
    return None


def _execution_error(history_entry: dict) -> Optional[str]:
    status = history_entry.get("status", {})
    for message in reversed(status.get("messages", [])):
        if isinstance(message, list) and len(message) > 1 and message[0] == "execution_error":
            details = message[1] if isinstance(message[1], dict) else {}
            return details.get("exception_message") or details.get("node_type") or "ComfyUI execution failed"
    if status.get("completed") and status.get("status_str") not in (None, "success"):
        return f"ComfyUI execution finished with status {status.get('status_str')}"
    return None


class ComfyUIVideoClient:
    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        timeout: int = 600,
        poll_interval: float = 2.0,
        session: Optional[requests.Session] = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.session = session or requests.Session()
        self.sleep = sleep
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    async def _request(self, method: str, path: str, **kwargs):
        return await asyncio.to_thread(
            self.session.request,
            method,
            f"{self.base_url}{path}",
            headers=self.headers,
            timeout=30,
            **kwargs,
        )

    async def upload_image(
        self, image_data: bytes, filename: str, content_type: str
    ) -> str:
        safe_name = Path(filename).name
        upload_name = f"owui-video-{uuid4().hex}-{safe_name}"
        response = await self._request(
            "POST",
            "/upload/image",
            files={"image": (upload_name, image_data, content_type)},
            data={"type": "input", "overwrite": "false"},
        )
        response.raise_for_status()
        name = response.json().get("name")
        if not name:
            raise ComfyUIVideoError("ComfyUI did not return an uploaded image name")
        return name

    async def generate(
        self,
        prompt: str,
        aspect_ratio: str,
        megapixels: float,
        duration: int,
        seed: int,
        first_frame: Optional[tuple[bytes, str, str]] = None,
        last_frame: Optional[tuple[bytes, str, str]] = None,
        reference_images: Optional[Sequence[tuple[bytes, str, str]]] = None,
        reference_audios: Optional[Sequence[tuple[bytes, str, str]]] = None,
        motion_loras: bool = False,
    ) -> tuple[bytes, str, str]:
        first_frame_name = await self.upload_image(*first_frame) if first_frame else None
        last_frame_name = await self.upload_image(*last_frame) if last_frame else None
        reference_image_names = [
            await self.upload_image(*reference_image)
            for reference_image in reference_images or []
        ]
        # /upload/image does not validate file type; it writes whatever it is given
        # into ComfyUI's input directory, which is what LoadAudio reads from.
        reference_audio_names = [
            await self.upload_image(*reference_audio)
            for reference_audio in reference_audios or []
        ]
        if reference_image_names:
            workflow = build_minimax_h3_reference_workflow(
                prompt,
                aspect_ratio,
                megapixels,
                duration,
                seed,
                reference_image_names,
                reference_audio_names,
                motion_loras,
            )
        else:
            workflow = build_minimax_h3_workflow(
                prompt,
                aspect_ratio,
                megapixels,
                duration,
                seed,
                first_frame_name,
                last_frame_name,
                motion_loras,
            )
        response = await self._request(
            "POST",
            "/prompt",
            json={"prompt": workflow, "client_id": str(uuid4())},
        )
        response.raise_for_status()
        queued = response.json()
        prompt_id = queued.get("prompt_id")
        if not prompt_id:
            raise ComfyUIVideoError(f"ComfyUI rejected the workflow: {queued}")

        deadline = time.monotonic() + self.timeout
        output = None
        while time.monotonic() < deadline:
            response = await self._request("GET", f"/history/{prompt_id}")
            response.raise_for_status()
            entry = response.json().get(prompt_id)
            if entry:
                error = _execution_error(entry)
                if error:
                    raise ComfyUIVideoError(error)
                output = find_video_output(entry)
                if output:
                    break
            await self.sleep(self.poll_interval)

        if not output:
            raise ComfyUIVideoError(
                f"MiniMax H3 generation did not finish within {self.timeout} seconds"
            )

        query = urlencode(
            {
                "filename": output["filename"],
                "subfolder": output.get("subfolder", ""),
                "type": output.get("type", "output"),
            }
        )
        response = await self._request("GET", f"/view?{query}")
        response.raise_for_status()
        content_type = response.headers.get("content-type") or mimetypes.guess_type(
            output["filename"]
        )[0] or "video/mp4"
        return response.content, output["filename"], content_type
