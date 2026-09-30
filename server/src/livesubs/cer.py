"""Character error rate for Japanese (WER is meaningless without word boundaries)."""

from __future__ import annotations

import unicodedata

_KANJI_DIGITS = str.maketrans("〇一二三四五六七八九", "0123456789")


def normalize_ja(text: str, *, kanji_digits: bool = False) -> str:
    """NFKC (full-width → ASCII, half-width kana → full), drop punctuation, symbols
    and spaces. ``kanji_digits`` also maps lone kanji digits to ASCII ones."""
    text = unicodedata.normalize("NFKC", text)
    if kanji_digits:
        text = text.translate(_KANJI_DIGITS)
    return "".join(
        ch for ch in text if not unicodedata.category(ch).startswith(("P", "S", "Z", "C"))
    )


def edit_distance(ref: str, hyp: str) -> int:
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i]
        for j, h in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h)))
        prev = cur
    return prev[-1]


def cer(refs: list[str], hyps: list[str]) -> float:
    """Corpus CER: total edits over total reference characters, after normalisation."""
    edits = total = 0
    for ref, hyp in zip(refs, hyps, strict=True):
        r, h = normalize_ja(ref), normalize_ja(hyp)
        edits += edit_distance(r, h)
        total += len(r)
    return edits / total if total else 0.0
