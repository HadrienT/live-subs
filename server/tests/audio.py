"""Synthetic audio helpers for tests: 'speech' is band-limited noise at -20 dBFS."""

from __future__ import annotations

from collections.abc import Iterator

import numpy as np
from numpy.typing import NDArray

from livesubs import protocol as p

SR = p.SAMPLE_RATE


def speech(seconds: float, seed: int = 0) -> NDArray[np.float32]:
    rng = np.random.default_rng(seed)
    return (rng.standard_normal(int(seconds * SR)) * 0.1).astype(np.float32)


def silence(seconds: float) -> NDArray[np.float32]:
    return np.zeros(int(seconds * SR), dtype=np.float32)


def concat(*parts: NDArray[np.float32]) -> NDArray[np.float32]:
    return np.concatenate(parts).astype(np.float32)


def to_frames(
    audio: NDArray[np.float32], *, start_idx: int = 0, media_t0: float = 0.0
) -> Iterator[p.AudioFrame]:
    pcm = np.clip(audio * 32768, -32768, 32767).astype(np.int16)
    for off in range(0, len(pcm), p.FRAME_SAMPLES):
        chunk = pcm[off : off + p.FRAME_SAMPLES]
        yield p.AudioFrame(start_idx + off, media_t0 + off / SR, chunk)
