"""The generation queue must run one job at a time, in submission order.

There is a single ComfyUI behind the studio, so overlap is the failure that matters:
before the queue existed a second submission raced the first and usually died against
the request timeout while it sat behind the other one in ComfyUI's own queue.

Each test drives its own event loop rather than using pytest-asyncio, which is not
installed in the runtime image.
"""

import asyncio
import time

import pytest

from open_webui.routers import videos


@pytest.fixture(autouse=True)
def clean_queue_state():
    """Give each test an empty job table, a fresh queue, and no worker.

    The queue is replaced rather than drained because an asyncio.Queue binds to the
    loop that first uses it, and every test runs on a loop of its own.
    """
    videos.VIDEO_GENERATION_JOBS.clear()
    videos.VIDEO_GENERATION_QUEUE = asyncio.Queue()
    videos.VIDEO_GENERATION_WORKER = None
    yield
    videos.VIDEO_GENERATION_JOBS.clear()
    videos.VIDEO_GENERATION_WORKER = None


class FakeUser:
    id = "user-1"


def submit(job_id, prompt="a clip"):
    """Put a job on the table exactly as the create endpoint does, minus HTTP."""
    now = time.time()
    videos.VIDEO_GENERATION_JOBS[job_id] = {
        "user_id": FakeUser.id,
        "status": "queued",
        "created_at": now,
        "updated_at": now,
        "result": None,
        "error": None,
        "label": prompt,
        "request": object(),
        "form_data": object(),
        "user": FakeUser(),
    }


def drain(job_ids, timeout=5.0):
    """Enqueue the ids, run the worker, and return once none of them are active."""

    async def _run():
        videos._ensure_video_generation_worker()
        for job_id in job_ids:
            await videos.VIDEO_GENERATION_QUEUE.put(job_id)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if all(
                videos.VIDEO_GENERATION_JOBS[j]["status"]
                not in videos.VIDEO_JOB_ACTIVE_STATES
                for j in job_ids
            ):
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("queue did not drain")
        worker = videos.VIDEO_GENERATION_WORKER
        if worker is not None and not worker.done():
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass

    asyncio.run(_run())


def stub_generation(monkeypatch, fake_generate):
    monkeypatch.setattr(videos, "video_generations", fake_generate)
    # Resolving a regeneration prompt reaches the database, which these tests do not have.
    monkeypatch.setattr(videos, "_resolve_regeneration_prompt", lambda *a, **k: None)


def test_jobs_run_one_at_a_time_in_order(monkeypatch):
    spans = []
    live = 0

    async def fake_generate(request, form_data, metadata=None, user=None):
        nonlocal live
        live += 1
        # The whole point of the queue: never two generations in flight at once.
        assert live == 1, "two generations overlapped"
        start = time.monotonic()
        await asyncio.sleep(0.05)
        live -= 1
        spans.append((start, time.monotonic()))
        return [{"url": "/x.mp4"}]

    stub_generation(monkeypatch, fake_generate)

    ids = ["job-a", "job-b", "job-c"]
    for job_id in ids:
        submit(job_id)

    drain(ids)

    assert [videos.VIDEO_GENERATION_JOBS[j]["status"] for j in ids] == ["completed"] * 3
    assert len(spans) == 3
    for earlier, later in zip(spans, spans[1:]):
        assert earlier[1] <= later[0], "generation spans overlapped"


def test_queue_positions_count_jobs_ahead():
    submit("job-a")
    submit("job-b")
    submit("job-c")
    # Nothing running yet, so position is simply submission order.
    assert videos._jobs_ahead("job-a") == 0
    assert videos._jobs_ahead("job-b") == 1
    assert videos._jobs_ahead("job-c") == 2

    videos.VIDEO_GENERATION_JOBS["job-a"]["status"] = "running"
    assert videos._jobs_ahead("job-a") == 0
    assert videos._jobs_ahead("job-b") == 1

    # A finished job stops counting against the ones behind it.
    videos.VIDEO_GENERATION_JOBS["job-a"]["status"] = "completed"
    assert videos._jobs_ahead("job-a") is None
    assert videos._jobs_ahead("job-b") == 0
    assert videos._jobs_ahead("job-c") == 1


def test_cancelled_job_is_skipped_and_does_not_block(monkeypatch):
    ran = []

    async def fake_generate(request, form_data, metadata=None, user=None):
        ran.append(True)
        return [{"url": "/x.mp4"}]

    stub_generation(monkeypatch, fake_generate)

    submit("job-a")
    submit("job-b")
    # Cancelled before the worker reaches it, exactly as the endpoint leaves it.
    videos.VIDEO_GENERATION_JOBS["job-a"]["status"] = "cancelled"
    videos._release_job_inputs(videos.VIDEO_GENERATION_JOBS["job-a"])

    drain(["job-a", "job-b"])

    assert videos.VIDEO_GENERATION_JOBS["job-a"]["status"] == "cancelled"
    assert videos.VIDEO_GENERATION_JOBS["job-b"]["status"] == "completed"
    # The cancelled one never reached ComfyUI, and the one behind it still ran.
    assert len(ran) == 1


def test_failure_does_not_stop_the_queue(monkeypatch):
    calls = []

    async def fake_generate(request, form_data, metadata=None, user=None):
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError("comfy exploded")
        return [{"url": "/x.mp4"}]

    stub_generation(monkeypatch, fake_generate)

    submit("job-a")
    submit("job-b")
    drain(["job-a", "job-b"])

    assert videos.VIDEO_GENERATION_JOBS["job-a"]["status"] == "failed"
    assert "comfy exploded" in videos.VIDEO_GENERATION_JOBS["job-a"]["error"]
    # The queue keeps going rather than wedging on the first failure.
    assert videos.VIDEO_GENERATION_JOBS["job-b"]["status"] == "completed"


def test_finished_jobs_release_their_payload(monkeypatch):
    async def fake_generate(request, form_data, metadata=None, user=None):
        return [{"url": "/x.mp4"}]

    stub_generation(monkeypatch, fake_generate)

    submit("job-a")
    drain(["job-a"])

    job = videos.VIDEO_GENERATION_JOBS["job-a"]
    # Reference images travel as data URLs and run to tens of megabytes, so a
    # finished job must not keep the submitted payload alive.
    assert "form_data" not in job
    assert "request" not in job
    assert "user" not in job
