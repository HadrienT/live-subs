import json
import threading
import time
from typing import Any

import numpy as np
from fastapi.testclient import TestClient

from livesubs import protocol as p
from livesubs.ahead.source import ArraySource, SourceError, watch_url
from livesubs.app import create_app, default_services
from livesubs.asr.fake import FakeTranscriber
from livesubs.config import Settings

from .audio import to_frames
from .test_align import talk

SPEED = 4.0
PLAYER_DELAY_S = 5.0  # the player is 5 s (stream time) behind the live edge
MEDIA_BASE = 1000.0  # video time = stream time + 1000 (DVR timelines start anywhere)


def client_for(source_factory: Any, **settings: Any) -> TestClient:
    s = Settings(
        **{
            "vad_backend": "energy",
            "asr_backend": "fake",
            "mt_backend": "fake",
            "ahead_capture_window_s": 4.0,
            "stats_interval_s": 60.0,
            **settings,
        }
    )
    services = default_services(s)
    services.load_transcriber = FakeTranscriber
    services.make_source = source_factory
    return TestClient(create_app(s, services))


def hello(mode: str) -> str:
    return json.dumps(
        {
            "type": "hello",
            "protocol_version": p.PROTOCOL_VERSION,
            "video_id": "dQw4w9WgXcQ",
            "mode": mode,
        }
    )


def test_only_youtube_ids_are_pulled() -> None:
    assert watch_url("dQw4w9WgXcQ") == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    for bad in ("http://evil/", "abc", "dQw4w9WgXcQ; rm -rf /"):
        try:
            watch_url(bad)
        except SourceError:
            continue
        raise AssertionError(bad)


def test_ahead_mode_aligns_and_subtitles_arrive_before_the_player() -> None:
    live = talk(36, seed=11)
    # this simulated live pauses 0.1–0.8 s: keep capture-mode cuts so sentences stay
    # shorter than the 5 s lead (the 800 ms default has its own test below)
    client = client_for(lambda _vid: ArraySource(live, speed=SPEED), ahead_min_silence_ms=400)
    received: list[tuple[float, dict[str, Any]]] = []
    sent_media = [0.0]
    with client, client.websocket_connect("/ws") as ws:
        ws.send_text(hello("ahead"))
        assert ws.receive_json()["type"] == "ready"
        start = time.monotonic()

        def player() -> None:
            # The player hears the live PLAYER_DELAY_S late, at the same pace.
            for frame in to_frames(live[: (28 * 16000)], media_t0=MEDIA_BASE):
                stream_t = frame.media_time - MEDIA_BASE
                due = start + (stream_t + PLAYER_DELAY_S) / SPEED
                if (d := due - time.monotonic()) > 0:
                    time.sleep(d)
                ws.send_bytes(p.encode_frame(frame))
                sent_media[0] = frame.media_time

        t = threading.Thread(target=player)
        t.start()
        while t.is_alive() or len(received) < 5:
            msg = ws.receive_json()
            received.append((sent_media[0], msg))
            if time.monotonic() - start > 15:
                break
        t.join()
    statuses = [m for _, m in received if m["type"] == "ahead_status"]
    aligned = [s for s in statuses if s["state"] == "aligned"]
    assert aligned, statuses
    assert abs(aligned[0]["offset_s"] + MEDIA_BASE) < 0.05  # stream − video
    assert 3.0 < aligned[0]["lead_s"] < 7.0
    finals = [(sent, m) for sent, m in received if m["type"] == "final"]
    assert finals
    assert all(m["seg_id"] > 1_000_000 for _, m in finals)  # from the pulled live
    # in video time, and ready before the player got there
    assert all(m["t0"] >= MEDIA_BASE for _, m in finals)
    assert any(m["t0"] > sent for sent, m in finals)
    assert any(m["type"] == "translation" for _, m in received)


def test_fallback_to_capture_when_the_live_cannot_be_pulled() -> None:
    def broken(_vid: str) -> Any:
        raise SourceError("yt-dlp not found")

    client = client_for(broken)
    with client, client.websocket_connect("/ws") as ws:
        ws.send_text(hello("ahead"))
        assert ws.receive_json()["type"] == "ready"
        status = ws.receive_json()
        assert status == {
            "type": "ahead_status",
            "state": "failed",
            "offset_s": None,
            "lead_s": None,
            "message": "yt-dlp not found",
        }
        for frame in to_frames(np.concatenate([talk(3, seed=2), np.zeros(16000, np.float32)])):
            ws.send_bytes(p.encode_frame(frame))
        ws.send_text('{"type":"pause"}')
        msgs: list[dict[str, Any]] = []
        while not any(m["type"] == "final" for m in msgs):
            msgs.append(ws.receive_json())
    assert any(m["type"] == "final" and m["seg_id"] < 1_000_000 for m in msgs)


def test_fallback_when_alignment_never_succeeds() -> None:
    unrelated = talk(40, seed=21)
    client = client_for(lambda _vid: ArraySource(unrelated, speed=SPEED), ahead_fail_after_s=2.0)
    with client, client.websocket_connect("/ws") as ws:
        ws.send_text(hello("ahead"))
        ws.receive_json()
        failed = None
        for frame in to_frames(talk(12, seed=22)):
            ws.send_bytes(p.encode_frame(frame))
            time.sleep(0.1 / SPEED)
        ws.send_text('{"type":"ping","ts":0}')
        for _ in range(200):
            msg = ws.receive_json()
            if msg["type"] == "ahead_status" and msg["state"] == "failed":
                failed = msg
                break
    assert failed is not None
    assert "align" in failed["message"]


def test_inner_session_waits_longer_and_skips_partials() -> None:
    from livesubs import protocol as proto
    from livesubs.app import start_ahead
    from livesubs.session import Session, VadOnlySink
    from livesubs.vad import EnergyVad, VadParams

    s = Settings(
        vad_backend="energy", asr_backend="fake", mt_backend="none", ahead_min_silence_ms=900
    )
    services = default_services(s)
    services.load_transcriber = FakeTranscriber
    services.make_source = lambda _vid: ArraySource(np.zeros(16000, np.float32), speed=0)
    hello_msg = proto.Hello(
        protocol_version=proto.PROTOCOL_VERSION, video_id="dQw4w9WgXcQ", mode="ahead"
    )

    async def run() -> None:
        await services.start()
        outer = Session(
            hello_msg,
            vad=EnergyVad(),
            vad_params=VadParams(),
            sink=VadOnlySink(),
            emit=lambda m: None,
        )
        ctl = start_ahead(hello_msg, outer, services)
        assert ctl is not None
        assert ctl.inner.show_partials is False
        assert ctl.inner._params.min_silence_ms == 900
        assert outer._params.min_silence_ms == 400  # capture mode unchanged
        await ctl.aclose()
        await services.stop()

    import asyncio

    asyncio.run(run())
