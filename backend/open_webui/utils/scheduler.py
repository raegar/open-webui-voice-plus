import asyncio
import json
import logging
import time
from datetime import datetime
from uuid import uuid4

from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import StreamingResponse

from open_webui.models.chats import Chats
from open_webui.models.scheduled_jobs import ScheduledJobModel, ScheduledJobs
from open_webui.models.users import Users

log = logging.getLogger(__name__)


async def scheduler_loop(app):
    """Background task that fires due scheduled jobs once per minute."""
    log.info("Scheduled job runner started")
    while True:
        now = datetime.now()
        sleep_secs = 60 - now.second - now.microsecond / 1_000_000
        await asyncio.sleep(sleep_secs)

        now = datetime.now()
        try:
            due = ScheduledJobs.get_due_jobs(now.hour, now.minute, now.weekday())
            for job in due:
                log.info(f"Firing scheduled job '{job.title}' ({job.id})")
                asyncio.create_task(run_scheduled_job(app, job))
        except Exception as e:
            log.exception(f"Scheduler error: {e}")


async def run_scheduled_job(app, job: ScheduledJobModel):
    """Execute a single scheduled job: inject a user message and run a completion."""
    try:
        user = Users.get_user_by_id(job.user_id)
        if not user:
            log.warning(f"Scheduled job {job.id}: user {job.user_id} not found")
            return

        chat = Chats.get_chat_by_id_and_user_id(job.chat_id, job.user_id)
        if not chat:
            log.warning(f"Scheduled job {job.id}: chat {job.chat_id} not found")
            return

        history = chat.chat.get("history", {})
        messages_map = history.get("messages", {})
        ordered_messages = _build_ordered_messages(history, messages_map)

        last_message_id = ordered_messages[-1]["id"] if ordered_messages else None

        # Save the injected user message
        user_message_id = str(uuid4())
        user_message = {
            "id": user_message_id,
            "role": "user",
            "content": job.prompt,
            "timestamp": int(time.time()),
            "parentId": last_message_id,
            "childrenIds": [],
        }
        Chats.upsert_message_to_chat_by_id_and_message_id(
            job.chat_id, user_message_id, user_message
        )

        completion_messages = [
            {"role": m["role"], "content": m.get("content", "")}
            for m in ordered_messages
            if m.get("role") in ("system", "user", "assistant") and m.get("content")
        ]
        completion_messages.append({"role": "user", "content": job.prompt})

        assistant_message_id = str(uuid4())

        mock_request = Request(
            {
                "type": "http",
                "asgi.version": "3.0",
                "asgi.spec_version": "2.0",
                "method": "POST",
                "path": "/internal/scheduler",
                "query_string": b"",
                "headers": Headers({}).raw,
                "client": ("127.0.0.1", 0),
                "server": ("127.0.0.1", 8080),
                "scheme": "http",
                "app": app,
            }
        )

        # NO session_id — forces chat_completion to run process_chat synchronously
        # (with session_id it spawns a background task and returns immediately)
        form_data = {
            "model": job.model_id,
            "messages": completion_messages,
            "stream": False,
            "chat_id": job.chat_id,
            "id": assistant_message_id,
            "parent_id": user_message_id,
            "filter_ids": [],
            "features": {},
            "variables": {},
        }

        from open_webui.main import chat_completion

        response = await chat_completion(mock_request, form_data, user)

        # Extract content from the response and save it directly
        content = None
        if isinstance(response, StreamingResponse):
            # Shouldn't happen with stream=False, but handle just in case
            chunks = []
            async for chunk in response.body_iterator:
                if isinstance(chunk, bytes):
                    chunk = chunk.decode("utf-8")
                if chunk.startswith("data: ") and not chunk.startswith("data: [DONE]"):
                    try:
                        data = json.loads(chunk[6:])
                        delta = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                        if delta:
                            chunks.append(delta)
                    except Exception:
                        pass
            content = "".join(chunks)
        elif isinstance(response, dict):
            content = response.get("choices", [{}])[0].get("message", {}).get("content")
        else:
            # JSONResponse
            try:
                data = json.loads(response.body.decode("utf-8"))
                content = data.get("choices", [{}])[0].get("message", {}).get("content")
            except Exception:
                pass

        if content:
            Chats.upsert_message_to_chat_by_id_and_message_id(
                job.chat_id,
                assistant_message_id,
                {
                    "id": assistant_message_id,
                    "role": "assistant",
                    "content": content,
                    "model": job.model_id,
                    "timestamp": int(time.time()),
                    "parentId": user_message_id,
                    "childrenIds": [],
                    "done": True,
                },
            )
            log.info(f"Scheduled job '{job.title}' ({job.id}) completed — saved {len(content)} chars")
        else:
            log.warning(f"Scheduled job '{job.title}' ({job.id}) — no content in response: {response}")

    except Exception as e:
        log.exception(f"Scheduled job {job.id} failed: {e}")
    finally:
        ScheduledJobs.set_last_run(job.id)


def _build_ordered_messages(history: dict, messages_map: dict) -> list:
    current_id = history.get("currentId")
    if not current_id or not messages_map:
        msgs = list(messages_map.values())
        msgs.sort(key=lambda m: m.get("timestamp", 0))
        return msgs

    chain = []
    visited = set()
    msg_id = current_id
    while msg_id and msg_id not in visited:
        msg = messages_map.get(msg_id)
        if not msg:
            break
        chain.append(msg)
        visited.add(msg_id)
        msg_id = msg.get("parentId")

    chain.reverse()
    return chain
