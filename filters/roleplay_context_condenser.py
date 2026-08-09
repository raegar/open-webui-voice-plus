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
        max_context_tokens: int = Field(
            default=120000,
            description=(
                "Approximate token budget for the outgoing request. Condensation also "
                "triggers when the estimated conversation size exceeds this, and the kept "
                "window is trimmed from the front until it fits. Set comfortably below your "
                "model's hard context limit to leave room for the response (e.g. ~120000 "
                "for a 163840-token model)."
            ),
        )

    def __init__(self):
        self.valves = self.Valves()
        # chat_id -> (summarised_message_count, summary_text)
        self._cache: dict[str, tuple[int, str]] = {}

    @staticmethod
    def _message_text(m: dict) -> str:
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        return content or ""

    @classmethod
    def _estimate_tokens(cls, messages: list[dict]) -> int:
        # Rough heuristic: ~4 chars/token plus a small per-message overhead.
        # Good enough to keep us safely under a hard context limit.
        return sum(len(cls._message_text(m)) // 4 + 4 for m in messages)

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

        over_count = len(conv_msgs) > trigger
        over_tokens = (
            self._estimate_tokens(messages) > self.valves.max_context_tokens
        )

        if not self.valves.force_condense and not over_count and not over_tokens:
            return body

        if keep >= len(conv_msgs):
            # Nothing old enough to summarise. Still cap the window if we're
            # over the token budget, otherwise leave the request untouched.
            if over_tokens:
                body["messages"] = system_msgs + self._enforce_budget(
                    system_msgs, conv_msgs, ""
                )
            return body

        to_summarize = conv_msgs[:-keep]
        to_keep = conv_msgs[-keep:]

        chat_id = (__metadata__ or {}).get("chat_id") or "_global"
        cached = self._cache.get(chat_id)

        summary = ""
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
                fresh = await self._summarize(to_summarize, body)
                if fresh is not None:
                    summary = fresh
                    self._cache[chat_id] = (len(to_summarize), fresh)
                # else: summariser failed — keep the stale cached summary and
                # still enforce the budget below so the request fits.
        else:
            fresh = await self._summarize(to_summarize, body)
            if fresh is not None:
                summary = fresh
                self._cache[chat_id] = (len(to_summarize), fresh)
            # else: no summary available, but we must NOT bail out — budget
            # enforcement below still trims the window so the request fits.

        to_keep = self._enforce_budget(system_msgs, to_keep, summary)
        body["messages"] = self._inject_summary(system_msgs, to_keep, summary)
        return body

    def _enforce_budget(
        self, system_msgs: list[dict], conv_msgs: list[dict], summary: str
    ) -> list[dict]:
        # Even after summarising the old tail, the kept window can exceed the
        # model's hard limit (long roleplay turns). Drop oldest kept messages
        # until the whole request fits under budget. Better to lose a little
        # recent verbatim history than to have the endpoint reject the request.
        budget = self.valves.max_context_tokens
        overhead = self._estimate_tokens(system_msgs) + len(summary) // 4
        kept = list(conv_msgs)
        while len(kept) > 2 and self._estimate_tokens(kept) + overhead > budget:
            kept = kept[1:]
        return kept

    def _inject_summary(
        self, system_msgs: list[dict], conv_msgs: list[dict], summary: str
    ) -> list[dict]:
        if not summary.strip():
            # Summariser unavailable; the window was still trimmed to fit.
            return system_msgs + conv_msgs

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
            content = self._message_text(m)
            if content:
                lines.append(f"{role}: {content}")

        if not lines:
            return None

        joined = "\n".join(lines)

        # Cap the summariser's own input so THIS call cannot itself overflow the
        # model's context window. Without this, force-condensing an already-
        # oversized chat sends the whole old tail in one request and throws the
        # exact same context-length error we're trying to escape. Keep the most
        # recent portion of the old history (it bridges to the verbatim window).
        input_budget = int(
            (self.valves.max_context_tokens - self.valves.max_summary_tokens)
            * 4
            * 0.85
        )
        truncated = input_budget > 0 and len(joined) > input_budget
        if truncated:
            joined = joined[-input_budget:]

        note = (
            "(Note: only the most recent portion of the older history is shown; "
            "summarise what is present.)\n\n"
            if truncated
            else ""
        )

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
                    "content": "Summarise this roleplay:\n\n" + note + joined,
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
