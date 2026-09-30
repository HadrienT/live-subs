"""Build the versioned golden-test fixtures (WP12) from the public CC0 set.

    uv run --group bench python ../benchmarks/make_fixtures.py

Needs `just bench-prepare` first. Writes server/tests/fixtures/golden/<name>.wav
(16 kHz mono, 20–30 s) and <name>.ref.json: the utterances with their reference
text and bounds, the music-only intervals, and the Common Voice clip ids.
Sources: Mozilla Common Voice 8.0 ja (CC0-1.0) and procedural music
(benchmarks/synth.py, no third-party material).
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "benchmarks"))

from synth import music  # noqa: E402

from livesubs.replay import SR, load_audio, write_wav  # noqa: E402

CV = ROOT / "benchmarks" / "data" / "public" / "cv8"
OUT = ROOT / "server" / "tests" / "fixtures" / "golden"


def db(x: np.ndarray) -> float:
    return float(20 * np.log10(np.sqrt(np.mean(x**2)) + 1e-9))


def build(
    name: str, clips: list[dict], *, bgm_db: float | None, music_gap_s: float, rng: random.Random
) -> None:
    parts = [np.zeros(int(0.8 * SR), dtype=np.float32)]
    t = 0.8
    utterances, music_only = [], []
    for i, clip in enumerate(clips):
        audio = load_audio(str(CV / f"{clip['id']}.wav"))
        parts.append(audio)
        utterances.append(
            {
                "id": clip["id"],
                "text": clip["text"],
                "t0": round(t, 3),
                "t1": round(t + len(audio) / SR, 3),
            }
        )
        t += len(audio) / SR
        pause = rng.uniform(0.6, 1.4)
        if music_gap_s and i == len(clips) // 2:
            # a stretch of music alone, as between two talks on a stream
            gap = music(music_gap_s, seed=len(name)) * 0.5
            parts.append(gap)
            music_only.append({"t0": round(t, 3), "t1": round(t + music_gap_s, 3)})
            t += music_gap_s
            pause = 0.5
        parts.append(np.zeros(int(pause * SR), dtype=np.float32))
        t += pause
    audio = np.concatenate(parts).astype(np.float32)
    if bgm_db is not None:
        bgm = music(len(audio) / SR + 1, seed=7)[: len(audio)]
        speech_level = db(np.concatenate([p for p in parts if db(p) > -60]))
        bgm = bgm * 10 ** ((speech_level + bgm_db - db(bgm)) / 20)
        audio = np.clip(audio + bgm, -1, 1).astype(np.float32)
    write_wav(OUT / f"{name}.wav", audio)
    ref = {
        "source": "Mozilla Common Voice 8.0 ja test split (CC0-1.0); music: benchmarks/synth.py",
        "seconds": round(len(audio) / SR, 2),
        "background_music_db": bgm_db,
        "utterances": utterances,
        "music_only": music_only,
    }
    (OUT / f"{name}.ref.json").write_text(
        json.dumps(ref, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(f"{name}: {ref['seconds']} s, {len(utterances)} utterances")


def main() -> None:
    rows = [json.loads(line) for line in (CV / "manifest.jsonl").read_text("utf-8").splitlines()]
    bench_ids = {r["id"] for r in rows[:200]}  # the ASR bench's utterances: kept out
    pool = [r for r in rows if r["id"] not in bench_ids and 2.0 <= r["seconds"] <= 6.0]
    rng = random.Random(20260930)
    rng.shuffle(pool)
    OUT.mkdir(parents=True, exist_ok=True)
    build("speech_a", pool[0:6], bgm_db=None, music_gap_s=0, rng=rng)
    build("speech_b", pool[6:12], bgm_db=None, music_gap_s=0, rng=rng)
    build("speech_bgm", pool[12:18], bgm_db=-12, music_gap_s=0, rng=rng)
    build("music_gap", pool[18:22], bgm_db=None, music_gap_s=10, rng=rng)


if __name__ == "__main__":
    main()
