import numpy as np

from livesubs.ahead.align import SR, find_lag

from .audio import concat, silence, speech


def talk(seconds: float, seed: int) -> np.ndarray:
    """Bursts of 'speech' with pauses of random length: a distinctive envelope."""
    rng = np.random.default_rng(seed)
    parts = []
    total = 0.0
    while total < seconds:
        on, off = rng.uniform(0.3, 1.5), rng.uniform(0.1, 0.8)
        parts += [
            speech(on, seed=int(rng.integers(1_000_000))) * rng.uniform(0.3, 1.0),
            silence(off),
        ]
        total += on + off
    return concat(*parts)[: int(seconds * SR)]


def test_recovers_the_lag_with_noise_and_level_change() -> None:
    source = talk(60, seed=1)
    lag = 23.47
    capture = source[int(lag * SR) : int((lag + 12) * SR)].copy()
    capture = capture * 0.3 + np.random.default_rng(2).standard_normal(len(capture)) * 0.003
    a = find_lag(capture.astype(np.float32), source, max_lag_s=48)
    assert a.ok
    assert abs(a.lag_s - lag) <= 0.02


def test_unrelated_audio_is_not_ok() -> None:
    a = find_lag(talk(10, seed=3), talk(60, seed=4), max_lag_s=50)
    assert not a.ok


def test_silence_is_not_ok() -> None:
    a = find_lag(silence(10), talk(60, seed=5), max_lag_s=50)
    assert not a.ok


def test_too_short_is_not_ok() -> None:
    assert not find_lag(talk(1, seed=6), talk(30, seed=7), max_lag_s=20).ok
