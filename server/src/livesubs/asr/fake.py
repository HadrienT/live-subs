"""``FakeTranscriber``: scripted output for tests and GPU-less development."""

from __future__ import annotations

import time
from collections.abc import Callable

import numpy as np
from numpy.typing import NDArray

from livesubs.asr.base import AsrResult, compression_ratio

SR = 16_000
_TEXT = "きょうはみんなであたらしいゲームをやっていきたいとおもいます" * 10


def growing_text(audio: NDArray[np.float32]) -> str:
    """Six characters per second of audio, always the same sentence: partials grow."""
    return _TEXT[: int(len(audio) / SR * 6)]


class FakeTranscriber:
    name = "fake"

    def __init__(
        self,
        script: Callable[[NDArray[np.float32]], str] = growing_text,
        *,
        delay_s: float = 0.0,
        avg_logprob: float = -0.05,
        no_speech_prob: float = 0.02,
    ) -> None:
        self.script = script
        self.delay_s = delay_s
        self.avg_logprob = avg_logprob
        self.no_speech_prob = no_speech_prob
        self.calls: list[tuple[float, str | None]] = []  # (seconds of audio, prompt)

    def transcribe(self, audio: NDArray[np.float32], *, prompt: str | None) -> AsrResult:
        self.calls.append((len(audio) / SR, prompt))
        if self.delay_s:
            time.sleep(self.delay_s)
        text = self.script(audio)
        return AsrResult(
            text=text,
            no_speech_prob=self.no_speech_prob,
            avg_logprob=self.avg_logprob,
            compression_ratio=compression_ratio(text),
        )

    def warmup(self) -> None:
        pass
