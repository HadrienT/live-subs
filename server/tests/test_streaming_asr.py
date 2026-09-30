import asyncio
import itertools
import json
import threading
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from livesubs import protocol as p
from livesubs.app import Services, create_app, default_services
from livesubs.asr.agreement import LocalAgreement
from livesubs.asr.base import AsrResult
from livesubs.asr.fake import FakeTranscriber
from livesubs.asr.scheduler import GpuScheduler
from livesubs.config import Settings

from .audio import concat, silence, speech, to_frames

# ------------------------------------------------------------------ local agreement


def test_local_agreement_commits_common_prefix() -> None:
    la = LocalAgreement()
    assert la.update("きょうは") == ("", "きょうは")
    assert la.update("きょうはいい") == ("きょうは", "いい")
    assert la.update("きょうはいい天気") == ("きょうはいい", "天気")
    # the tail changes: what both decodings share is committed, the rest stays grey
    assert la.update("きょうはいい天候だ") == ("きょうはいい天", "候だ")


def test_local_agreement_never_retracts() -> None:
    la = LocalAgreement()
    la.update("あいうえお")
    la.update("あいうえおか")
    stable, unstable = la.update("あいかきく")
    assert stable == "あいうえお"
    assert unstable == "かきく"


# ------------------------------------------------------------------ scheduler


class GatedTranscriber:
    """Blocks inside transcribe() until released, to control queue contents."""

    name = "gated"

    def __init__(self) -> None:
        self.release = threading.Event()
        self.started = threading.Event()
        self.order: list[str] = []

    def transcribe(self, audio: np.ndarray, *, prompt: str | None) -> AsrResult:
        self.order.append(prompt or "")
        self.started.set()
        self.release.wait(5)
        return AsrResult(text=prompt or "")


async def test_finals_jump_ahead_and_partials_coalesce() -> None:
    gated = GatedTranscriber()
    sched = GpuScheduler(gated)
    audio = np.zeros(16000, dtype=np.float32)
    try:
        blocker = asyncio.create_task(sched.transcribe(audio, prompt="busy", final=True, key=0))
        await asyncio.to_thread(gated.started.wait, 5)
        p1 = asyncio.create_task(sched.transcribe(audio, prompt="p1", final=False, key=1))
        await asyncio.sleep(0.01)
        p2 = asyncio.create_task(sched.transcribe(audio, prompt="p2", final=False, key=1))
        f2 = asyncio.create_task(sched.transcribe(audio, prompt="f2", final=True, key=2))
        await asyncio.sleep(0.01)
        assert await p1 is None  # superseded by p2 while waiting
        assert sched.queue_depth == 2
        gated.release.set()
        await asyncio.gather(blocker, p2, f2)
        assert gated.order == ["busy", "f2", "p2"]  # the final overtook the partial
    finally:
        sched.close()


async def test_final_cancels_pending_partial_of_same_segment() -> None:
    gated = GatedTranscriber()
    sched = GpuScheduler(gated)
    audio = np.zeros(16000, dtype=np.float32)
    try:
        blocker = asyncio.create_task(sched.transcribe(audio, prompt="busy", final=True, key=0))
        await asyncio.to_thread(gated.started.wait, 5)
        part = asyncio.create_task(sched.transcribe(audio, prompt="p", final=False, key=7))
        await asyncio.sleep(0.01)
        fin = asyncio.create_task(sched.transcribe(audio, prompt="f", final=True, key=7))
        await asyncio.sleep(0.01)
        assert await part is None
        gated.release.set()
        timed = await fin
        await blocker
        assert timed is not None
        assert timed.result.text == "f"
        assert gated.order == ["busy", "f"]
    finally:
        sched.close()


# ------------------------------------------------------------------ end to end with a fake


def fake_client(transcriber: Any, **settings: Any) -> TestClient:
    s = Settings(
        **{"vad_backend": "energy", "asr_backend": "fake", "mt_backend": "none", **settings}
    )
    services: Services = default_services(s)
    services.load_transcriber = lambda: transcriber
    return TestClient(create_app(s, services))


def run_session(client: TestClient, audio: np.ndarray, **hello: Any) -> list[dict[str, Any]]:
    with client, client.websocket_connect("/ws") as ws:
        ws.send_text(
            json.dumps(
                {"type": "hello", "protocol_version": p.PROTOCOL_VERSION, "video_id": "v", **hello}
            )
        )
        assert ws.receive_json()["type"] == "ready"
        for frame in to_frames(audio):
            ws.send_bytes(p.encode_frame(frame))
        ws.send_text('{"type":"pause"}')
        return drain(ws)


def drain(ws: WebSocketTestSession, rounds: int = 3) -> list[dict[str, Any]]:
    """Ping until `rounds` pongs arrive with nothing else in between."""
    out: list[dict[str, Any]] = []
    quiet = 0
    while quiet < rounds:
        ws.send_text('{"type":"ping","ts":0}')
        got_other = False
        while (msg := ws.receive_json())["type"] != "pong":
            out.append(msg)
            got_other = True
        quiet = 0 if got_other else quiet + 1
        if not got_other:
            import time

            time.sleep(0.05)
    return out


def test_partials_then_final_per_sentence() -> None:
    fake = FakeTranscriber()
    msgs = run_session(
        fake_client(fake), concat(silence(0.5), speech(3.3), silence(1), speech(1.5), silence(1))
    )
    finals = [m for m in msgs if m["type"] == "final"]
    partials = [m for m in msgs if m["type"] == "partial"]
    assert [f["seg_id"] for f in finals] == [1, 2]
    assert all(f["ja"] for f in finals)
    assert {m["seg_id"] for m in partials} <= {1, 2}
    assert any(m["seg_id"] == 1 for m in partials)
    # a partial never follows the final of its segment
    for f in finals:
        idx = msgs.index(f)
        assert not any(m["type"] == "partial" and m["seg_id"] == f["seg_id"] for m in msgs[idx:])
    # stable text only grows
    stables = [m["ja_stable"] for m in partials if m["seg_id"] == 1]
    assert all(b.startswith(a) for a, b in itertools.pairwise(stables))


def test_prompt_carries_previous_finals() -> None:
    fake = FakeTranscriber(script=lambda a: "こんにちは")
    run_session(fake_client(fake), concat(speech(1), silence(1), speech(1), silence(1)))
    prompts = [prompt for _, prompt in fake.calls]
    assert prompts[0] is None
    assert "こんにちは" in (prompts[-1] or "")


def test_prompt_can_be_disabled() -> None:
    fake = FakeTranscriber(script=lambda a: "こんにちは")
    run_session(
        fake_client(fake, asr_use_prompt=False),
        concat(speech(1), silence(1), speech(1), silence(1)),
    )
    assert all(prompt is None for _, prompt in fake.calls)


def test_hallucinations_are_dropped() -> None:
    fake = FakeTranscriber(script=lambda a: "ご視聴ありがとうございました")
    msgs = run_session(fake_client(fake), concat(speech(2), silence(1)))
    assert not [m for m in msgs if m["type"] in ("final", "partial") and m.get("ja", "x")]


def test_low_confidence_sparse_output_dropped_and_partials_retracted() -> None:
    # kotoba-whisper on music: a short word for seconds of audio, avg_logprob ≈ -0.45
    fake = FakeTranscriber(
        script=lambda a: "ごめん" if len(a) > 3 * 16000 else "ごめんね" * 3, avg_logprob=-0.45
    )
    msgs = run_session(fake_client(fake), concat(speech(4), silence(1)))
    finals = [m for m in msgs if m["type"] == "final"]
    assert len(finals) == 1
    assert finals[0]["ja"] == ""  # retracts the partials already shown


def test_show_partials_off() -> None:
    fake = FakeTranscriber()
    client = fake_client(fake)
    with client, client.websocket_connect("/ws") as ws:
        ws.send_text(
            json.dumps({"type": "hello", "protocol_version": p.PROTOCOL_VERSION, "video_id": "v"})
        )
        ws.receive_json()
        ws.send_text('{"type":"config","show_partials":false}')
        for frame in to_frames(concat(speech(3), silence(1))):
            ws.send_bytes(p.encode_frame(frame))
        msgs = drain(ws)
    assert not [m for m in msgs if m["type"] == "partial"]
    assert [m for m in msgs if m["type"] == "final"]


@pytest.mark.parametrize("backend", ["none", "fake"])
def test_health_reports_asr(backend: str) -> None:
    client = TestClient(
        create_app(Settings(vad_backend="energy", asr_backend=backend, mt_backend="none"))
    )
    with client:
        asr = client.get("/health").json()["asr"]
    assert asr["warm"] is True
