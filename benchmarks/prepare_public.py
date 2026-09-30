"""Build the public (CC0) benchmark set from Common Voice 8.0 ja (test split).

    uv run --group bench python ../benchmarks/prepare_public.py [--n 300]

Source: https://huggingface.co/datasets/japanese-asr/ja_asr.common_voice_8_0
(parquet mirror of Mozilla Common Voice 8.0, CC0-1.0). Writes, under
benchmarks/data/public/ (git-ignored):
  cv8/<id>.wav + cv8/manifest.jsonl   individual 16 kHz clips with reference text
  cv8_concat.wav + cv8_concat.jsonl   clips chained with 0.3–1.5 s pauses (replay input)
"""

from __future__ import annotations

import argparse
import io
import json
import random
import urllib.request
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import soundfile as sf

from livesubs.replay import SR, resample, write_wav

DATA = Path(__file__).resolve().parent / "data" / "public"
URL = (
    "https://huggingface.co/datasets/japanese-asr/ja_asr.common_voice_8_0/"
    "resolve/main/data/test-00000-of-00001.parquet"
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=300, help="clips to extract")
    ap.add_argument("--concat-minutes", type=float, default=5.0)
    ap.add_argument("--seed", type=int, default=20260930)
    args = ap.parse_args()

    parquet = DATA / "cv8_ja_test.parquet"
    if not parquet.exists():
        DATA.mkdir(parents=True, exist_ok=True)
        print(f"downloading {URL}")
        urllib.request.urlretrieve(URL, parquet)
    table = pq.read_table(parquet)
    rows = list(range(table.num_rows))
    random.Random(args.seed).shuffle(rows)

    out = DATA / "cv8"
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    rng = random.Random(args.seed)
    concat: list[np.ndarray] = [np.zeros(SR, dtype=np.float32)]
    concat_meta = []
    t = 1.0
    for row in rows[: args.n]:
        rec = table.slice(row, 1).to_pylist()[0]
        audio, rate = sf.read(io.BytesIO(rec["audio"]["bytes"]), dtype="float32")
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        audio = resample(audio, rate, SR)
        clip_id = Path(rec["audio"]["path"]).stem
        write_wav(out / f"{clip_id}.wav", audio)
        entry = {"id": clip_id, "text": rec["transcription"], "seconds": len(audio) / SR}
        manifest.append(entry)
        if t < args.concat_minutes * 60:
            concat.append(audio)
            concat_meta.append({**entry, "t0": t, "t1": t + len(audio) / SR})
            t += len(audio) / SR
            pause = rng.uniform(0.3, 1.5)
            concat.append(np.zeros(int(pause * SR), dtype=np.float32))
            t += pause
    (out / "manifest.jsonl").write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in manifest), encoding="utf-8"
    )
    write_wav(DATA / "cv8_concat.wav", np.concatenate(concat))
    (DATA / "cv8_concat.jsonl").write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in concat_meta), encoding="utf-8"
    )
    print(f"{len(manifest)} clips in {out}, {t / 60:.1f} min in cv8_concat.wav")


if __name__ == "__main__":
    main()
