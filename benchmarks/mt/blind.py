"""WP06 — blind A/B review of two translation models from a bench-mt results file.

    just bench-mt-blind [results-YYYY-MM-DD.json] [--n 50]

Shows the Japanese line, its previous line for context, and the two English
candidates in random order (A / B). Answer a, b or = (tie). Model names are only
revealed at the end; votes are saved next to the results file.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

OUT = Path(__file__).resolve().parent


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("results", nargs="?", help="default: latest results-*.json")
    ap.add_argument("--models", help="the two models to compare (default: first two)")
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()

    path = Path(args.results) if args.results else sorted(OUT.glob("results-*.json"))[-1]
    data = json.loads(path.read_text(encoding="utf-8"))
    results = [r for r in data["results"] if not r.get("skipped")]
    if args.models:
        wanted = args.models.split(",")
        results = [r for r in results if r["model"] in wanted]
    if len(results) < 2:
        raise SystemExit("need two benchmarked models")
    a, b = results[:2]
    segs = data["segments"]
    rng = random.Random(args.seed)
    picks = rng.sample(range(len(segs)), min(args.n, len(segs)))
    score = {a["model"]: 0.0, b["model"]: 0.0}
    votes = []
    for k, i in enumerate(picks, 1):
        pair = [a, b]
        rng.shuffle(pair)
        print(f"\n[{k}/{len(picks)}]")
        if i > 0:
            print(f"  (previous) {segs[i - 1]['ja']}")
        print(f"  JA  {segs[i]['ja']}")
        print(f"  A   {pair[0]['outputs'][i]}")
        print(f"  B   {pair[1]['outputs'][i]}")
        answer = ""
        while answer not in ("a", "b", "="):
            answer = input("  better? [a/b/=] ").strip().lower()
        if answer == "=":
            score[a["model"]] += 0.5
            score[b["model"]] += 0.5
        else:
            score[pair[0 if answer == "a" else 1]["model"]] += 1
        votes.append({"segment": i, "a": pair[0]["model"], "b": pair[1]["model"], "vote": answer})
    print("\nScore:", ", ".join(f"{m} {s:g}" for m, s in score.items()))
    out = path.with_name(path.stem + "-blind.json")
    out.write_text(
        json.dumps({"score": score, "votes": votes}, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"votes saved to {out}")


if __name__ == "__main__":
    main()
