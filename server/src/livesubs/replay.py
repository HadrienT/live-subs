"""Replay an audio file into a running server exactly like the extension does.

Used by ``tools/replay.py`` (``just replay``) and by the golden end-to-end tests.
Latencies are measured from the moment the frame holding the end of speech
(``t1``) was sent, i.e. relative to the end of the VAD segment.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from livesubs import protocol as p

SR = p.SAMPLE_RATE
FFMPEG_OUT = ("-ac", "1", "-ar", str(SR), "-f", "s16le", "-")


def load_audio(source: str) -> NDArray[np.float32]:
    """Mono float32 at 16 kHz. WAV is read natively; anything else goes through ffmpeg."""
    if source.lower().endswith(".wav") and Path(source).exists():
        with wave.open(source, "rb") as w:
            rate, channels, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
            raw = w.readframes(w.getnframes())
        if width != 2:
            raise ValueError(f"{source}: only 16-bit PCM WAV is read natively")
        audio = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        audio = audio.reshape(-1, channels).mean(axis=1)
        if rate != SR:
            audio = resample(audio, rate, SR)
        return audio.astype(np.float32)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg is needed for non-WAV input")
    out = subprocess.run(
        [ffmpeg, "-nostdin", "-loglevel", "error", "-i", source, *FFMPEG_OUT],
        check=True,
        capture_output=True,
    ).stdout
    return np.frombuffer(out, dtype="<i2").astype(np.float32) / 32768.0


def resample(audio: NDArray[np.float32], src: int, dst: int) -> NDArray[np.float32]:
    """Windowed-sinc low-pass + linear interpolation; good enough for test input."""
    if src == dst:
        return audio
    if dst < src:
        cutoff = 0.45 * dst / src
        taps = np.arange(-32, 33)
        h = np.sinc(2 * cutoff * taps) * np.blackman(len(taps))
        audio = np.convolve(audio, h / h.sum(), mode="same").astype(np.float32)
    t = np.arange(0, len(audio) * dst / src) * src / dst
    return np.interp(t, np.arange(len(audio)), audio).astype(np.float32)


def write_wav(path: Path, audio: NDArray[np.float32]) -> None:
    pcm = np.clip(audio * 32768, -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def percentile(values: list[float], q: float) -> float | None:
    return float(np.percentile(values, q)) if values else None


@dataclass
class ReplayResult:
    messages: list[dict[str, Any]] = field(default_factory=list)
    ja_latency_ms: list[float] = field(default_factory=list)
    en_latency_ms: list[float] = field(default_factory=list)
    en_first_token_ms: list[float] = field(default_factory=list)

    def finals(self) -> list[dict[str, Any]]:
        return [m for m in self.messages if m["type"] == "final"]

    def translations(self) -> dict[int, str]:
        return {m["seg_id"]: m["en"] for m in self.messages if m["type"] == "translation"}

    def summary(self) -> dict[str, Any]:
        return {
            "segments": len(self.finals()),
            "translations": len(self.translations()),
            "ja_ms_p50": percentile(self.ja_latency_ms, 50),
            "ja_ms_p95": percentile(self.ja_latency_ms, 95),
            "en_ms_p50": percentile(self.en_latency_ms, 50),
            "en_ms_p95": percentile(self.en_latency_ms, 95),
            "en_first_token_ms_p50": percentile(self.en_first_token_ms, 50),
            "errors": [m for m in self.messages if m["type"] == "error"],
        }


async def replay(
    url: str,
    audio: NDArray[np.float32],
    *,
    speed: float = 1.0,
    video_id: str = "replay",
    channel_id: str | None = None,
    token: str | None = None,
    media_offset: float = 0.0,
    drain_s: float = 15.0,
    on_message: Any = None,
) -> ReplayResult:
    """Send ``audio`` as 100 ms frames (``speed`` × real time, 0 = as fast as possible),
    then ``pause`` to close the last segment and wait until the server goes quiet."""
    from websockets.asyncio.client import connect

    result = ReplayResult()
    n_frames = (len(audio) + p.FRAME_SAMPLES - 1) // p.FRAME_SAMPLES
    sent_at = np.full(n_frames, np.nan)
    first_delta: dict[int, float] = {}
    pcm = np.clip(audio * 32768, -32768, 32767).astype(np.int16)

    def sent_time(media_t: float) -> float:
        i = int((media_t - media_offset) * SR) // p.FRAME_SAMPLES
        return float(sent_at[min(max(i, 0), n_frames - 1)])

    final_t1: dict[int, float] = {}
    last_rx = time.monotonic()

    async with connect(url, max_size=2**22) as ws:
        hello = p.Hello(
            protocol_version=p.PROTOCOL_VERSION,
            video_id=video_id,
            channel_id=channel_id,
            token=token,
        )
        await ws.send(p.dump(hello))
        ready = json.loads(await ws.recv())
        result.messages.append(ready)
        if ready["type"] != "ready":
            raise RuntimeError(f"server refused the session: {ready}")

        async def receiver() -> None:
            nonlocal last_rx
            async for raw in ws:
                now = time.monotonic()
                last_rx = now
                msg = json.loads(raw)
                result.messages.append(msg)
                kind, seg = msg["type"], msg.get("seg_id")
                if kind == "final":
                    final_t1[seg] = msg["t1"]
                    result.ja_latency_ms.append((now - sent_time(msg["t1"])) * 1000)
                elif kind == "translation_delta" and seg not in first_delta and seg in final_t1:
                    first_delta[seg] = now
                    result.en_first_token_ms.append((now - sent_time(final_t1[seg])) * 1000)
                elif kind == "translation" and seg in final_t1:
                    result.en_latency_ms.append((now - sent_time(final_t1[seg])) * 1000)
                if on_message is not None:
                    on_message(msg, now)

        rx = asyncio.create_task(receiver())
        start = time.monotonic()
        for i in range(n_frames):
            if speed > 0:
                due = start + i * p.FRAME_SAMPLES / SR / speed
                if (delay := due - time.monotonic()) > 0:
                    await asyncio.sleep(delay)
            chunk = pcm[i * p.FRAME_SAMPLES : (i + 1) * p.FRAME_SAMPLES]
            idx = i * p.FRAME_SAMPLES
            frame = p.AudioFrame(idx, media_offset + idx / SR, chunk)
            await ws.send(p.encode_frame(frame))
            sent_at[i] = time.monotonic()
        await ws.send(p.dump(p.Pause()))
        last_rx = time.monotonic()
        while time.monotonic() - last_rx < min(drain_s, 3.0) or (  # noqa: ASYNC110
            _pending(result) and time.monotonic() - last_rx < drain_s
        ):
            await asyncio.sleep(0.1)
        rx.cancel()
    return result


def _pending(result: ReplayResult) -> bool:
    """A final whose translation has not arrived yet (only if the server translates)."""
    ready = result.messages[0]
    if not ready.get("mt_model"):
        return False
    done = set(result.translations())
    for m in result.messages:
        if m["type"] == "translation":
            done.update(m.get("merged", []))
        if m["type"] == "error" and m.get("seg_id") is not None:
            done.add(m["seg_id"])
    return any(f["seg_id"] not in done and f["ja"] for f in result.finals())
