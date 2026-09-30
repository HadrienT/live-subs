"""Aligning two recordings of the same live: what the extension captured (in video
time) and what the server pulled (in its own stream time).

Envelope cross-correlation: RMS envelopes at 100 Hz, normalised, correlated over
the allowed lags with an FFT. Speech envelopes are distinctive enough that a few
seconds of overlap give a sharp peak; music or silence give a flat one, which is
reported as low confidence rather than a wrong offset.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

SR = 16_000
HOP = 160  # 10 ms → envelope at 100 Hz
ENV_RATE = SR // HOP


def envelope(audio: NDArray[np.float32]) -> NDArray[np.float64]:
    """Log-RMS envelope at 100 Hz (log: loud and quiet passages weigh alike)."""
    n = len(audio) // HOP
    if n == 0:
        return np.zeros(0)
    frames = audio[: n * HOP].astype(np.float64).reshape(n, HOP)
    return np.log10(np.sqrt(np.mean(frames**2, axis=1)) + 1e-4)


@dataclass(frozen=True, slots=True)
class Alignment:
    lag_s: float  # source time = capture time + lag_s
    confidence: float  # normalised correlation at the peak, 0..1
    sharpness: float  # peak vs the best value outside ±0.5 s of it

    @property
    def ok(self) -> bool:
        return self.confidence >= 0.6 and self.sharpness >= 1.25


def find_lag(
    capture: NDArray[np.float32], source: NDArray[np.float32], *, max_lag_s: float
) -> Alignment:
    """Where ``capture`` (the more recent, shorter excerpt) sits inside ``source``.

    Returns ``lag_s`` such that capture[0] corresponds to source[lag_s], searched
    in ``[0, max_lag_s]``.
    """
    c = envelope(capture)
    s = envelope(source)
    n = len(c)
    max_lag = min(int(max_lag_s * ENV_RATE), len(s) - n)
    if n < 2 * ENV_RATE or max_lag < 0:
        return Alignment(0.0, 0.0, 0.0)
    c = (c - c.mean()) / (c.std() + 1e-9)
    # Sliding normalised cross-correlation of c against every window of s.
    size = 1 << int(np.ceil(np.log2(len(s) + n)))
    corr = np.fft.irfft(np.fft.rfft(s, size) * np.conj(np.fft.rfft(c, size)), size)[: max_lag + 1]
    csum = np.concatenate([[0.0], np.cumsum(s)])
    csq = np.concatenate([[0.0], np.cumsum(s * s)])
    lags = np.arange(max_lag + 1)
    mean = (csum[lags + n] - csum[lags]) / n
    var = np.maximum((csq[lags + n] - csq[lags]) / n - mean**2, 1e-12)
    ncc = corr / (n * np.sqrt(var))  # c is zero-mean: the window mean cancels out
    best = int(np.argmax(ncc))
    peak = float(ncc[best])
    away = np.abs(lags - best) > ENV_RATE // 2
    runner_up = float(ncc[away].max()) if away.any() else 0.0
    sharp = peak / runner_up if runner_up > 0 else float("inf")
    return Alignment(best / ENV_RATE, max(0.0, min(1.0, peak)), sharp)
