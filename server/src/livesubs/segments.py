"""Types shared by the ASR pipeline and the translator."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FinalSegment:
    """A transcribed segment, as handed to the translator."""

    seg_id: int
    ja: str
    t0: float
    t1: float
    speech_end_at: float  # time.monotonic() when the end of the segment's audio arrived
