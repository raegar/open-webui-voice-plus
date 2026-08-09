import pytest

from open_webui.utils.videos.comfyui import (
    ComfyUIVideoClient,
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

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if url.endswith("/upload/image"):
            return FakeResponse({"name": "source.png"})
        if url.endswith("/prompt"):
            workflow = kwargs["json"]["prompt"]
            assert workflow["105:104"]["inputs"]["first_frame"] == ["114", 0]
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
    workflow = build_minimax_h3_workflow("prompt", "9:16", 0.4, 5, 42)
    inputs = workflow["105:104"]["inputs"]
    assert (inputs["width"], inputs["height"]) == (480, 864)
    assert workflow["105:111"]["inputs"]["value"] == 5.0
    assert workflow["105:15"]["inputs"]["noise_seed"] == 42
    assert "114" not in workflow


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
async def test_client_uploads_image_polls_and_downloads_video():
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
        (b"image", "source.png", "image/png"),
    )
    assert data == b"video"
    assert filename == "MiniMax_H3_00001_.mp4"
    assert content_type == "video/mp4"
    assert session.history_calls == 2
