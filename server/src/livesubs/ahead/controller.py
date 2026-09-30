"""Ahead-of-live controller (WP13).

The *outer* session is the extension's connection: in ahead mode its captured
frames are only used to align the two timelines. The *inner* session runs the
usual VAD → ASR → MT pipeline on the audio the server pulls from the live edge,
and its messages are re-timed into video time before reaching the extension.
Since the player is kept a few seconds behind the live, subtitles arrive before
their sentence is heard; the overlay shows each one at its ``t0``.

If the live cannot be pulled, or the two recordings cannot be aligned within
``fail_after_s``, the controller steps aside: ``ahead_status{failed}`` and the
outer session transcribes the capture as usual (automatic fallback).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import deque
from collections.abc import Callable

import numpy as np
from numpy.typing import NDArray

from livesubs import protocol as p
from livesubs.ahead.align import find_lag
from livesubs.ahead.source import PcmSource, Resolver
from livesubs.ingest import AudioRing
from livesubs.session import Session

log = logging.getLogger(__name__)

SR = p.SAMPLE_RATE
INNER_FIRST_SEG_ID = 1_000_001  # never collides with the outer session's ids
REPLAY_GIVE_UP_S = 15.0  # reading this far behind the player: start over from it


class AheadController:
    def __init__(
        self,
        outer: Session,
        # (emit, replay) → inner session; replay: no merging of late translations
        make_inner: Callable[[Callable[[p.ServerMessage], None], bool], Session],
        resolver: Resolver,
        *,
        replay_lead_s: float = 60.0,
        capture_window_s: float = 15.0,
        max_lag_s: float = 120.0,
        retry_s: float = 2.0,
        realign_s: float = 30.0,
        fail_after_s: float = 45.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.outer = outer
        self._make_inner = make_inner
        self._gen = 0  # bumped on each (re)start: late messages of an old inner are dropped
        self.inner = self._new_inner()
        self.resolver = resolver
        self.source: PcmSource | None = None
        self.replay: bool | None = None  # unknown until probed
        self.replay_lead_s = replay_lead_s
        self._replay_start = 0.0
        self._player_pos: float | None = None
        self._restart = asyncio.Event()
        self._player_moved = asyncio.Event()  # set by every captured frame
        self._last_status = -1e9
        self.capture_window_s = capture_window_s
        self.retry_s = retry_s
        self.realign_s = realign_s
        self.fail_after_s = fail_after_s
        self.clock = clock
        self.state: p.AheadState = "aligning"
        self.offset_s: float | None = None
        self._started = clock()
        self._last_try = -1e9
        # capture: (media_time of first sample, pcm) per frame since the last discontinuity
        self._capture: deque[tuple[float, NDArray[np.float32]]] = deque(
            maxlen=int(capture_window_s * SR / p.FRAME_SAMPLES) + 1
        )
        self._source = AudioRing(max_lag_s + capture_window_s)
        self._source_total = 0  # samples pulled since the start
        self._task: asyncio.Task[None] | None = None
        outer.frame_hook = self.on_capture_frame

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        self._emit_status()
        self._task = asyncio.get_running_loop().create_task(self._pull())

    async def aclose(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        if self.source is not None:
            await self.source.aclose()
        await self.inner.close()

    def _new_inner(self, replay: bool = False) -> Session:
        self._gen += 1
        gen = self._gen
        return self._make_inner(lambda msg: self._emit_inner(msg, gen), replay)

    async def _pull(self) -> None:
        try:
            self.replay = not await self.resolver.probe()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._fail(f"cannot open the video: {e}")
            return
        if self.replay:
            await self._pull_replay()
        else:
            await self._pull_live()

    async def _pull_live(self) -> None:
        try:
            self.source = self.resolver.live_source()
            async for chunk in self.source.chunks():
                pcm = np.clip(chunk * 32768, -32768, 32767).astype(np.int16)
                frame = p.AudioFrame(self._source_total, self._source_total / SR, pcm)
                self._append_source(chunk)
                self.inner.on_frame(frame)
            self._fail("the live stream ended")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("ahead source failed: %s", e)
            self._fail(f"cannot pull the live: {e}")

    async def _pull_replay(self) -> None:
        """Replay: read from the player's position, up to ``replay_lead_s`` ahead of
        it, and start over wherever the player jumps. Video time is the replay's own
        timeline, so there is nothing to align: offset = −start."""
        while self._player_pos is None:
            await self._wait_player()
        while True:
            self._restart.clear()
            start = self._player_pos
            if self.source is not None:
                await self.source.aclose()
            await self.inner.close()
            self.inner = self._new_inner(replay=True)
            self._source_total = 0
            try:
                self.source = self.resolver.replay_source(start)
                # a DASH replay starts at a fragment boundary, slightly before `start`
                start = self.source.actual_start_s
                self._replay_start = start
                self.offset_s = -start
                self.state = "aligned"
                self._emit_status()
                async for chunk in self.source.chunks():
                    # far enough ahead: wait for the player (stays put while paused)
                    while self._ahead_of_player() > self.replay_lead_s:
                        if self._restart.is_set():
                            break
                        await self._wait_player()
                    if self._restart.is_set():
                        break
                    pcm = np.clip(chunk * 32768, -32768, 32767).astype(np.int16)
                    frame = p.AudioFrame(self._source_total, self._source_total / SR, pcm)
                    self._source_total += len(chunk)
                    self.inner.on_frame(frame)
                else:
                    self.inner.on_pause()  # end of the replay: close the last sentence
                    await self._restart.wait()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("replay source failed: %s", e)
                self._fail(f"cannot read the replay: {e}")
                return

    async def _wait_player(self) -> None:
        self._player_moved.clear()
        await self._player_moved.wait()

    def _ahead_of_player(self) -> float:
        if self._player_pos is None:
            return 0.0
        return self._replay_start + self._source_total / SR - self._player_pos

    def _append_source(self, chunk: NDArray[np.float32]) -> None:
        self._source.write(chunk)
        self._source_total += len(chunk)

    # ------------------------------------------------------------------ alignment

    def on_capture_frame(self, frame: p.AudioFrame) -> bool:
        """Outer session hook: True = consumed (not transcribed from the capture)."""
        if self.state == "failed":
            return False
        self._player_pos = frame.media_time
        self._player_moved.set()
        if self.replay is None:
            return True  # still probing: nothing to transcribe from yet
        if self.replay:
            self._follow_player(frame.media_time, frame.discontinuity)
            return True
        if frame.discontinuity:
            self._capture.clear()
        self._capture.append((frame.media_time, frame.pcm.astype(np.float32) / 32768.0))
        self._maybe_align()
        return True

    def _maybe_align(self) -> None:
        now = self.clock()
        wait = self.retry_s if self.state == "aligning" else self.realign_s
        if now - self._last_try < wait:
            return
        if len(self._capture) < self._capture.maxlen - 1:  # type: ignore[operator]
            self._check_timeout(now)
            return
        self._last_try = now
        capture = np.concatenate([pcm for _, pcm in self._capture])
        m0 = self._capture[0][0]
        ring = self._source
        source = ring.read(ring.start_idx, ring.end_idx)
        start_time = ring.start_idx / SR
        a = find_lag(capture, source, max_lag_s=len(source) / SR)
        if a.ok:
            offset = start_time + a.lag_s - m0
            first = self.state != "aligned"
            if not first and self.offset_s is not None and abs(offset - self.offset_s) > 0.2:
                log.info("ahead offset moved %.2f → %.2f s", self.offset_s, offset)
            self.offset_s = offset
            self.state = "aligned"
            self._emit_status()
        else:
            log.debug("ahead: no alignment yet (conf %.2f, sharp %.2f)", a.confidence, a.sharpness)
            self._check_timeout(now)

    def _follow_player(self, media_time: float, jumped: bool) -> None:
        """Replay: restart the reading if the player left the prefetched window.

        Only on a real jump (the extension flags seeks), or when hopelessly behind:
        the first audio takes a second or more to arrive, and meanwhile the player
        naturally moves past what has been read (that is not a seek)."""
        read_to = self._replay_start + self._source_total / SR
        outside = media_time < self._replay_start - 1.0 or media_time > read_to + 1.0
        if (jumped and outside) or media_time > read_to + REPLAY_GIVE_UP_S:
            log.info("ahead: player jumped to %.1f s, reading from there", media_time)
            self._restart.set()
            self._player_moved.set()
        elif self.clock() - self._last_status > 10.0:
            self._emit_status()

    def _check_timeout(self, now: float) -> None:
        if self.state == "aligning" and now - self._started > self.fail_after_s:
            self._fail("could not align the live with the player")

    @property
    def lead_s(self) -> float | None:
        if self.replay:
            return self._ahead_of_player()
        if self.offset_s is None or not self._capture:
            return None
        video_now = self._capture[-1][0] + p.FRAME_SAMPLES / SR
        return self._source_total / SR - self.offset_s - video_now

    def _emit_status(self, message: str | None = None) -> None:
        self._last_status = self.clock()
        lead = self.lead_s
        self.outer.emit(
            p.AheadStatus(
                state=self.state,
                offset_s=round(self.offset_s, 3) if self.offset_s is not None else None,
                lead_s=round(lead, 2) if lead is not None else None,
                message=message,
            )
        )

    def _fail(self, why: str) -> None:
        if self.state == "failed":
            return
        log.warning("ahead mode off: %s", why)
        self.state = "failed"
        self.outer.frame_hook = None  # the capture is transcribed again from now on
        self._emit_status(why)

    # ------------------------------------------------------------------ inner → client

    def _emit_inner(self, msg: p.ServerMessage, gen: int) -> None:
        if gen != self._gen:
            return  # from an inner session replaced after a jump
        if self.state != "aligned" or self.offset_s is None:
            if isinstance(msg, p.Error):
                self.outer.emit(msg)
            return  # nothing can be placed in video time yet
        if isinstance(msg, p.Partial | p.Final):
            msg = msg.model_copy(
                update={"t0": msg.t0 - self.offset_s, "t1": msg.t1 - self.offset_s}
            )
        if isinstance(msg, p.Stats):
            return  # the outer session reports its own
        self.outer.emit(msg)
