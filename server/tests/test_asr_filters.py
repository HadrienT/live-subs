import pytest

from livesubs.asr.base import AsrResult, compression_ratio
from livesubs.asr.filters import FilterParams, reject_reason

P = FilterParams()


def r(text: str, nsp: float = 0.05, lp: float = -0.05) -> AsrResult:
    return AsrResult(
        text=text, no_speech_prob=nsp, avg_logprob=lp, compression_ratio=compression_ratio(text)
    )


@pytest.mark.parametrize(
    ("result", "seconds", "reason"),
    [
        (r("今日はいい天気ですね"), 2.0, None),
        (r("ごめん", lp=-0.05), 1.0, None),  # a real, confident short utterance
        (r("ごめん", lp=-0.45), 6.0, "sparse_low_confidence"),  # kotoba on music
        (r(""), 2.0, "empty"),
        (r("。"), 2.0, "empty"),
        (r("えー", nsp=0.8, lp=-1.2), 2.0, "no_speech"),
        (r("ありがとう" * 30), 10.0, "repetition"),
        (r("ご視聴ありがとうございました"), 3.0, "blacklist"),
        (r("チャンネル登録お願いします"), 3.0, "blacklist"),
        (r("(拍手)"), 2.0, "blacklist"),
    ],
)
def test_reject_reason(result: AsrResult, seconds: float, reason: str | None) -> None:
    assert reject_reason(result, seconds, P) == reason
