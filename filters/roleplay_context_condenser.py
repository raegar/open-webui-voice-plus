"""
title: Roleplay Context Condenser
description: Summarises old conversation turns into a compact narrative before sending to the API,
             keeping recent messages verbatim. Prevents token costs from ballooning in long roleplay
             sessions while preserving story continuity.
version: 0.1.0
"""

from pydantic import BaseModel, Field
from typing import Optional
import httpx


class Filter:
    class Valves(BaseModel):
        keep_recent: int = Field(
            default=20,
            description=(
                "Number of recent messages to always send verbatim. "
                "Keep this even (user+assistant pairs). Minimum 6 recommended."
            ),
        )
        trigger_at: int = Field(
            default=30,
            description=(
                "Total conversation message count (excluding system) that triggers summarisation. "
                "Must be greater than keep_recent."
            ),
        )
        resummary_every: int = Field(
            default=10,
            description=(
                "How many new messages must fall into the 'old' bucket before the summary is refreshed. "
                "Between refreshes, gap messages are kept verbatim alongside the recent window. "
                "Lower = more accurate summary, more API calls. 10 (~5 exchanges) is a good balance."
            ),
        )
        summary_model: str = Field(
            default="",
            description=(
                "Model to use for summarisation. Leave blank to reuse the current chat model. "
                "A cheap/fast model (e.g. deepseek/deepseek-chat-v3-0324:floor) works well here."
            ),
        )
        owui_base_url: str = Field(
            default="http://localhost:3000",
            description="Base URL of your Open WebUI instance. Used to make summarisation API calls.",
        )
        owui_api_key: str = Field(
            default="",
            description=(
                "Your Open WebUI API key (Settings → Account → API Keys). "
                "Required for summarisation calls."
            ),
        )
        max_summary_tokens: int = Field(
            default=500,
            description="Maximum tokens for the generated summary. 400–600 is plenty for roleplay.",
        )
        summary_label: str = Field(
            default="[Story so far]",
            description="Label injected before the summary in the system prompt.",
        )
        force_condense: bool = Field(
            default=False,
            description=(
                "Force condensation on the very next message, ignoring trigger_at. "
                "Toggle on, send any message, then toggle off. "
                "Useful for old threads that are already over the threshold."
            ),
        )

    def __init__(self):
        self.valves = self.Valves()
        # chat_id -> (summarised_message_count, summary_text)
        self._cache: dict[str, tuple[int, str]] = {}

    async def inlet(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
    ) -> dict:
        messages = body.get("messages", [])

        system_msgs = [m for m in messages if m.get("role") == "system"]
        conv_msgs = [m for m in messages if m.get("role") != "system"]

        keep = self.valves.keep_recent
        trigger = self.valves.trigger_at

        if not self.valves.force_condense and len(conv_msgs) <= trigger:
            return body

        if keep >= len(conv_msgs):
            return body

        to_summarize = conv_msgs[:-keep]
        to_keep = conv_msgs[-keep:]

        chat_id = (__metadata__ or {}).get("chat_id") or "_global"
        cached = self._cache.get(chat_id)

        if cached:
            cached_count, summary = cached
            gap_size = len(to_summarize) - cached_count

            if gap_size < self.valves.resummary_every:
                # Not enough new old-messages to warrant a fresh summary.
                # Bridge the gap by keeping those messages verbatim instead.
                if gap_size > 0:
                    gap_msgs = conv_msgs[cached_count : len(conv_msgs) - keep]
                    to_keep = gap_msgs + to_keep
            else:
                summary = await self._summarize(to_summarize, body)
                if summary is None:
                    return body  # graceful fallback — don't modify on failure
                self._cache[chat_id] = (len(to_summarize), summary)
        else:
            summary = await self._summarize(to_summarize, body)
            if summary is None:
                return body
            self._cache[chat_id] = (len(to_summarize), summary)

        body["messages"] = self._inject_summary(system_msgs, to_keep, summary)
        return body

    def _inject_summary(
        self, system_msgs: list[dict], conv_msgs: list[dict], summary: str
    ) -> list[dict]:
        label = self.valves.summary_label
        blurb = f"\n\n{label}\n{summary}"

        if system_msgs:
            last = system_msgs[-1]
            patched = {**last, "content": last["content"] + blurb}
            return system_msgs[:-1] + [patched] + conv_msgs
        else:
            return [{"role": "system", "content": blurb.lstrip()}] + conv_msgs

    async def _summarize(self, messages: list[dict], body: dict) -> Optional[str]:
        model = self.valves.summary_model or body.get("model", "")
        if not model:
            print("[context-condenser] no model available for summarisation")
            return None

        lines: list[str] = []
        for m in messages:
            role = m.get("role", "user").capitalize()
            content = m.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    part.get("text", "")
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                )
            if content:
                lines.append(f"{role}: {content}")

        if not lines:
            return None

        system_prompt = (
            "You are a story archivist for a roleplay session. "
            "Summarise the conversation below into a concise narrative of 200–400 words. "
            "Include: key plot events in order, character names and their relationships, "
            "important decisions or revelations, each character's current emotional and physical state, "
            "and any unresolved threads or tensions. "
            "Write in past tense, third person. Be specific — names, places, and details matter."
        )

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": "Summarise this roleplay:\n\n" + "\n".join(lines),
                },
            ],
            "stream": False,
            "max_tokens": self.valves.max_summary_tokens,
        }

        url = self.valves.owui_base_url.rstrip("/") + "/api/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.valves.owui_api_key:
            headers["Authorization"] = f"Bearer {self.valves.owui_api_key}"

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json=payload, headers=headers, timeout=60.0)
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"[context-condenser] summarisation API call failed: {e}")
            return None
