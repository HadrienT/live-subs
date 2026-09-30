"""Server-side latency breakdown (WP09 §2).

Every latency is measured from the end of speech, i.e. when the frame holding
the end of the VAD segment reached the server, and broken down into:

    vad_wait   end of speech → segment closed (min_silence_ms of silence)
    queue      waiting for the GPU thread
    asr        decoding
    ja         end of speech → final sent            (= vad_wait + queue + asr)
    mt_first   translation request → first token
    mt         translation request → last token
    en         end of speech → translation sent

One JSON line per segment on the ``livesubs.metrics`` logger; rolling p50/p95
over 5 minutes in the ``stats`` message.
"""

from __future__ import annotations

import json
import logging
import time
from collections import deque

import numpy as np

from livesubs import protocol as p

log = logging.getLogger("livesubs.metrics")

WINDOW_S = 300.0


class Rolling:
    def __init__(self, window_s: float = WINDOW_S) -> None:
        self.window_s = window_s
        self._samples: deque[tuple[float, float]] = deque()

    def add(self, value: float, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self._samples.append((now, value))
        self._prune(now)

    def _prune(self, now: float) -> None:
        while self._samples and self._samples[0][0] < now - self.window_s:
            self._samples.popleft()

    def percentile(self, q: float, now: float | None = None) -> float | None:
        self._prune(time.monotonic() if now is None else now)
        if not self._samples:
            return None
        return round(float(np.percentile([v for _, v in self._samples], q)), 1)

    def __len__(self) -> int:
        return len(self._samples)


class SessionMetrics:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.asr = Rolling()
        self.ja = Rolling()
        self.en = Rolling()
        self.mt_first = Rolling()

    def on_final(
        self, seg_id: int, *, vad_wait_ms: float, queue_ms: float, asr_ms: float, ja_ms: float
    ) -> None:
        self.asr.add(asr_ms)
        self.ja.add(ja_ms)
        self._log(seg_id, vad_wait_ms=vad_wait_ms, queue_ms=queue_ms, asr_ms=asr_ms, ja_ms=ja_ms)

    def on_translation(
        self, seg_id: int, *, mt_first_ms: float, mt_ms: float, en_ms: float
    ) -> None:
        self.mt_first.add(mt_first_ms)
        self.en.add(en_ms)
        self._log(seg_id, mt_first_ms=mt_first_ms, mt_ms=mt_ms, en_ms=en_ms)

    def _log(self, seg_id: int, **fields: float) -> None:
        if log.isEnabledFor(logging.INFO):
            rounded = {k: round(v, 1) for k, v in fields.items()}
            log.info(json.dumps({"session": self.session_id, "seg_id": seg_id, **rounded}))

    def stats(self, *, queue_depth: int, gpu_busy: bool) -> p.Stats:
        return p.Stats(
            queue_depth=queue_depth,
            gpu_busy=gpu_busy,
            asr_ms_p50=self.asr.percentile(50),
            asr_ms_p95=self.asr.percentile(95),
            ja_ms_p50=self.ja.percentile(50),
            ja_ms_p95=self.ja.percentile(95),
            en_ms_p50=self.en.percentile(50),
            en_ms_p95=self.en.percentile(95),
            mt_first_token_ms_p50=self.mt_first.percentile(50),
        )
