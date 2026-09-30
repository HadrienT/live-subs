"""Procedural non-speech audio (license-free by construction): background music,
game-like effects, noise. Used by the ASR bench (hallucination rate) and the
music fixture of the golden tests."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

SR = 16_000


def _env(n: int, attack: float, decay: float) -> NDArray[np.float64]:
    t = np.arange(n) / SR
    return np.minimum(t / attack, 1.0) * np.exp(-t / decay)


def music(seconds: float, seed: int = 0, bpm: float = 110) -> NDArray[np.float32]:
    """Chord pads + bass + plucked melody + kick/hi-hat, like stream BGM."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    out = np.zeros(n)
    beat = int(SR * 60 / bpm)
    root = rng.choice([220.0, 246.9, 261.6, 293.7])
    progression = [0, 5, 7, 3]
    for bar, start in enumerate(range(0, n, 4 * beat)):
        semis = progression[bar % 4]
        base = root * 2 ** (semis / 12)
        length = min(4 * beat, n - start)
        t = np.arange(length) / SR
        for ratio in (1.0, 1.26, 1.5):  # major triad
            for harm, amp in ((1, 0.06), (2, 0.02), (3, 0.01)):
                out[start : start + length] += amp * np.sin(2 * np.pi * base * ratio * harm * t)
        out[start : start + length] += 0.08 * np.sin(2 * np.pi * base / 2 * t)
    scale = [0, 2, 4, 7, 9, 12]
    for start in range(0, n, beat // 2):
        length = min(beat // 2, n - start)
        f = root * 2 * 2 ** (rng.choice(scale) / 12)
        t = np.arange(length) / SR
        out[start : start + length] += 0.07 * np.sin(2 * np.pi * f * t) * _env(length, 0.005, 0.12)
    for start in range(0, n, beat):
        length = min(beat, n - start)
        t = np.arange(length) / SR
        kick = np.sin(2 * np.pi * (50 + 80 * np.exp(-t * 30)) * t) * _env(length, 0.001, 0.08)
        out[start : start + length] += 0.25 * kick
        hat = min(int(0.03 * SR), n - start - beat // 2) if start + beat // 2 < n else 0
        if hat > 0:
            h0 = start + beat // 2
            out[h0 : h0 + hat] += 0.04 * rng.standard_normal(hat) * _env(hat, 0.0005, 0.01)
    return (out / max(np.abs(out).max(), 1e-9) * 0.4).astype(np.float32)


def game_sfx(seconds: float, seed: int = 0) -> NDArray[np.float32]:
    """Bleeps, sweeps and clicks over a low hum."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    t = np.arange(n) / SR
    out = 0.02 * np.sin(2 * np.pi * 60 * t)
    for _ in range(int(seconds * 3)):
        start = rng.integers(0, max(n - SR // 4, 1))
        length = int(rng.uniform(0.05, 0.25) * SR)
        tt = np.arange(length) / SR
        f0, f1 = rng.uniform(300, 2000, size=2)
        sweep = np.sin(2 * np.pi * (f0 + (f1 - f0) * tt / tt[-1] / 2) * tt)
        out[start : start + length] += 0.2 * sweep * _env(length, 0.002, 0.08)[: len(sweep)]
    return out.astype(np.float32)


def noise(seconds: float, seed: int = 0, level: float = 0.02) -> NDArray[np.float32]:
    return (np.random.default_rng(seed).standard_normal(int(seconds * SR)) * level).astype(
        np.float32
    )
