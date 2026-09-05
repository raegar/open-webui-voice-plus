import pytest

from open_webui.utils.videos.comfyui import (
    ComfyUIVideoClient,
    build_minimax_h3_reference_workflow,
    build_minimax_h3_workflow,
    find_video_output,
)


class FakeResponse:
    def __init__(self, payload=None, content=b"", content_type="application/json"):
        self.payload = payload or {}
        self.content = content
        self.headers = {"content-type": content_type}

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self):
        self.calls = []
        self.history_calls = 0
        self.workflow = None

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if url.endswith("/upload/image"):
            upload_name = kwargs["files"]["image"][0]
            return FakeResponse({"name": upload_name})
        if url.endswith("/prompt"):
            self.workflow = kwargs["json"]["prompt"]
            return FakeResponse({"prompt_id": "prompt-1"})
        if "/history/" in url:
            self.history_calls += 1
            if self.history_calls == 1:
                return FakeResponse({"prompt-1": {"outputs": {}, "status": {}}})
            return FakeResponse(
                {
                    "prompt-1": {
                        "outputs": {
                            "92": {
                                "images": [
                                    {
                                        "filename": "MiniMax_H3_00001_.mp4",
                                        "subfolder": "video",
                                        "type": "output",
                                    }
                                ]
                            }
                        },
                        "status": {"completed": True, "status_str": "success"},
                    }
                }
            )
        if "/view?" in url:
            return FakeResponse(content=b"video", content_type="video/mp4")
        raise AssertionError(url)


def test_build_workflow_maps_supported_options():
    workflow = build_minimax_h3_workflow("prompt", "9:16", 0.4, 10, 42)
    inputs = workflow["105:104"]["inputs"]
    assert (inputs["width"], inputs["height"]) == (480, 864)
    assert workflow["105:111"]["inputs"]["value"] == 10.0
    assert workflow["105:15"]["inputs"]["noise_seed"] == 42
    assert "114" not in workflow


def test_build_workflow_wires_first_and_last_frames():
    workflow = build_minimax_h3_workflow(
        "prompt", "16:9", 0.2, 5, 42, "first.png", "last.png"
    )
    inputs = workflow["105:104"]["inputs"]
    assert workflow["114"]["inputs"]["image"] == "first.png"
    assert workflow["115"]["inputs"]["image"] == "last.png"
    assert inputs["first_frame"] == ["114", 0]
    assert inputs["last_frame"] == ["115", 0]


def test_build_reference_workflow_wires_ordered_images():
    workflow = build_minimax_h3_reference_workflow(
        "prompt", "16:9", 0.4, 10, 42, ["person.png", "location.webp"]
    )
    inputs = workflow["136"]["inputs"]
    assert workflow["127"]["inputs"]["unet_name"] == (
        "minimax_h3_ref2va_pruned_int8_convrot.safetensors"
    )
    assert workflow["136"]["class_type"] == "MiniMaxH3ReferenceToVideo"
    assert (inputs["width"], inputs["height"]) == (864, 480)
    assert inputs["ref_images.ref_image_0"] == ["ref-image-0", 0]
    assert inputs["ref_images.ref_image_1"] == ["ref-image-1", 0]
    assert workflow["ref-image-0"]["inputs"]["image"] == "person.png"
    assert workflow["ref-image-1"]["inputs"]["image"] == "location.webp"
    assert workflow["129"]["inputs"]["noise_seed"] == 42


def test_build_reference_workflow_requires_one_to_nine_images():
    with pytest.raises(ValueError):
        build_minimax_h3_reference_workflow("prompt", "16:9", 0.2, 5, 42, [])


def test_find_video_output_uses_extension_not_bucket_name():
    entry = {
        "outputs": {
            "92": {
                "images": [
                    {
                        "filename": "clip.mp4",
                        "subfolder": "video",
                        "type": "output",
                    }
                ]
            }
        }
    }
    assert find_video_output(entry)["filename"] == "clip.mp4"


@pytest.mark.asyncio
async def test_client_queues_text_workflow_polls_and_downloads_video():
    async def no_sleep(_):
        return None

    session = FakeSession()
    client = ComfyUIVideoClient(
        "http://comfyui:8188",
        session=session,
        poll_interval=0,
        sleep=no_sleep,
    )
    data, filename, content_type = await client.generate(
        "prompt",
        "16:9",
        0.2,
        3,
        7,
    )
    assert data == b"video"
    assert filename == "MiniMax_H3_00001_.mp4"
    assert content_type == "video/mp4"
    assert session.history_calls == 2
    assert "first_frame" not in session.workflow["105:104"]["inputs"]
    assert not any("/upload/image" in call[1] for call in session.calls)


@pytest.mark.asyncio
async def test_client_uploads_and_wires_first_and_last_frames():
    async def no_sleep(_):
        return None

    session = FakeSession()
    client = ComfyUIVideoClient(
        "http://comfyui:8188",
        session=session,
        poll_interval=0,
        sleep=no_sleep,
    )
    await client.generate(
        "prompt",
        "16:9",
        0.2,
        5,
        7,
        (b"first", "first.png", "image/png"),
        (b"last", "last.png", "image/png"),
    )
    inputs = session.workflow["105:104"]["inputs"]
    assert inputs["first_frame"][0] == "114"
    assert inputs["last_frame"][0] == "115"
    uploads = [call for call in session.calls if "/upload/image" in call[1]]
    assert len(uploads) == 2
    assert uploads[0][2]["files"]["image"][2] == "image/png"


@pytest.mark.asyncio
async def test_client_uploads_and_wires_reference_images():
    async def no_sleep(_):
        return None

    session = FakeSession()
    client = ComfyUIVideoClient(
        "http://comfyui:8188",
        session=session,
        poll_interval=0,
        sleep=no_sleep,
    )
    await client.generate(
        "subject_definitions:\n<Subject 1> comes from <Picture 1>.",
        "9:16",
        0.2,
        5,
        7,
        reference_images=[
            (b"first", "person.png", "image/png"),
            (b"second", "style.webp", "image/webp"),
        ],
    )
    inputs = session.workflow["136"]["inputs"]
    assert inputs["ref_images.ref_image_0"][0] == "ref-image-0"
    assert inputs["ref_images.ref_image_1"][0] == "ref-image-1"
    assert session.workflow["127"]["inputs"]["unet_name"].startswith(
        "minimax_h3_ref2va"
    )
    uploads = [call for call in session.calls if "/upload/image" in call[1]]
    assert len(uploads) == 2


def test_reference_workflow_wires_voice_references():
    """ComfyUI ignores an unknown autogrow key instead of erroring, so the exact
    `ref_audios.ref_audio_N` shape is worth asserting."""
    workflow = build_minimax_h3_reference_workflow(
        "a prompt",
        "16:9",
        0.2,
        5,
        123,
        ["img-a.png", "img-b.png"],
        ["voice-1.wav", "voice-2.mp3"],
    )
    node = workflow["136"]["inputs"]
    assert node["ref_images.ref_image_0"] == ["200", 0] or node["ref_images.ref_image_0"][1] == 0
    assert node["ref_audios.ref_audio_0"][1] == 0
    assert node["ref_audios.ref_audio_1"][1] == 0

    audio_nodes = [
        n for n in workflow.values() if n.get("class_type") == "LoadAudio"
    ]
    assert len(audio_nodes) == 2
    assert {n["inputs"]["audio"] for n in audio_nodes} == {"voice-1.wav", "voice-2.mp3"}


def test_reference_workflow_rejects_too_many_voices():
    with pytest.raises(ValueError):
        build_minimax_h3_reference_workflow(
            "a prompt", "16:9", 0.2, 5, 1, ["img.png"], ["a.wav", "b.wav", "c.wav", "d.wav"]
        )


def test_reference_workflow_without_voices_has_no_audio_nodes():
    workflow = build_minimax_h3_reference_workflow(
        "a prompt", "16:9", 0.2, 5, 1, ["img.png"]
    )
    assert not [n for n in workflow.values() if n.get("class_type") == "LoadAudio"]
    assert not [k for k in workflow["136"]["inputs"] if k.startswith("ref_audios.")]


def test_motion_loras_are_absent_unless_requested():
    """The known-good path must stay byte-identical when the toggle is off."""
    for workflow in (
        build_minimax_h3_workflow("p", "9:16", 0.2, 10, 1),
        build_minimax_h3_reference_workflow("p", "9:16", 0.2, 10, 1, ["a.png"]),
    ):
        assert not [n for n in workflow.values() if n["class_type"] == "LoraLoaderModelOnly"]
        assert not [n for n in workflow.values() if n["class_type"] == "MiniMaxH3SigmaShift"]
    base = build_minimax_h3_workflow("p", "9:16", 0.2, 10, 1)
    assert base["105:17"]["inputs"]["sampler_name"] == "res_multistep"
    assert base["105:9"]["inputs"]["steps"] == 20


def test_motion_loras_chain_in_order_and_retune_the_sampler():
    workflow = build_minimax_h3_workflow("p", "9:16", 0.2, 10, 1, motion_loras=True)
    # UNet -> hmmotion -> turbo -> shift -> guider/scheduler.
    assert workflow["901"]["inputs"]["model"] == ["105:6", 0]
    assert workflow["902"]["inputs"]["model"] == ["901", 0]
    assert workflow["903"]["inputs"]["model"] == ["902", 0]
    assert workflow["105:16"]["inputs"]["model"] == ["903", 0]
    assert workflow["105:9"]["inputs"]["model"] == ["903", 0]
    assert workflow["902"]["inputs"]["strength_model"] == 0.5
    assert workflow["903"]["inputs"]["shift_video"] == 6.0
    assert workflow["105:17"]["inputs"]["sampler_name"] == "euler"
    assert workflow["105:9"]["inputs"]["steps"] == 12


def test_motion_loras_apply_to_the_reference_workflow_too():
    workflow = build_minimax_h3_reference_workflow(
        "p", "9:16", 0.2, 10, 1, ["a.png"], motion_loras=True
    )
    assert workflow["901"]["inputs"]["model"] == ["127", 0]
    assert workflow["126"]["inputs"]["model"] == ["903", 0]
    assert workflow["124"]["inputs"]["model"] == ["903", 0]
    assert workflow["123"]["inputs"]["sampler_name"] == "euler"
    assert workflow["124"]["inputs"]["steps"] == 12
