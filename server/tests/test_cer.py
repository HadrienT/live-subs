from livesubs.cer import cer, edit_distance, normalize_ja


def test_normalize_drops_punctuation_and_width() -> None:
    assert normalize_ja("忘れちゃったの？.") == "忘れちゃったの"
    assert normalize_ja("ＡＢＣ　１２３、ｶﾀｶﾅ") == "ABC123カタカナ"
    assert normalize_ja("三時", kanji_digits=True) == "3時"


def test_cer() -> None:
    assert edit_distance("abc", "abd") == 1
    assert cer(["今日は晴れ。"], ["今日は晴れ"]) == 0.0
    assert cer(["今日は晴れ"], ["今日は雨"]) == 2 / 5
