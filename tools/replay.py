"""Replay an audio file into a running live-subs server, like the extension would.

    just replay benchmarks/data/clip.wav [--speed 4] [--dump-segments out/] [--json out.json]

Prints partial / final / translation messages as they arrive, with their latency
from the end of speech, then p50 / p95 latencies.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from livesubs.replay import SR, load_audio, replay, write_wav

GREY, RESET, BOLD = "\033[90m", "\033[0m", "\033[1m"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("source", help="WAV file (anything else needs ffmpeg), path or URL")
    ap.add_argument("--url", default=os.environ.get("LIVESUBS_URL", "ws://127.0.0.1:8765/ws"))
    ap.add_argument("--speed", type=float, default=1.0, help="× real time; 0 = no pacing")
    ap.add_argument("--channel-id", default=None, help="selects the glossary")
    ap.add_argument("--video-id", default="replay")
    ap.add_argument("--token", default=os.environ.get("LIVESUBS_TOKEN") or None)
    ap.add_argument("--dump-segments", type=Path, help="write one WAV per final segment here")
    ap.add_argument("--json", type=Path, help="write every message and the summary here")
    ap.add_argument("--quiet", action="store_true", help="only the summary")
    args = ap.parse_args()

    audio = load_audio(args.source)
    print(f"{args.source}: {len(audio) / SR:.1f} s → {args.url} at ×{args.speed or 'max'}")
    t_start = time.monotonic()

    def show(msg: dict[str, Any], now: float) -> None:
        if args.quiet:
            return
        clock = f"{now - t_start:7.2f}s"
        match msg["type"]:
            case "partial":
                print(
                    f"{clock} {GREY}~{msg['seg_id']:>3} {msg['ja_stable']}"
                    f"[{msg['ja_unstable']}]{RESET}"
                )
            case "final":
                print(
                    f"{clock} {BOLD}JA{msg['seg_id']:>3}{RESET} [{msg['t0']:.2f}–{msg['t1']:.2f}]"
                    f" {msg['ja']}  {GREY}(asr {msg['asr_ms']:.0f} ms){RESET}"
                )
            case "translation":
                print(
                    f"{clock} {BOLD}EN{msg['seg_id']:>3}{RESET} {msg['en']}"
                    f"  {GREY}(mt {msg['mt_ms']:.0f} ms){RESET}"
                )
            case "error":
                print(f"{clock} ERROR {msg['code']}: {msg['message']}")
            case "stats" | "translation_delta":
                pass
            case _:
                print(f"{clock} {msg}")

    result = asyncio.run(
        replay(
            args.url,
            audio,
            speed=args.speed,
            video_id=args.video_id,
            channel_id=args.channel_id,
            token=args.token,
            on_message=show,
        )
    )
    summary = result.summary()
    print(json.dumps({k: v for k, v in summary.items() if k != "errors"}, indent=2))

    if args.dump_segments:
        args.dump_segments.mkdir(parents=True, exist_ok=True)
        for f in result.finals():
            clip = audio[int(f["t0"] * SR) : int(f["t1"] * SR)]
            write_wav(args.dump_segments / f"seg{f['seg_id']:04d}_{f['t0']:08.2f}.wav", clip)
        print(f"{len(result.finals())} segments written to {args.dump_segments}")
    if args.json:
        args.json.write_text(
            json.dumps(
                {"summary": summary, "messages": result.messages}, ensure_ascii=False, indent=1
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
