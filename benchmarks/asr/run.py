"""WP04 — Japanese ASR benchmark.

    just bench-asr [--models kotoba-whisper-v2.0,large-v3] [--n 200] [--device cuda:0]

For every model × compute_type × beam_size, in a fresh subprocess (clean VRAM
measurement): CER on the public set (Common Voice 8.0 ja, CC0) and on the
"streams" set if present, hallucination rate on non-speech audio, decoding
latency p50/p95 on 2 / 5 / 10 s segments (warm GPU, one decode at a time), peak
VRAM, and partial stability (characters rewritten between successive decodes of
a growing segment). Writes benchmarks/asr/results-<date>.md and .json.

Data (git-ignored, see benchmarks/prepare_public.py):
  benchmarks/data/public/cv8/manifest.jsonl, cv8_concat.wav
  benchmarks/data/streams/manifest.jsonl   {"audio": "x.wav", "text": "..."} (optional)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "benchmarks"))

from synth import game_sfx, music, noise  # noqa: E402

from livesubs.asr.base import AsrResult  # noqa: E402
from livesubs.asr.filters import FilterParams, reject_reason  # noqa: E402
from livesubs.cer import cer, normalize_ja  # noqa: E402
from livesubs.replay import SR, load_audio  # noqa: E402

DATA = ROOT / "benchmarks" / "data"
OUT = ROOT / "benchmarks" / "asr"
DEFAULT_MODELS = "kotoba-whisper-v2.0,large-v3,large-v3-turbo"
P95_BUDGET_MS = 500.0
# Unbounded, OpenBLAS spins 56 threads for the mel spectrogram (x20 CPU for nothing).
THREAD_LIMITS = {"OMP_NUM_THREADS": "4", "OPENBLAS_NUM_THREADS": "4", "MKL_NUM_THREADS": "4"}
VRAM_BUDGET_MIB = 4096


# ------------------------------------------------------------------ datasets


def load_set(manifest: Path, n: int | None) -> list[tuple[np.ndarray, str]]:
    if not manifest.exists():
        return []
    rows = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines()]
    out = []
    for row in rows[:n]:
        audio_path = manifest.parent / row.get("audio", f"{row['id']}.wav")
        out.append((load_audio(str(audio_path)), row["text"]))
    return out


def nonspeech_set() -> list[tuple[str, np.ndarray]]:
    clips = [(f"music{i}", music(6 + i % 5, seed=i, bpm=90 + 10 * i)) for i in range(8)]
    clips += [(f"sfx{i}", game_sfx(6, seed=i)) for i in range(4)]
    clips += [(f"noise{i}", noise(5, seed=i, level=0.005 * (i + 1))) for i in range(4)]
    clips += [("silence0", np.zeros(5 * SR, dtype=np.float32))]
    clips += [("silence1", np.zeros(10 * SR, dtype=np.float32))]
    return clips


def crops(concat: np.ndarray, seconds: float, count: int, seed: int) -> list[np.ndarray]:
    rng = random.Random(seed)
    n = int(seconds * SR)
    return [concat[(s := rng.randrange(0, len(concat) - n)) : s + n] for _ in range(count)]


def growing_windows(concat_meta: list[dict[str, Any]], concat: np.ndarray) -> list[np.ndarray]:
    """8 s windows starting at an utterance onset: a segment that grows while someone talks."""
    starts = [m["t0"] for m in concat_meta if m["t0"] + 8 < len(concat) / SR][:10]
    return [concat[int(t * SR) : int((t + 8) * SR)] for t in starts]


# ------------------------------------------------------------------ worker


def kana(text: str) -> str:
    """Reading in hiragana: きょう and 今日 compare equal (Common Voice mixes both)."""
    import pykakasi

    global _KKS
    if _KKS is None:
        _KKS = pykakasi.kakasi()
    return "".join(item["hira"] for item in _KKS.convert(normalize_ja(text)))


_KKS: Any = None


def rewritten(prev: str, cur: str) -> int:
    common = 0
    for a, b in zip(prev, cur, strict=False):
        if a != b:
            break
        common += 1
    return len(prev) - common


def worker(cfg: dict[str, Any]) -> dict[str, Any]:
    from livesubs.asr.faster_whisper import FasterWhisperTranscriber

    t = time.perf_counter()
    tr = FasterWhisperTranscriber(
        cfg["model"],
        device=cfg["device"],
        compute_type=cfg["compute_type"],
        beam_size=cfg["beam_size"],
    )
    load_s = time.perf_counter() - t
    for _ in range(3):
        tr.warmup()

    def decode(audio: np.ndarray) -> tuple[AsrResult, float]:
        t0 = time.perf_counter()
        result = tr.transcribe(audio, prompt=None)
        return result, (time.perf_counter() - t0) * 1000

    def run(audio: np.ndarray) -> tuple[str, float]:
        result, ms = decode(audio)
        return result.text, ms

    def rejected(result: AsrResult, audio: np.ndarray) -> str | None:
        return reject_reason(result, len(audio) / SR, FilterParams())

    res: dict[str, Any] = {**cfg, "load_s": round(load_s, 1)}
    for name, manifest in (
        ("public", DATA / "public" / "cv8" / "manifest.jsonl"),
        ("streams", DATA / "streams" / "manifest.jsonl"),
    ):
        items = load_set(manifest, cfg["n"] if name == "public" else None)
        if not items:
            res[f"cer_{name}"] = None
            continue
        results = [decode(a)[0] for a, _ in items]
        hyps = [r.text for r in results]
        refs = [ref for _, ref in items]
        res[f"cer_{name}"] = round(100 * cer(refs, hyps), 2)
        res[f"cer_{name}_kana"] = round(
            100 * cer([kana(x) for x in refs], [kana(x) for x in hyps]), 2
        )
        res[f"n_{name}"] = len(items)
        dropped = [
            {"ref": ref, "hyp": r.text, "why": why, "lp": round(r.avg_logprob, 2)}
            for (a, ref), r in zip(items, results, strict=True)
            if (why := rejected(r, a))
        ]
        res[f"speech_rejected_{name}"] = round(len(dropped) / len(items), 4)
        res[f"speech_rejected_{name}_examples"] = dropped[:5]
        if name == "public":
            res["examples"] = [
                {"ref": r, "hyp": h} for (_, r), h in list(zip(items, hyps, strict=True))[:5]
            ]

    halluc = []
    kept = 0
    for label, audio in nonspeech_set():
        result = decode(audio)[0]
        if normalize_ja(result.text):
            halluc.append(
                {
                    "clip": label,
                    "text": result.text,
                    "lp": round(result.avg_logprob, 2),
                    "nsp": round(result.no_speech_prob, 2),
                }
            )
            kept += rejected(result, audio) is None
    res["hallucinations"] = halluc
    res["halluc_rate"] = round(len(halluc) / len(nonspeech_set()), 3)
    res["halluc_rate_filtered"] = round(kept / len(nonspeech_set()), 3)

    concat = load_audio(str(DATA / "public" / "cv8_concat.wav"))
    for seconds in (2, 5, 10):
        lat = [run(a)[1] for a in crops(concat, seconds, 20, seed=seconds)]
        res[f"lat{seconds}_p50"] = round(float(np.percentile(lat, 50)), 1)
        res[f"lat{seconds}_p95"] = round(float(np.percentile(lat, 95)), 1)

    meta = [
        json.loads(line)
        for line in (DATA / "public" / "cv8_concat.jsonl").read_text("utf-8").splitlines()
    ]
    steps = []
    for window in growing_windows(meta, concat):
        prev = ""
        for k in range(1, 9):
            cur = normalize_ja(run(window[: k * SR])[0])
            steps.append(rewritten(prev, cur))
            prev = cur
    res["rewrite_chars_per_step"] = round(float(np.mean(steps)), 2)
    return res


# ------------------------------------------------------------------ driver


def gpu_mem_of(pid: int) -> int:
    out = subprocess.run(
        ["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    for line in out.splitlines():
        p, _, mem = line.partition(",")
        if p.strip() == str(pid):
            return int(mem.strip())
    return 0


def run_config(cfg: dict[str, Any]) -> dict[str, Any]:
    proc = subprocess.Popen(
        [sys.executable, __file__, "--worker", json.dumps(cfg)],
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ, **THREAD_LIMITS, "HF_HUB_DISABLE_PROGRESS_BARS": "1"},
    )
    peak = 0
    stop = threading.Event()

    def poll() -> None:
        nonlocal peak
        while not stop.is_set():
            peak = max(peak, gpu_mem_of(proc.pid))
            stop.wait(0.25)

    poller = threading.Thread(target=poll, daemon=True)
    poller.start()
    stdout, _ = proc.communicate()
    stop.set()
    poller.join()
    if proc.returncode != 0:
        return {**cfg, "error": f"worker exited with {proc.returncode}"}
    res: dict[str, Any] = json.loads(stdout.strip().splitlines()[-1])
    res["vram_peak_mib"] = peak
    return res


def repo_revision(model: str) -> str | None:
    from huggingface_hub import HfApi

    from livesubs.asr.faster_whisper import MODELS

    try:
        return HfApi().model_info(MODELS.get(model, model)).sha
    except Exception:
        return None


def passes(r: dict[str, Any]) -> bool:
    return (
        "error" not in r
        and r["lat5_p95"] <= P95_BUDGET_MS
        and r["vram_peak_mib"] <= VRAM_BUDGET_MIB
    )


def decide(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Hard constraints, then lowest CER on streams (public if no streams set);
    a CER gap < 1 point is broken by latency."""
    ok = [r for r in results if passes(r)]
    if not ok:
        return None
    key = "cer_streams" if all(r.get("cer_streams") is not None for r in ok) else "cer_public"
    best = min(r[key] for r in ok)
    close = [r for r in ok if r[key] - best < 1.0]
    winner = min(close, key=lambda r: r["lat5_p50"])
    return {**winner, "decided_on": key}


def merge_with_today(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Re-running some configs the same day updates their rows, keeps the others."""
    path = OUT / f"results-{dt.date.today().isoformat()}.json"
    if not path.exists():
        return results
    key = lambda r: (r["model"], r["compute_type"], r["beam_size"])  # noqa: E731
    merged = {key(r): r for r in json.loads(path.read_text(encoding="utf-8"))["results"]}
    for r in results:
        if "error" not in r or key(r) not in merged:
            merged[key(r)] = r
    return list(merged.values())


def write_report(results: list[dict[str, Any]], args: argparse.Namespace) -> Path:
    date = dt.date.today().isoformat()
    winner = decide(results)
    gpu = subprocess.run(
        ["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.splitlines()
    lines = [
        f"# Banc ASR — {date}",
        "",
        f"GPU : {gpu[0] if gpu else '?'}, faster-whisper / CTranslate2, "
        "`temperature=0`, sans prompt, un décodage à la fois, GPU chaud.",
        "",
        f"- **Jeu public** : Common Voice 8.0 ja (test, CC0), {args.n} énoncés lus.",
        "- **Jeu « streams »** : "
        + (
            "présent."
            if any(r.get("cer_streams") is not None for r in results)
            else "**absent** (`benchmarks/data/streams/manifest.jsonl`) : "
            "le choix est **provisoire** tant qu'il n'a pas été passé sur de vrais streams."
        ),
        "- **Non-parole** : 18 extraits synthétiques (musique, effets de jeu, bruit, silence).",
        "- **CER kana** : même CER après conversion en lecture hiragana (pykakasi), parce que "
        "les références de Common Voice écrivent souvent en kana ce que les modèles écrivent "
        "en kanji (きょう / 今日) ; il isole les vraies erreurs d'écoute.",
        "- **Hallucinations** : part des extraits de non-parole qui produisent du texte, "
        "brut puis après le filtre du lot 05 (`livesubs.asr.filters`). "
        "**Parole rejetée** : part des énoncés réels que ce filtre jetterait à tort.",
        "- **Réécriture** : caractères réécrits en moyenne entre deux décodages successifs "
        "d'un segment qui grandit d'1 s (scintillement des `partial`, avant accord local).",
        "",
        "| Modèle | compute | beam | CER public % | CER kana % | CER streams % | "
        "hallu. brut → filtré | parole rejetée | 2 s p50/p95 ms | 5 s p50/p95 ms | "
        "10 s p50/p95 ms | VRAM Mio | réécr. | contraintes |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        if "error" in r:
            lines.append(
                f"| {r['model']} | {r['compute_type']} | {r['beam_size']} | "
                f"{r['error']} |||||||||||"
            )
            continue
        cs = "—" if r.get("cer_streams") is None else f"{r['cer_streams']:.2f}"
        lines.append(
            f"| {r['model']} | {r['compute_type']} `{r['device']}` | {r['beam_size']} | "
            f"{r['cer_public']:.2f} | {r['cer_public_kana']:.2f} | {cs} | "
            f"{r['halluc_rate']:.0%} → {r['halluc_rate_filtered']:.0%} | "
            f"{r['speech_rejected_public']:.1%} | {r['lat2_p50']:.0f}/{r['lat2_p95']:.0f} | "
            f"{r['lat5_p50']:.0f}/{r['lat5_p95']:.0f} | {r['lat10_p50']:.0f}/{r['lat10_p95']:.0f}"
            f" | {r['vram_peak_mib']} | {r['rewrite_chars_per_step']:.2f} | "
            f"{'✅' if passes(r) else '❌'} |"
        )
    lines += ["", "## Décision (règle du lot 04 §4)", ""]
    if winner:
        lines.append(
            f"**{winner['model']}**, `compute_type={winner['compute_type']}`, "
            f"`beam_size={winner['beam_size']}` : CER {winner[winner['decided_on']]:.2f} % "
            f"({winner['decided_on']}), 5 s p95 {winner['lat5_p95']:.0f} ms, "
            f"VRAM {winner['vram_peak_mib']} Mio."
        )
    else:
        lines.append("Aucune configuration ne respecte les contraintes dures.")
    lines += ["", "## Révisions testées", ""]
    for model in dict.fromkeys(r["model"] for r in results):
        lines.append(f"- `{model}` : `{repo_revision(model)}`")
    lines += ["", "## Hallucinations sur la non-parole", ""]
    for r in results:
        if r.get("hallucinations"):
            texts = "; ".join(f"{h['clip']}: « {h['text']} »" for h in r["hallucinations"][:6])
            lines.append(f"- {r['model']} {r['compute_type']} b{r['beam_size']} : {texts}")
    OUT.mkdir(parents=True, exist_ok=True)
    md = OUT / f"results-{date}.md"
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (OUT / f"results-{date}.json").write_text(
        json.dumps({"results": results, "winner": winner}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return md


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--worker", help=argparse.SUPPRESS)
    ap.add_argument("--models", default=DEFAULT_MODELS)
    ap.add_argument("--compute-types", default="float16,int8_float16")
    ap.add_argument("--beams", default="1,5")
    ap.add_argument("--n", type=int, default=200, help="public-set utterances")
    ap.add_argument("--device", default="cuda:0")
    args = ap.parse_args()
    if args.worker:
        print(json.dumps(worker(json.loads(args.worker)), ensure_ascii=False))
        return
    if not (DATA / "public" / "cv8_concat.wav").exists():
        sys.exit("public set missing: run `just bench-prepare` first")
    results = []
    for model in args.models.split(","):
        for compute_type in args.compute_types.split(","):
            for beam in map(int, args.beams.split(",")):
                cfg = {
                    "model": model,
                    "compute_type": compute_type,
                    "beam_size": beam,
                    "device": args.device,
                    "n": args.n,
                }
                print(f"→ {model} {compute_type} beam={beam}", flush=True)
                r = run_config(cfg)
                print(
                    {k: v for k, v in r.items() if k not in ("examples", "hallucinations")},
                    flush=True,
                )
                results.append(r)
    print(f"report: {write_report(merge_with_today(results), args)}")


if __name__ == "__main__":
    main()
