"""WP06 — translation benchmark: picks the model of AgenticEnv's `translate` profile.

    just bench-mt --models "Qwen3-30B-A3B-Instruct-2507,gemma-3-27b" [--segments FILE]

Each model must be served by llama-server when its turn comes (load it by hand,
or let AgenticEnv's model swapper do it): the script checks ``/v1/models`` and
waits for you otherwise. Latencies are measured through the very client the
server uses (``LlamaServerTranslator``), one request at a time, with the same
sliding context. Outputs are kept for the blind A/B review (``blind.py``).

Segments file (JSONL, one per line, in stream order): {"ja": "...", "channel_id": "..."}
Default: benchmarks/data/mt/segments.jsonl; build it from a replay with
``--from-replay replay.json`` (the finals of ``just replay ... --json``).
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

from livesubs.mt.base import TranslationContext
from livesubs.mt.glossary import GlossaryStore
from livesubs.mt.llama import LlamaServerTranslator
from livesubs.segments import FinalSegment

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "benchmarks" / "data" / "mt"
OUT = ROOT / "benchmarks" / "mt"


def load_segments(args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.from_replay:
        msgs = json.loads(Path(args.from_replay).read_text(encoding="utf-8"))["messages"]
        segs = [
            {"ja": m["ja"], "channel_id": args.channel_id}
            for m in msgs
            if m["type"] == "final" and m["ja"]
        ]
        DATA.mkdir(parents=True, exist_ok=True)
        (DATA / "segments.jsonl").write_text(
            "".join(json.dumps(s, ensure_ascii=False) + "\n" for s in segs), encoding="utf-8"
        )
        return segs[: args.n]
    path = Path(args.segments)
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()][: args.n]


def llama_vram_mib() -> list[int]:
    """VRAM held by llama-server on each GPU (host view)."""
    out = subprocess.run(
        [
            "nvidia-smi",
            "--query-compute-apps=pid,process_name,used_memory,gpu_uuid",
            "--format=csv,noheader,nounits",
        ],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    return [int(line.split(",")[2]) for line in out.splitlines() if "llama-server" in line]


async def wait_for_model(tr: LlamaServerTranslator, interactive: bool) -> bool:
    while True:
        status = await tr.status(fresh=True)
        if status.active:
            return True
        print(f"  llama-server serves {status.served or 'nothing'}, not {tr.model}.")
        if not interactive:
            return False
        answer = await asyncio.to_thread(input, "  load it (AgenticEnv), Enter — or s to skip: ")
        if answer.strip().lower() == "s":
            return False


async def bench_model(
    model: str, segs: list[dict[str, Any]], args: argparse.Namespace
) -> dict[str, Any]:
    tr = LlamaServerTranslator(args.base_url, model, max_tokens=args.max_tokens)
    if not await wait_for_model(tr, not args.no_wait):
        await tr.aclose()
        return {"model": model, "skipped": True}
    glossaries = GlossaryStore(ROOT / "glossaries")
    history: list[tuple[str, str]] = []
    first_ms: list[float] = []
    total_ms: list[float] = []
    outputs: list[str] = []
    # warm-up: first request pays for prompt processing of the system prompt
    async for _ in tr.translate(FinalSegment(0, "こんにちは", 0, 1, 0.0), TranslationContext()):
        pass
    for i, s in enumerate(segs):
        glossary = glossaries.get(s.get("channel_id"))
        ctx = TranslationContext(
            history=list(history), glossary=glossary, channel_name=glossary.name
        )
        t0 = time.perf_counter()
        first = None
        pieces = []
        async for piece in tr.translate(FinalSegment(i, s["ja"], 0, 1, 0), ctx):
            first = first or time.perf_counter()
            pieces.append(piece)
        done = time.perf_counter()
        en = "".join(pieces).strip()
        outputs.append(en)
        first_ms.append(((first or done) - t0) * 1000)
        total_ms.append((done - t0) * 1000)
        history = [*history, (s["ja"], en)][-6:]
        if i % 20 == 0:
            print(f"  [{i}/{len(segs)}] {s['ja']} → {en}  ({total_ms[-1]:.0f} ms)", flush=True)
    await tr.aclose()
    pct = lambda v, q: round(float(np.percentile(v, q)), 1)  # noqa: E731
    return {
        "model": model,
        "first_token_p50": pct(first_ms, 50),
        "first_token_p95": pct(first_ms, 95),
        "total_p50": pct(total_ms, 50),
        "total_p95": pct(total_ms, 95),
        "vram_mib": llama_vram_mib(),
        "outputs": outputs,
    }


def write_report(results: list[dict[str, Any]], segs: list[dict[str, Any]]) -> Path:
    date = dt.date.today().isoformat()
    lines = [
        f"# Banc de traduction — {date}",
        "",
        f"{len(segs)} segments, contexte glissant de 6 couples, `temperature=0.2`, "
        "un appel à la fois, client `LlamaServerTranslator` (celui du serveur).",
        "",
        "| Modèle | 1er jeton p50/p95 ms | total p50/p95 ms | VRAM llama-server Mio |",
        "|---|---|---|---|",
    ]
    for r in results:
        if r.get("skipped"):
            lines.append(f"| {r['model']} | non servi, sauté | | |")
            continue
        lines.append(
            f"| {r['model']} | {r['first_token_p50']:.0f}/{r['first_token_p95']:.0f} | "
            f"{r['total_p50']:.0f}/{r['total_p95']:.0f} | {' + '.join(map(str, r['vram_mib']))} |"
        )
    lines += [
        "",
        "Qualité : `just bench-mt-blind` (A/B à l'aveugle sur 50 segments).",
        "",
        "## Exemples",
        "",
    ]
    ok = [r for r in results if not r.get("skipped")]
    for i, s in enumerate(segs[:10]):
        lines.append(f"- {s['ja']}")
        for r in ok:
            lines.append(f"  - *{r['model']}* : {r['outputs'][i]}")
    OUT.mkdir(parents=True, exist_ok=True)
    md = OUT / f"results-{date}.md"
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (OUT / f"results-{date}.json").write_text(
        json.dumps({"segments": segs, "results": results}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return md


async def amain() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument(
        "--models", required=True, help="comma-separated names as llama-server serves them"
    )
    ap.add_argument(
        "--base-url", default=os.environ.get("LIVESUBS_LLM_BASE_URL", "http://127.0.0.1:8000/v1")
    )
    ap.add_argument("--segments", default=str(DATA / "segments.jsonl"))
    ap.add_argument(
        "--from-replay", help="build the segments file from `just replay --json` output"
    )
    ap.add_argument("--channel-id", default=None)
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--max-tokens", type=int, default=120)
    ap.add_argument("--no-wait", action="store_true", help="skip models that are not served")
    args = ap.parse_args()
    segs = load_segments(args)
    if not segs:
        sys.exit("no segments")
    results = []
    for model in args.models.split(","):
        print(f"→ {model}", flush=True)
        results.append(await bench_model(model.strip(), segs, args))
    print(f"report: {write_report(results, segs)}")


if __name__ == "__main__":
    asyncio.run(amain())
