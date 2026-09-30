import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from livesubs import protocol as p
from livesubs.app import create_app
from livesubs.config import Settings

from .audio import concat, silence, speech, to_frames


def hello(**kw: Any) -> str:
    return json.dumps(
        {"type": "hello", "protocol_version": p.PROTOCOL_VERSION, "video_id": "vid", **kw}
    )


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(Settings(vad_backend="energy", asr_backend="none")))


def recv(ws: WebSocketTestSession) -> dict[str, Any]:
    msg: dict[str, Any] = ws.receive_json()
    return msg


def open_session(ws: WebSocketTestSession, **kw: Any) -> dict[str, Any]:
    ws.send_text(hello(**kw))
    ready = recv(ws)
    assert ready["type"] == "ready"
    return ready


def send_audio(ws: WebSocketTestSession, audio: Any, **kw: Any) -> None:
    for frame in to_frames(audio, **kw):
        ws.send_bytes(p.encode_frame(frame))


def finals_until_pong(ws: WebSocketTestSession) -> list[dict[str, Any]]:
    ws.send_text(json.dumps({"type": "ping", "ts": 1.0}))
    out = []
    while (msg := recv(ws))["type"] != "pong":
        if msg["type"] == "final":
            out.append(msg)
    return out


def test_segments_in_media_time(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        open_session(ws)
        send_audio(ws, concat(silence(1), speech(2), silence(1)), media_t0=500.0)
        finals = finals_until_pong(ws)
    assert len(finals) == 1
    assert finals[0]["t0"] == pytest.approx(500.8, abs=0.05)
    assert finals[0]["t1"] == pytest.approx(503.2, abs=0.05)


def test_discontinuity_closes_segment(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        open_session(ws)
        send_audio(ws, concat(silence(0.5), speech(2)))
        assert finals_until_pong(ws) == []
        frames = list(to_frames(speech(0.5), start_idx=40_000, media_t0=900.0))
        ws.send_bytes(p.encode_frame(p.AudioFrame(**{**_fields(frames[0]), "discontinuity": True})))
        finals = finals_until_pong(ws)
    assert len(finals) == 1
    assert finals[0]["t1"] == pytest.approx(2.5, abs=0.05)


def _fields(f: p.AudioFrame) -> dict[str, Any]:
    return {"sample_idx": f.sample_idx, "media_time": f.media_time, "pcm": f.pcm}


def test_pause_closes_segment_and_resume_restarts(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        open_session(ws)
        send_audio(ws, concat(silence(0.5), speech(2)))
        ws.send_text('{"type":"pause"}')
        finals = finals_until_pong(ws)
        assert len(finals) == 1
        ws.send_text('{"type":"resume"}')
        send_audio(ws, concat(speech(1), silence(1)), start_idx=10 * 16000, media_t0=60.0)
        finals = finals_until_pong(ws)
    assert len(finals) == 1
    assert finals[0]["seg_id"] == 2
    assert finals[0]["t0"] == pytest.approx(60.0, abs=0.05)


def test_two_sessions_are_independent(client: TestClient) -> None:
    with client.websocket_connect("/ws") as a, client.websocket_connect("/ws") as b:
        ra, rb = open_session(a), open_session(b, video_id="other")
        assert ra["session_id"] != rb["session_id"]
        send_audio(a, concat(silence(0.5), speech(1)))
        send_audio(b, concat(silence(0.5), speech(1), silence(1)))
        assert len(finals_until_pong(b)) == 1
        assert finals_until_pong(a) == []  # a's segment is still open


def test_protocol_mismatch_is_fatal(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        ws.send_text(hello(protocol_version=p.PROTOCOL_VERSION + 1))
        err = recv(ws)
    assert err["type"] == "error" and err["code"] == "protocol_mismatch" and err["fatal"]


def test_token_is_checked() -> None:
    client = TestClient(
        create_app(Settings(vad_backend="energy", asr_backend="none", token="s3cret"))
    )
    with client.websocket_connect("/ws") as ws:
        ws.send_text(hello(token="wrong"))
        assert recv(ws)["code"] == "unauthorized"
    with client.websocket_connect("/ws") as ws:
        assert open_session(ws, token="s3cret")["type"] == "ready"


def test_bad_frame_is_reported_not_fatal(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        open_session(ws)
        ws.send_bytes(b"\x07" * 30)
        err = recv(ws)
        assert err["code"] == "bad_frame" and not err["fatal"]
        ws.send_text('{"type":"ping","ts":2}')
        assert recv(ws) == {"type": "pong", "ts": 2.0}


def test_health_counts_sessions(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        open_session(ws)
        assert client.get("/health").json()["sessions"] == 1
