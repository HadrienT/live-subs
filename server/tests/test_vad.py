import itertools

import numpy as np

from livesubs.protocol import SAMPLE_RATE as SR
from livesubs.vad import WINDOW_SAMPLES, EnergyVad, Segmenter, SileroVad, SpeechSegment, VadParams

from .audio import concat, silence, speech


def run(audio: np.ndarray, params: VadParams | None = None) -> list[SpeechSegment]:
    vad, seg = EnergyVad(), Segmenter(params or VadParams())
    events: list[SpeechSegment] = []
    end = 0
    for end in range(WINDOW_SAMPLES, len(audio) + 1, WINDOW_SAMPLES):
        events += seg.push(end, vad.prob(audio[end - WINDOW_SAMPLES : end]))
    return events + seg.flush(end)


def closed(events: list[SpeechSegment]) -> list[SpeechSegment]:
    return [e for e in events if e.is_closed]


def test_two_sentences_split_on_long_silence() -> None:
    audio = concat(silence(1), speech(2), silence(0.8), speech(1.5), silence(1))
    segs = closed(run(audio))
    assert [s.seg_id for s in segs] == [1, 2]
    first, second = segs
    # padded by 200 ms on each side
    assert abs(first.start_idx / SR - 0.8) < 0.05
    assert abs(first.end_idx / SR - 3.2) < 0.05
    assert abs(second.start_idx / SR - 3.6) < 0.05


def test_short_pause_keeps_one_segment() -> None:
    audio = concat(silence(1), speech(1), silence(0.25), speech(1), silence(1))
    assert len(closed(run(audio))) == 1


def test_brief_noise_is_ignored() -> None:
    audio = concat(silence(1), speech(0.1), silence(1))
    assert run(audio) == []


def test_forced_cut_at_max_segment() -> None:
    audio = concat(silence(0.5), speech(30), silence(1))
    segs = closed(run(audio, VadParams(max_segment_s=12)))
    assert len(segs) == 3
    assert all(s.duration_s <= 12.01 for s in segs)
    # contiguous: each cut starts where the previous one ended
    for a, b in itertools.pairwise(segs):
        assert a.end_idx == b.start_idx


def test_open_segment_republished_every_second() -> None:
    audio = concat(silence(0.5), speech(4.2), silence(1))
    events = run(audio)
    updates = [e for e in events if not e.is_closed]
    assert len(updates) == 4
    assert all(u.seg_id == 1 for u in updates)
    assert closed(events)[0].seg_id == 1


def test_silero_loads_and_rejects_silence() -> None:
    vad = SileroVad()
    probs = [vad.prob(np.zeros(WINDOW_SAMPLES, dtype=np.float32)) for _ in range(10)]
    assert all(0.0 <= q < 0.2 for q in probs)
    vad.reset()


class CountingVad:
    """Energy VAD that counts resets."""

    def __init__(self) -> None:
        self.inner = EnergyVad()
        self.resets = 0

    def prob(self, window: np.ndarray) -> float:
        return self.inner.prob(window)

    def reset(self) -> None:
        self.resets += 1


def test_session_resets_vad_state_after_quiet() -> None:
    from livesubs import protocol as p
    from livesubs.session import Session, VadOnlySink

    from .audio import to_frames

    vad = CountingVad()
    hello = p.Hello(protocol_version=p.PROTOCOL_VERSION, video_id="v")
    session = Session(
        hello,
        vad=vad,
        vad_params=VadParams(reset_after_s=1.0),
        sink=VadOnlySink(),
        emit=lambda m: None,
    )
    # 0.5 s of silence: no reset; speech; then 2.5 s of silence: two resets
    for frame in to_frames(concat(silence(0.5), speech(1), silence(2.5))):
        session.on_frame(frame)
    assert vad.resets == 2
