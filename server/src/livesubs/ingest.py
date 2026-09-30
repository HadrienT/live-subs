"""Per-session audio ingestion: ring buffer indexed by ``sample_idx`` and the
``sample_idx → media_time`` table used to express segment bounds in video time."""

from __future__ import annotations

import bisect
import logging
import time
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from livesubs.protocol import SAMPLE_RATE, AudioFrame

log = logging.getLogger(__name__)


class AudioRing:
    """Float32 ring buffer holding the last ``capacity`` samples of the session."""

    def __init__(self, seconds: float = 30.0) -> None:
        self.capacity = int(seconds * SAMPLE_RATE)
        self._buf = np.zeros(self.capacity, dtype=np.float32)
        self.end_idx = 0  # absolute index one past the last written sample

    @property
    def start_idx(self) -> int:
        return max(0, self.end_idx - self.capacity)

    def reset_to(self, idx: int) -> None:
        self.end_idx = idx

    def write(self, samples: NDArray[np.float32]) -> None:
        n = len(samples)
        if n >= self.capacity:
            samples = samples[-self.capacity :]
            self.end_idx += n - self.capacity
            n = self.capacity
        pos = self.end_idx % self.capacity
        first = min(n, self.capacity - pos)
        self._buf[pos : pos + first] = samples[:first]
        self._buf[: n - first] = samples[first:]
        self.end_idx += n

    def read(self, start: int, end: int) -> NDArray[np.float32]:
        """Copy of samples ``[start, end)``, clipped to what is still in the buffer."""
        start = max(start, self.start_idx)
        end = min(end, self.end_idx)
        if end <= start:
            return np.zeros(0, dtype=np.float32)
        return self._buf[np.arange(start, end) % self.capacity]


class TimeMap:
    """One ``(sample_idx, media_time, arrival)`` entry per frame; linear inside a frame.

    ``arrival`` is ``time.monotonic()`` when the frame reached the server: the
    origin of every server-side latency (WP09)."""

    def __init__(self, keep_seconds: float = 120.0) -> None:
        self._idx: list[int] = []
        self._mt: list[float] = []
        self._arrival: list[float] = []
        self._keep = int(keep_seconds * SAMPLE_RATE)

    def add(self, sample_idx: int, media_time: float, arrival: float | None = None) -> None:
        self._idx.append(sample_idx)
        self._mt.append(media_time)
        self._arrival.append(time.monotonic() if arrival is None else arrival)
        if len(self._idx) > 64 and sample_idx - self._idx[0] > self._keep:
            cut = bisect.bisect_left(self._idx, sample_idx - self._keep)
            del self._idx[:cut]
            del self._mt[:cut]
            del self._arrival[:cut]

    def arrival(self, sample_idx: int) -> float:
        """When the frame holding ``sample_idx`` arrived (now if unknown)."""
        if not self._idx:
            return time.monotonic()
        i = max(bisect.bisect_right(self._idx, sample_idx) - 1, 0)
        return self._arrival[i]

    def media_time(self, sample_idx: int) -> float:
        if not self._idx:
            return sample_idx / SAMPLE_RATE
        i = bisect.bisect_right(self._idx, sample_idx) - 1
        i = max(i, 0)
        return self._mt[i] + (sample_idx - self._idx[i]) / SAMPLE_RATE


@dataclass(frozen=True, slots=True)
class Accepted:
    """Result of ``Ingest.accept``: new samples start at ``start_idx``."""

    start_idx: int
    samples: NDArray[np.float32]
    discontinuity: bool
    gap: int  # samples of silence inserted before this frame


class Ingest:
    """Orders frames: drops late / duplicate ones, fills gaps with silence."""

    def __init__(self, ring_seconds: float = 30.0) -> None:
        self.ring = AudioRing(ring_seconds)
        self.times = TimeMap()
        self._expected: int | None = None
        self._high = 0  # never accept anything before this, even after a restart
        self.dropped = 0
        self.gap_samples = 0

    @property
    def expected_idx(self) -> int:
        return self._expected or 0

    def restart(self) -> None:
        """The next frame starts a new stretch of audio (after a pause)."""
        self._expected = None

    def accept(self, frame: AudioFrame) -> Accepted | None:
        if frame.sample_idx < self._high:
            self.dropped += 1
            return None
        if self._expected is None:
            self._expected = frame.sample_idx
            self.ring.reset_to(frame.sample_idx)
        if frame.sample_idx < self._expected:
            self.dropped += 1
            log.debug("dropping late/duplicate frame at %d", frame.sample_idx)
            return None
        gap = frame.sample_idx - self._expected
        discontinuity = frame.discontinuity
        if gap > self.ring.capacity:
            # Far beyond anything we could still segment: start over from this frame.
            log.warning("gap of %.1f s: treating as discontinuity", gap / SAMPLE_RATE)
            self.ring.reset_to(frame.sample_idx)
            discontinuity, gap = True, 0
        elif gap:
            self.gap_samples += gap
            log.warning(
                "gap of %d ms before sample %d", gap * 1000 // SAMPLE_RATE, frame.sample_idx
            )
            self.ring.write(np.zeros(gap, dtype=np.float32))
        samples = frame.pcm.astype(np.float32) / 32768.0
        self.times.add(frame.sample_idx, frame.media_time)
        self.ring.write(samples)
        self._expected = self._high = frame.sample_idx + len(samples)
        return Accepted(frame.sample_idx, samples, discontinuity, gap)
