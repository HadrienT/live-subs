"""Single GPU inference thread fed by a priority queue (ADR-005, WP05 §2).

``final``s go first; ``partial``s are coalesced per key: a newer partial for the
same segment replaces the one still waiting, and a final cancels it. So a busy
GPU never drops a final and never lets partials pile up.
"""

from __future__ import annotations

import asyncio
import heapq
import itertools
import logging
import threading
import time
from collections.abc import Hashable
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from livesubs.asr.base import AsrResult, Transcriber

log = logging.getLogger(__name__)

FINAL, PARTIAL = 0, 1


@dataclass(order=True)
class _Job:
    priority: int
    seq: int
    key: Hashable = field(compare=False)
    audio: NDArray[np.float32] = field(compare=False, repr=False)
    prompt: str | None = field(compare=False)
    future: asyncio.Future[Timed | None] = field(compare=False, repr=False)
    loop: asyncio.AbstractEventLoop = field(compare=False, repr=False)
    queued_at: float = field(compare=False, default_factory=time.monotonic)
    cancelled: bool = field(compare=False, default=False)


@dataclass(frozen=True, slots=True)
class Timed:
    result: AsrResult
    queue_ms: float
    asr_ms: float


def _resolve(fut: asyncio.Future[Timed | None], value: Timed | BaseException | None) -> None:
    if fut.done():
        return
    if isinstance(value, BaseException):
        fut.set_exception(value)
    else:
        fut.set_result(value)


class GpuScheduler:
    def __init__(self, transcriber: Transcriber) -> None:
        self.transcriber = transcriber
        self._heap: list[_Job] = []
        self._partials: dict[Hashable, _Job] = {}
        self._cond = threading.Condition()
        self._seq = itertools.count()
        self._closed = False
        self.busy = False
        self._thread = threading.Thread(target=self._run, name="gpu-inference", daemon=True)
        self._thread.start()

    @property
    def queue_depth(self) -> int:
        with self._cond:
            return sum(not j.cancelled for j in self._heap)

    async def transcribe(
        self, audio: NDArray[np.float32], *, prompt: str | None, final: bool, key: Hashable
    ) -> Timed | None:
        """``None`` means the job was superseded (coalesced partial)."""
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Timed | None] = loop.create_future()
        job = _Job(FINAL if final else PARTIAL, next(self._seq), key, audio, prompt, fut, loop)
        with self._cond:
            if self._closed:
                raise RuntimeError("scheduler closed")
            if (old := self._partials.pop(key, None)) is not None:
                old.cancelled = True
                loop.call_soon(_resolve, old.future, None)
            if not final:
                self._partials[key] = job
            heapq.heappush(self._heap, job)
            self._cond.notify()
        return await fut

    def _run(self) -> None:
        while True:
            with self._cond:
                while not self._heap and not self._closed:
                    self._cond.wait()
                if self._closed:
                    return
                job = heapq.heappop(self._heap)
                if job.cancelled:
                    continue
                if self._partials.get(job.key) is job:
                    del self._partials[job.key]
                self.busy = True
            started = time.monotonic()
            try:
                result = self.transcriber.transcribe(job.audio, prompt=job.prompt)
                done = time.monotonic()
                value: Timed | BaseException = Timed(
                    result, (started - job.queued_at) * 1000, (done - started) * 1000
                )
            except Exception as e:  # surfaced to the awaiting coroutine
                log.exception("transcription failed")
                value = e
            finally:
                self.busy = False
            job.loop.call_soon_threadsafe(_resolve, job.future, value)

    def close(self) -> None:
        with self._cond:
            self._closed = True
            for job in self._heap:
                job.loop.call_soon_threadsafe(_resolve, job.future, None)
            self._heap.clear()
            self._cond.notify_all()
        self._thread.join(timeout=5)
