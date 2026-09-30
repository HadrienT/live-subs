"""Golden replay (WP12 §3): the free fixtures through the real ASR, compared with
the reviewed reference outputs.

    just test-gpu          # compare
    just golden-update     # regenerate *.golden.json — review the diff before committing

Checks per fixture: CER of the joined finals ≤ reference CER + 2 points, number
of segments within ±20 %, zero final over a music-only stretch, JA latency p95
within the budget (1.5 s after the end of the VAD segment, real-time replay).
Translation uses FakeTranslator: LLM output is not deterministic enough for a
golden file; it only checks that every final gets its translation.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import uvicorn

from livesubs.app import create_app
from livesubs.cer import cer
from livesubs.config import Settings
from livesubs.replay import load_audio, replay

pytestmark = [pytest.mark.gpu, pytest.mark.e2e]

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "golden"
WAVS = sorted(FIXTURES.glob("*.wav"))
UPDATE = os.environ.get("LIVESUBS_GOLDEN_UPDATE") == "1"
CER_MARGIN = 0.02
SEGMENTS_TOLERANCE = 0.20
JA_P95_BUDGET_MS = 1500.0


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def server_url() -> Iterator[str]:
    port = free_port()
    settings = Settings(host="127.0.0.1", port=port, mt_backend="fake", log_level="warning")
    server = uvicorn.Server(
        uvicorn.Config(create_app(settings), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 180
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError("server did not start")
        time.sleep(0.2)
    yield f"ws://127.0.0.1:{port}/ws"
    server.should_exit = True
    thread.join(timeout=10)


def overlaps(seg: dict[str, Any], span: dict[str, Any], margin: float = 0.3) -> bool:
    return bool(seg["t0"] < span["t1"] - margin and seg["t1"] > span["t0"] + margin)


@pytest.mark.parametrize("wav", WAVS, ids=[w.stem for w in WAVS])
def test_golden(wav: Path, server_url: str) -> None:
    ref = json.loads(wav.with_suffix(".ref.json").read_text(encoding="utf-8"))
    golden_path = wav.with_suffix(".golden.json")
    result = asyncio.run(replay(server_url, load_audio(str(wav)), speed=1.0, drain_s=10))
    finals = [f for f in result.finals() if f["ja"]]
    text = "".join(f["ja"] for f in finals)
    measured = {
        "cer": round(cer(["".join(u["text"] for u in ref["utterances"])], [text]), 4),
        "segments": len(finals),
        "ja_ms_p95": result.summary()["ja_ms_p95"],
        "finals": [{k: f[k] for k in ("seg_id", "ja", "t0", "t1")} for f in finals],
    }

    # Invariants, golden or not
    in_music = [f for f in finals for m in ref["music_only"] if overlaps(f, m)]
    assert in_music == [], f"finals over music-only stretches: {in_music}"
    assert measured["ja_ms_p95"] is not None
    assert measured["ja_ms_p95"] <= JA_P95_BUDGET_MS
    assert set(result.translations()) == {f["seg_id"] for f in finals}

    if UPDATE or not golden_path.exists():
        golden_path.write_text(
            json.dumps(measured, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
        if not UPDATE:
            pytest.skip(f"wrote {golden_path.name}: review it, then commit")
        return
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    assert measured["cer"] <= golden["cer"] + CER_MARGIN, (measured["cer"], golden["cer"])
    lo = golden["segments"] * (1 - SEGMENTS_TOLERANCE)
    hi = golden["segments"] * (1 + SEGMENTS_TOLERANCE)
    assert lo <= measured["segments"] <= hi, (measured["segments"], golden["segments"])
