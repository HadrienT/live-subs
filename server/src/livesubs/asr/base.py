"""The ``Transcriber`` interface and its result type."""

from __future__ import annotations

import zlib
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True, slots=True)
class Word:
    text: str
    start: float
    end: float
    probability: float


@dataclass(frozen=True, slots=True)
class AsrResult:
    text: str
    words: list[Word] = field(default_factory=list)
    no_speech_prob: float = 0.0
    avg_logprob: float = 0.0
    compression_ratio: float = 1.0


class Transcriber(Protocol):
    """Blocking and GPU-bound: only ever called from the single inference thread."""

    name: str

    def transcribe(self, audio: NDArray[np.float32], *, prompt: str | None) -> AsrResult: ...


def compression_ratio(text: str) -> float:
    """Same measure as Whisper: high values mean repetitive, looping output."""
    raw = text.encode("utf-8")
    return len(raw) / len(zlib.compress(raw)) if raw else 1.0
