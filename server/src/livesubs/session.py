"""One ``Session`` per WebSocket connection: ingest → VAD → segment pipeline."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

from livesubs import protocol as p
from livesubs.ingest import Ingest
from livesubs.metrics import SessionMetrics
from livesubs.vad import WINDOW_SAMPLES, Segmenter, SpeechProbModel, SpeechSegment, VadParams

log = logging.getLogger(__name__)

Emit = Callable[[p.ServerMessage], None]


class SegmentSink(Protocol):
    """What the session does with speech segments (VAD-only, or ASR + MT)."""

    def on_segment(self, session: Session, seg: SpeechSegment) -> None: ...

    def on_discontinuity(self, session: Session) -> None: ...

    async def aclose(self) -> None: ...


class VadOnlySink:
    """Emits an empty ``final`` per closed segment: enough to check the cuts by ear."""

    def on_segment(self, session: Session, seg: SpeechSegment) -> None:
        if seg.is_closed:
            t0, t1 = session.bounds(seg)
            session.emit(p.Final(seg_id=seg.seg_id, ja="", t0=t0, t1=t1, asr_ms=0.0))

    def on_discontinuity(self, session: Session) -> None:
        pass

    async def aclose(self) -> None:
        pass


class Session:
    def __init__(
        self,
        hello: p.Hello,
        *,
        vad: SpeechProbModel,
        vad_params: VadParams,
        sink: SegmentSink,
        emit: Emit,
        ring_seconds: float = 30.0,
        first_seg_id: int = 1,
    ) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.hello = hello
        self.emit = emit
        self.targets = list(hello.targets)
        self.show_partials = True
        self.ingest = Ingest(ring_seconds)
        self._vad = vad
        self._segmenter = Segmenter(vad_params, first_seg_id=first_seg_id)
        # Ahead mode (WP13): a hook that takes the captured frames for itself.
        self.frame_hook: Callable[[p.AudioFrame], bool] | None = None
        self._sink = sink
        self._vad_cursor: int | None = None
        self._params = vad_params
        self._quiet_windows = 0
        self.paused = False
        self.segments_closed = 0
        self.metrics = SessionMetrics(self.id)

    # ------------------------------------------------------------------ audio

    def audio(self, seg: SpeechSegment) -> NDArray[np.float32]:
        return self.ingest.ring.read(seg.start_idx, seg.end_idx)

    def bounds(self, seg: SpeechSegment) -> tuple[float, float]:
        """Segment bounds in ``media_time`` (seconds of the video)."""
        times = self.ingest.times
        return times.media_time(seg.start_idx), times.media_time(seg.end_idx)

    def speech_end_at(self, seg: SpeechSegment) -> float:
        """Arrival time of the last sample of the segment: latencies start here."""
        return self.ingest.times.arrival(seg.end_idx - 1)

    def on_frame(self, frame: p.AudioFrame) -> None:
        if self.frame_hook is not None and self.frame_hook(frame):
            return
        if self.paused:
            return
        expected = self.ingest.expected_idx
        huge_gap = frame.sample_idx - expected > self.ingest.ring.capacity
        if self._vad_cursor is not None and (frame.discontinuity or huge_gap):
            self._break(expected)
        accepted = self.ingest.accept(frame)
        if accepted is None:
            return
        if self._vad_cursor is None or huge_gap:
            self._vad_cursor = accepted.start_idx
            self._segmenter.restart_at(accepted.start_idx)
        self._run_vad()

    def _run_vad(self) -> None:
        assert self._vad_cursor is not None
        ring = self.ingest.ring
        while self._vad_cursor + WINDOW_SAMPLES <= ring.end_idx:
            end = self._vad_cursor + WINDOW_SAMPLES
            prob = self._vad.prob(ring.read(self._vad_cursor, end))
            self._vad_cursor = end
            self._maybe_reset_vad(prob)
            for seg in self._segmenter.push(end, prob):
                self._dispatch(seg)

    def _maybe_reset_vad(self, prob: float) -> None:
        params = self._params
        self._quiet_windows = self._quiet_windows + 1 if prob < params.reset_below else 0
        quiet_s = self._quiet_windows * WINDOW_SAMPLES / p.SAMPLE_RATE
        if params.reset_after_s and quiet_s >= params.reset_after_s and not self._segmenter.is_open:
            self._vad.reset()
            self._quiet_windows = 0

    def _dispatch(self, seg: SpeechSegment) -> None:
        if seg.is_closed:
            self.segments_closed += 1
        self._sink.on_segment(self, seg)

    def _break(self, at_idx: int) -> None:
        """Close the open segment at ``at_idx`` and forget the VAD state."""
        for seg in self._segmenter.flush(at_idx):
            self._dispatch(seg)
        self._vad.reset()
        self._sink.on_discontinuity(self)

    # ------------------------------------------------------------------ control

    def on_pause(self) -> None:
        if not self.paused and self._vad_cursor is not None:
            self._break(self.ingest.expected_idx)
        self.paused = True

    def on_resume(self) -> None:
        if self.paused:
            # Whatever the client skipped while paused is not silence to segment:
            # restart the clock at the next frame.
            self.ingest.restart()
            self._vad_cursor = None
        self.paused = False

    def on_config(self, msg: p.Config) -> None:
        if msg.targets is not None:
            self.targets = list(msg.targets)
        if msg.show_partials is not None:
            self.show_partials = msg.show_partials

    async def close(self) -> None:
        if self._vad_cursor is not None:
            self._break(self.ingest.expected_idx)
        await self._sink.aclose()
