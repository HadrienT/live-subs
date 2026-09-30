"""``LlamaServerTranslator``: streaming client for AgenticEnv's llama-server (ADR-003).

OpenAI-compatible ``/v1/chat/completions`` with ``stream: true`` over plain httpx:
an SSE loop is a few lines and spares the ``openai`` SDK.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from livesubs.mt.base import ModelStatus, TranslationContext, build_messages
from livesubs.segments import FinalSegment

log = logging.getLogger(__name__)


class MtUnreachableError(Exception):
    pass


class LlamaServerTranslator:
    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        temperature: float = 0.2,
        max_tokens: int = 120,
        disable_thinking: bool = True,
        client: httpx.AsyncClient | None = None,
        status_ttl_s: float = 10.0,
    ) -> None:
        self.name = model
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.disable_thinking = disable_thinking
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=2.0))
        self._status: tuple[float, ModelStatus] | None = None
        self._status_ttl = status_ttl_s

    async def status(self, *, fresh: bool = False) -> ModelStatus:
        """Is llama-server up, and does it serve ``model``? Cached for a few seconds."""
        now = time.monotonic()
        if not fresh and self._status and now - self._status[0] < self._status_ttl:
            return self._status[1]
        try:
            resp = await self._client.get(f"{self.base_url}/models", timeout=2.0)
            resp.raise_for_status()
            served = [m["id"] for m in resp.json().get("data", [])]
            status = ModelStatus(True, self.model in served, served)
        except (httpx.HTTPError, ValueError, KeyError) as e:
            log.debug("llama-server unreachable: %s", e)
            status = ModelStatus(False, False, [])
        self._status = (now, status)
        return status

    def payload(self, ja: str, ctx: TranslationContext) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": build_messages(ja, ctx),
            "stream": True,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if self.disable_thinking:
            # Qwen3-style templates; ignored by templates that do not use it.
            body["chat_template_kwargs"] = {"enable_thinking": False}
        return body

    async def translate(self, seg: FinalSegment, ctx: TranslationContext) -> AsyncIterator[str]:
        body = self.payload(seg.ja, ctx)
        started = False
        try:
            async with self._client.stream(
                "POST", f"{self.base_url}/chat/completions", json=body
            ) as resp:
                if resp.status_code >= 400:
                    detail = (await resp.aread())[:200].decode("utf-8", "replace")
                    raise MtUnreachableError(f"llama-server HTTP {resp.status_code}: {detail}")
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    chunk = json.loads(data)
                    choices = chunk.get("choices") or [{}]
                    delta = choices[0].get("delta", {}).get("content") or ""
                    if not delta:
                        continue
                    if not started:
                        delta = delta.lstrip()
                        started = bool(delta)
                    if "\n" in delta and started:
                        # One subtitle line: stop at the first line break after content.
                        head = delta.split("\n", 1)[0]
                        if head:
                            yield head
                        break
                    if delta:
                        yield delta
        except httpx.HTTPError as e:
            self._status = None
            raise MtUnreachableError(str(e)) from e

    async def aclose(self) -> None:
        await self._client.aclose()
