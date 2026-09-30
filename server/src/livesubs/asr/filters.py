"""Hallucination guard: decides whether an ASR result is shown at all.

Whisper-family models write text on music, noise and silence. The VAD is the
first barrier; this is the second. Thresholds come from the WP04 bench (see
ADR-002): kotoba-whisper's ``no_speech_prob`` stays below 0.2 even on silence,
so the discriminating signal is a low ``avg_logprob`` together with a sparse
output (a few characters for seconds of audio).
"""

from __future__ import annotations

from dataclasses import dataclass

from livesubs.asr.base import AsrResult
from livesubs.cer import normalize_ja

# Credits and outros from the subtitle data Whisper was trained on.
BLACKLIST_CONTAINS = (
    "ご視聴ありがとうございました",
    "ご視聴ありがとうございます",
    "ご視聴いただきありがとうございます",
    "チャンネル登録",
    "高評価",
    "字幕作成",
    "字幕提供",
    "次回もお楽しみに",
    "おやすみなさいませ",
)
BLACKLIST_EXACT = ("おわり", "終わり", "拍手", "笑", "音楽", "字幕")


@dataclass(frozen=True, slots=True)
class FilterParams:
    no_speech_prob: float = 0.6
    no_speech_logprob: float = -1.0
    max_compression_ratio: float = 2.4
    sparse_logprob: float = -0.3
    sparse_chars_per_s: float = 2.0


def reject_reason(result: AsrResult, duration_s: float, p: FilterParams) -> str | None:
    """``None`` if the result may be shown, else why it is dropped."""
    text = normalize_ja(result.text)
    if not text:
        return "empty"
    if result.no_speech_prob > p.no_speech_prob and result.avg_logprob < p.no_speech_logprob:
        return "no_speech"
    if result.compression_ratio > p.max_compression_ratio:
        return "repetition"
    if text in BLACKLIST_EXACT or any(b in text for b in BLACKLIST_CONTAINS):
        return "blacklist"
    if (
        result.avg_logprob < p.sparse_logprob
        and len(text) / max(duration_s, 0.1) < p.sparse_chars_per_s
    ):
        return "sparse_low_confidence"
    return None
