"""Voice activity detection: Silero VAD v5 (ONNX, CPU) and the speech segmenter.

The segmenter is independent of the model: it consumes one speech probability
per 32 ms window and cuts the stream into ``SpeechSegment``s, in absolute sample
indices (the session clock, see ``protocol.AudioFrame.sample_idx``).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from importlib import resources
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

from livesubs.protocol import SAMPLE_RATE

WINDOW_SAMPLES = 512  # Silero v5 at 16 kHz: 32 ms


class SpeechProbModel(Protocol):
    """Stateful per-stream model: one probability per ``WINDOW_SAMPLES`` window."""

    def prob(self, window: NDArray[np.float32]) -> float: ...

    def reset(self) -> None: ...


class SileroVad:
    """Silero VAD v5. The ONNX session is shared; the recurrent state is per instance."""

    _CONTEXT = 64
    _session: object | None = None

    def __init__(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, self._CONTEXT), dtype=np.float32)
        self._sr = np.array(SAMPLE_RATE, dtype=np.int64)

    @classmethod
    def _get_session(cls) -> object:
        if cls._session is None:
            import onnxruntime as ort

            opts = ort.SessionOptions()
            opts.inter_op_num_threads = 1
            opts.intra_op_num_threads = 1
            model = resources.files("livesubs.data").joinpath("silero_vad.onnx").read_bytes()
            cls._session = ort.InferenceSession(
                model, sess_options=opts, providers=["CPUExecutionProvider"]
            )
        return cls._session

    def prob(self, window: NDArray[np.float32]) -> float:
        x = np.concatenate([self._context, window.reshape(1, -1)], axis=1)
        session = self._get_session()
        out, state = session.run(  # type: ignore[attr-defined]
            None, {"input": x, "state": self._state, "sr": self._sr}
        )
        self._state = state
        self._context = x[:, -self._CONTEXT :]
        return float(out[0][0])

    def reset(self) -> None:
        self._state[:] = 0
        self._context[:] = 0


class EnergyVad:
    """Deterministic stand-in for tests: 'speech' is any window louder than -40 dBFS."""

    def __init__(self, threshold_dbfs: float = -40.0) -> None:
        self._threshold = 10 ** (threshold_dbfs / 20)

    def prob(self, window: NDArray[np.float32]) -> float:
        rms = float(np.sqrt(np.mean(np.square(window, dtype=np.float64))))
        return 1.0 if rms > self._threshold else 0.0

    def reset(self) -> None:
        pass


@dataclass(frozen=True, slots=True)
class VadParams:
    threshold: float = 0.5
    min_speech_ms: int = 250
    min_silence_ms: int = 400
    max_segment_s: float = 12.0
    pad_ms: int = 200
    update_interval_s: float = 1.0

    @property
    def neg_threshold(self) -> float:
        return max(self.threshold - 0.15, 0.01)


@dataclass(frozen=True, slots=True)
class SpeechSegment:
    """``end_idx`` is the current end of audio while the segment is open."""

    seg_id: int
    start_idx: int
    end_idx: int
    is_closed: bool

    @property
    def duration_s(self) -> float:
        return (self.end_idx - self.start_idx) / SAMPLE_RATE


class Segmenter:
    """Turns window probabilities into open / updated / closed speech segments.

    Feed it with ``push(window_end_idx, prob)`` for consecutive windows. It returns
    the events of that window: an open segment is re-published every
    ``update_interval_s`` of speech (this paces the ``partial``s), and published
    once more with ``is_closed=True`` when it ends.
    """

    def __init__(self, params: VadParams, *, first_seg_id: int = 1) -> None:
        self.p = params
        self._next_id = first_seg_id
        ms = SAMPLE_RATE // 1000
        self._min_speech = params.min_speech_ms * ms
        self._min_silence = params.min_silence_ms * ms
        self._max_len = int(params.max_segment_s * SAMPLE_RATE)
        self._pad = params.pad_ms * ms
        self._update = int(params.update_interval_s * SAMPLE_RATE)
        self._history: deque[tuple[int, float]] = deque(maxlen=int(2 * SAMPLE_RATE / 512) + 1)
        self._floor = 0  # no segment may start before this index (previous end)
        self._reset_state()

    def _reset_state(self) -> None:
        self._candidate: int | None = None  # start of a speech run not yet long enough
        self._open_id: int | None = None
        self._start = 0
        self._temp_end: int | None = None  # start of the current silence run
        self._last_published = 0

    @property
    def is_open(self) -> bool:
        return self._open_id is not None

    def push(self, end_idx: int, prob: float) -> list[SpeechSegment]:
        start_idx = end_idx - WINDOW_SAMPLES
        self._history.append((end_idx, prob))
        events: list[SpeechSegment] = []
        if self._open_id is None:
            if prob >= self.p.threshold:
                if self._candidate is None:
                    self._candidate = start_idx
                if end_idx - self._candidate >= self._min_speech:
                    self._open(max(self._candidate - self._pad, self._floor), end_idx)
                    self._candidate = None
            elif prob < self.p.neg_threshold:
                self._candidate = None
            return events

        if prob >= self.p.threshold:
            self._temp_end = None
        elif prob < self.p.neg_threshold and self._temp_end is None:
            self._temp_end = start_idx

        if self._temp_end is not None and end_idx - self._temp_end >= self._min_silence:
            events.append(self._close(min(self._temp_end + self._pad, end_idx)))
            return events

        if end_idx - self._start >= self._max_len:
            cut = self._lowest_prob_point(end_idx)
            events.append(self._close(cut))
            self._open(cut, end_idx)
            # speech continues: the new segment is already open

        if end_idx - self._last_published >= self._update:
            self._last_published = end_idx
            assert self._open_id is not None
            events.append(SpeechSegment(self._open_id, self._start, end_idx, False))
        return events

    def restart_at(self, idx: int) -> None:
        """Audio restarts at ``idx`` (after a pause or a jump): nothing may start before."""
        self._floor = idx

    def flush(self, end_idx: int) -> list[SpeechSegment]:
        """Close the open segment now (pause, discontinuity, end of stream)."""
        events: list[SpeechSegment] = []
        if self._open_id is not None:
            end = self._temp_end + self._pad if self._temp_end is not None else end_idx
            events.append(self._close(min(end, end_idx)))
        self._reset_state()
        self._history.clear()
        self._floor = end_idx
        return events

    def _open(self, start: int, now: int) -> None:
        self._open_id = self._next_id
        self._next_id += 1
        self._start = start
        self._temp_end = None
        self._last_published = now

    def _close(self, end: int) -> SpeechSegment:
        assert self._open_id is not None
        seg = SpeechSegment(self._open_id, self._start, end, True)
        self._floor = end
        self._open_id = None
        self._temp_end = None
        return seg

    def _lowest_prob_point(self, end_idx: int) -> int:
        """Window boundary with the lowest probability in the last 2 s (forced cut)."""
        lo = end_idx - 2 * SAMPLE_RATE
        candidates = [(p, idx) for idx, p in self._history if idx > max(lo, self._start)]
        if not candidates:
            return end_idx
        _, idx = min(candidates)
        return idx - WINDOW_SAMPLES // 2
