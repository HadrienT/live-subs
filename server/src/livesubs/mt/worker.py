"""Per-session translation queue (WP06 §3).

One request at a time, in segment order. When more than ``merge_after`` segments
are waiting (llama-server busy with the coding agent), they are merged into one
request instead of falling further behind. A request that exceeds ``timeout_s``
is abandoned with ``error{mt_timeout}``: its English line stays empty. If the
served model is not ours (``code`` profile loaded), nothing is translated and
``error{mt_model_inactive}`` says so (ADR-003): never translate silently with the
coding model.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from livesubs import protocol as p
from livesubs.mt.base import TranslationContext, Translator
from livesubs.mt.glossary import EMPTY, Glossary
from livesubs.mt.llama import MtUnreachableError
from livesubs.segments import FinalSegment

if TYPE_CHECKING:
    from livesubs.session import Session

log = logging.getLogger(__name__)

# (first_token_ms, mt_ms, en_ms since the end of speech)
MtMetrics = Callable[[float, float, float], None]


class TranslationWorker:
    def __init__(
        self,
        translator: Translator,
        *,
        glossary: Glossary = EMPTY,
        history_len: int = 6,
        merge_after: int = 3,
        timeout_s: float = 8.0,
        on_metrics: MtMetrics | None = None,
    ) -> None:
        self.translator = translator
        self.glossary = glossary
        self.history: list[tuple[str, str]] = []
        self.history_len = history_len
        self.merge_after = merge_after
        self.timeout_s = timeout_s
        self.on_metrics = on_metrics
        self._pending: list[FinalSegment] = []
        self._wake = asyncio.Event()
        self._session: Session | None = None
        self._task: asyncio.Task[None] | None = None
        self.merges = 0

    @property
    def backlog(self) -> int:
        return len(self._pending)

    def submit(self, session: Session, seg: FinalSegment) -> None:
        self._session = session
        self._pending.append(seg)
        self._wake.set()
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._run())

    def reset_context(self) -> None:
        self.history.clear()

    async def aclose(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    # ------------------------------------------------------------------ internals

    async def _run(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            while self._pending:
                assert self._session is not None
                if "en" not in self._session.targets:
                    self._pending.clear()
                    break
                if len(self._pending) > self.merge_after:
                    batch, self._pending = self._pending, []
                    self.merges += 1
                else:
                    batch = [self._pending.pop(0)]
                try:
                    await self._translate(self._session, batch)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("translation failed")

    async def _translate(self, session: Session, batch: list[FinalSegment]) -> None:
        last = batch[-1]
        status = await self.translator.status()
        if not status.reachable:
            session.emit(
                p.Error(
                    code="mt_unreachable", seg_id=last.seg_id, message="llama-server unreachable"
                )
            )
            return
        if not status.active:
            served = ", ".join(status.served) or "nothing"
            session.emit(
                p.Error(
                    code="mt_model_inactive",
                    seg_id=last.seg_id,
                    message=f"LLM serves {served}, not {self.translator.name}: "
                    "switch AgenticEnv to the translate profile",
                )
            )
            return
        ja = " ".join(s.ja for s in batch)
        seg = FinalSegment(last.seg_id, ja, batch[0].t0, last.t1, last.speech_end_at)
        ctx = TranslationContext(
            history=list(self.history), glossary=self.glossary, channel_name=self.glossary.name
        )
        started = time.monotonic()
        first_token: float | None = None
        pieces: list[str] = []
        try:
            async with asyncio.timeout(self.timeout_s):
                async for piece in self.translator.translate(seg, ctx):
                    if first_token is None:
                        first_token = time.monotonic()
                    pieces.append(piece)
                    session.emit(p.TranslationDelta(seg_id=last.seg_id, en_delta=piece))
        except TimeoutError:
            session.emit(
                p.Error(
                    code="mt_timeout",
                    seg_id=last.seg_id,
                    message=f"translation took more than {self.timeout_s:.0f} s",
                )
            )
            return
        except MtUnreachableError as e:
            session.emit(p.Error(code="mt_unreachable", seg_id=last.seg_id, message=str(e)))
            return
        done = time.monotonic()
        en = "".join(pieces).strip()
        mt_ms = (done - started) * 1000
        session.emit(
            p.Translation(
                seg_id=last.seg_id, en=en, mt_ms=mt_ms, merged=[s.seg_id for s in batch[:-1]]
            )
        )
        if en:
            self.history = [*self.history, (ja, en)][-self.history_len :]
        first_ms = ((first_token or done) - started) * 1000
        en_ms = (done - last.speech_end_at) * 1000
        session.metrics.on_translation(last.seg_id, mt_first_ms=first_ms, mt_ms=mt_ms, en_ms=en_ms)
        if self.on_metrics is not None:
            self.on_metrics(first_ms, mt_ms, en_ms)
