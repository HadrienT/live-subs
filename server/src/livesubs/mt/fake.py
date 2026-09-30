"""``FakeTranslator``: deterministic, streamed output for tests and GPU-less runs."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from livesubs.mt.base import ModelStatus, TranslationContext
from livesubs.segments import FinalSegment


class FakeTranslator:
    name = "fake-mt"

    def __init__(
        self, *, delay_s: float = 0.0, first_token_s: float = 0.0, active: bool = True
    ) -> None:
        self.delay_s = delay_s
        self.first_token_s = first_token_s
        self.active = active
        self.requests: list[tuple[str, TranslationContext]] = []

    async def status(self, *, fresh: bool = False) -> ModelStatus:
        return ModelStatus(True, self.active, [self.name if self.active else "code-model"])

    async def translate(
        self, seg: FinalSegment, ctx: TranslationContext, *, retry: bool = False
    ) -> AsyncIterator[str]:
        """``EN(<ja>)``, with glossary terms replaced by their imposed spelling."""
        self.requests.append((seg.ja, ctx))
        text = seg.ja
        for ja, en in sorted(ctx.glossary.terms.items(), key=lambda kv: -len(kv[0])):
            text = text.replace(ja, f" {en} ")
        out = f"EN({' '.join(text.split())})"
        if self.first_token_s:
            await asyncio.sleep(self.first_token_s)
        for i in range(0, len(out), 4):
            if self.delay_s:
                await asyncio.sleep(self.delay_s)
            yield out[i : i + 4]

    async def aclose(self) -> None:
        pass
