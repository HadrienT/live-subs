"""WP02 spike echo server: records what the spike extension sends.

Run on 192.168.1.200:  uv run --project ../../server python echo_server.py
Writes spike-<conn>.wav (16 kHz mono) in benchmarks/data/ and reports, per
connection, frames received, sequence gaps and inter-arrival jitter.
"""

from __future__ import annotations

import asyncio
import itertools
import struct
import time
import wave
from pathlib import Path

from websockets.asyncio.server import ServerConnection, serve

OUT = Path(__file__).resolve().parents[2] / "benchmarks" / "data"


async def handle(ws: ServerConnection) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    name = OUT / f"spike-{time.strftime('%Y%m%d-%H%M%S')}-{id(ws) % 10000}.wav"
    frames = gaps = 0
    last_seq = -1
    arrivals: list[float] = []
    with wave.open(str(name), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        async for msg in ws:
            if isinstance(msg, str):
                continue
            if len(msg) >= 13 and len(msg) % 2 == 1:  # background: 13-byte header
                seq, media_time, ad = struct.unpack_from("<IdB", msg)
                pcm = msg[13:]
                if last_seq >= 0 and seq != last_seq + 1:
                    gaps += 1
                last_seq = seq
                if frames % 50 == 0:
                    await ws.send(f"frame {seq} media_time={media_time:.2f} ad={ad}")
            else:  # direct from the content script: raw PCM
                pcm = msg
            wav.writeframes(pcm)
            frames += 1
            arrivals.append(time.monotonic())
    deltas = sorted(b - a for a, b in itertools.pairwise(arrivals))
    p = (lambda q: deltas[int(q * (len(deltas) - 1))] * 1000) if deltas else (lambda q: 0.0)
    print(
        f"{name.name}: {frames} frames, {gaps} seq gaps, inter-arrival "
        f"p50={p(0.5):.0f}ms p95={p(0.95):.0f}ms max={p(1):.0f}ms"
    )


async def main() -> None:
    async with serve(handle, "0.0.0.0", 8766, max_size=2**20):
        print("spike echo server on ws://0.0.0.0:8766/spike")
        await asyncio.get_running_loop().create_future()


if __name__ == "__main__":
    asyncio.run(main())
