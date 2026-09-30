import numpy as np

from livesubs import protocol as p
from livesubs.ingest import AudioRing, Ingest, TimeMap


def test_ring_wraps_and_clips() -> None:
    ring = AudioRing(seconds=1.0)  # 16 000 samples
    data = np.arange(40_000, dtype=np.float32)
    for off in range(0, len(data), 3_000):
        ring.write(data[off : off + 3_000])
    assert ring.end_idx == 40_000
    assert ring.start_idx == 24_000
    np.testing.assert_array_equal(ring.read(30_000, 30_010), data[30_000:30_010])
    np.testing.assert_array_equal(ring.read(20_000, 24_005), data[24_000:24_005])  # clipped
    assert len(ring.read(39_990, 50_000)) == 10


def test_time_map_is_linear_inside_frames() -> None:
    tm = TimeMap()
    tm.add(0, 100.0)
    tm.add(1600, 100.1)
    tm.add(3200, 250.0)  # seek
    assert tm.media_time(800) == 100.05
    assert tm.media_time(3200 + 160) == 250.01


def _frame(idx: int, n: int = 1600, value: int = 1000, disc: bool = False) -> p.AudioFrame:
    return p.AudioFrame(idx, idx / 16000, np.full(n, value, dtype=np.int16), disc)


def test_duplicate_and_late_frames_are_dropped() -> None:
    ing = Ingest()
    assert ing.accept(_frame(0)) is not None
    assert ing.accept(_frame(1600)) is not None
    assert ing.accept(_frame(1600)) is None
    assert ing.accept(_frame(0)) is None
    assert ing.dropped == 2
    assert ing.expected_idx == 3200


def test_gap_is_filled_with_silence() -> None:
    ing = Ingest()
    ing.accept(_frame(0))
    acc = ing.accept(_frame(4800))  # frames 1600 and 3200 lost
    assert acc is not None and acc.gap == 3200
    assert ing.gap_samples == 3200
    assert not ing.ring.read(1600, 4800).any()
    assert ing.ring.read(4800, 4801)[0] > 0


def test_huge_gap_becomes_discontinuity() -> None:
    ing = Ingest(ring_seconds=1.0)
    ing.accept(_frame(0))
    acc = ing.accept(_frame(10 * 16000))
    assert acc is not None and acc.discontinuity and acc.gap == 0


def test_restart_drops_frames_from_before() -> None:
    ing = Ingest()
    ing.accept(_frame(0))
    ing.accept(_frame(1600))
    ing.restart()
    assert ing.accept(_frame(0)) is None
    acc = ing.accept(_frame(48_000))
    assert acc is not None and acc.gap == 0
