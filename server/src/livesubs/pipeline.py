"""Per-session pipeline: VAD segments → ``partial`` / ``final`` (→ translation)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time

import numpy as np
from numpy.typing import NDArray

from livesubs import protocol as p
from livesubs.asr.agreement import LocalAgreement
from livesubs.asr.filters import reject_reason
from livesubs.asr.scheduler import GpuScheduler
from livesubs.mt.worker import TranslationWorker
from livesubs.pipeline_options import AsrOptions
from livesubs.segments import FinalSegment
from livesubs.session import Session
from livesubs.vad import SpeechSegment

log = logging.getLogger(__name__)

MIN_PARTIAL_S = 0.5
Audio = NDArray[np.float32]


class StreamingAsrSink:
    def __init__(
        self,
        scheduler: GpuScheduler,
        *,
        options: AsrOptions | None = None,
        prompt_terms: str = "",
        translation: TranslationWorker | None = None,
    ) -> None:
        self.scheduler = scheduler
        self.opts = options or AsrOptions()
        self.prompt_terms = prompt_terms
        self.translation = translation
        self._context = ""
        self._agreements: dict[int, LocalAgreement] = {}
        self._partials_sent: set[int] = set()
        self._closed: set[int] = set()
        self._tasks: set[asyncio.Task[None]] = set()
        self.rejected: dict[str, int] = {}

    # ------------------------------------------------------------------ SegmentSink

    def on_segment(self, session: Session, seg: SpeechSegment) -> None:
        t0, t1 = session.bounds(seg)
        audio = session.audio(seg)
        if seg.is_closed:
            self._closed.add(seg.seg_id)
            coro = self._final(session, seg, t0, t1, audio, session.speech_end_at(seg))
        elif session.show_partials and seg.duration_s >= MIN_PARTIAL_S:
            coro = self._partial(session, seg, t0, t1, audio)
        else:
            return
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def on_discontinuity(self, session: Session) -> None:
        # A wrong context propagates: never carry it across a jump (seek, ad, new video).
        self._context = ""
        if self.translation is not None:
            self.translation.reset_context()

    async def aclose(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        # may run inside a cancelled `finally`: keep closing (see TranslationWorker.aclose)
        if self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.wait(list(self._tasks), timeout=2.0)
        if self.translation is not None:
            await self.translation.aclose()

    # ------------------------------------------------------------------ internals

    def _prompt(self) -> str | None:
        if not self.opts.use_prompt:
            return None
        prompt = (self.prompt_terms + " " + self._context[-self.opts.prompt_chars :]).strip()
        return prompt or None

    async def _partial(
        self, session: Session, seg: SpeechSegment, t0: float, t1: float, audio: Audio
    ) -> None:
        timed = await self.scheduler.transcribe(
            audio, prompt=self._prompt(), final=False, key=(session.id, seg.seg_id)
        )
        if timed is None or seg.seg_id in self._closed:
            return
        if reject_reason(timed.result, seg.duration_s, self.opts.filters):
            return
        agreement = self._agreements.setdefault(seg.seg_id, LocalAgreement())
        stable, unstable = agreement.update(timed.result.text)
        self._partials_sent.add(seg.seg_id)
        session.emit(
            p.Partial(seg_id=seg.seg_id, ja_stable=stable, ja_unstable=unstable, t0=t0, t1=t1)
        )

    async def _final(
        self,
        session: Session,
        seg: SpeechSegment,
        t0: float,
        t1: float,
        audio: Audio,
        speech_end_at: float,
    ) -> None:
        closed_at = time.monotonic()
        timed = await self.scheduler.transcribe(
            audio, prompt=self._prompt(), final=True, key=(session.id, seg.seg_id)
        )
        self._agreements.pop(seg.seg_id, None)
        shown = seg.seg_id in self._partials_sent
        self._partials_sent.discard(seg.seg_id)
        if timed is None:
            return
        reason = reject_reason(timed.result, seg.duration_s, self.opts.filters)
        if reason:
            self.rejected[reason] = self.rejected.get(reason, 0) + 1
            log.debug("seg %d dropped (%s): %r", seg.seg_id, reason, timed.result.text)
            if shown:  # retract the partials already on screen
                session.emit(p.Final(seg_id=seg.seg_id, ja="", t0=t0, t1=t1, asr_ms=timed.asr_ms))
            return
        text = timed.result.text
        self._context = (self._context + text)[-self.opts.prompt_chars :]
        session.emit(p.Final(seg_id=seg.seg_id, ja=text, t0=t0, t1=t1, asr_ms=timed.asr_ms))
        session.metrics.on_final(
            seg.seg_id,
            vad_wait_ms=(closed_at - speech_end_at) * 1000,
            queue_ms=timed.queue_ms,
            asr_ms=timed.asr_ms,
            ja_ms=(time.monotonic() - speech_end_at) * 1000,
        )
        if self.translation is not None:
            self.translation.submit(session, FinalSegment(seg.seg_id, text, t0, t1, speech_end_at))
