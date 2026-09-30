import json
import logging

import pytest

from livesubs import protocol as p
from livesubs.asr.fake import FakeTranscriber
from livesubs.ingest import TimeMap
from livesubs.metrics import Rolling, SessionMetrics

from .audio import concat, silence, speech, to_frames
from .test_streaming_asr import fake_client


def test_rolling_window() -> None:
    r = Rolling(window_s=10)
    r.add(100, now=0)
    r.add(300, now=5)
    assert r.percentile(50, now=6) == 200
    assert r.percentile(50, now=12) == 300
    assert Rolling().percentile(50) is None


def test_time_map_arrival() -> None:
    tm = TimeMap()
    tm.add(0, 0.0, arrival=100.0)
    tm.add(1600, 0.1, arrival=100.1)
    assert tm.arrival(1599) == 100.0
    assert tm.arrival(5000) == 100.1


def test_session_metrics_log_and_stats(caplog: pytest.LogCaptureFixture) -> None:
    m = SessionMetrics("s1")
    with caplog.at_level(logging.INFO, logger="livesubs.metrics"):
        m.on_final(3, vad_wait_ms=410, queue_ms=2, asr_ms=110, ja_ms=522)
        m.on_translation(3, mt_first_ms=80, mt_ms=300, en_ms=830)
    lines = [json.loads(r.message) for r in caplog.records]
    assert lines[0] == {
        "session": "s1",
        "seg_id": 3,
        "vad_wait_ms": 410,
        "queue_ms": 2,
        "asr_ms": 110,
        "ja_ms": 522,
    }
    stats = m.stats(queue_depth=1, gpu_busy=True)
    assert (stats.ja_ms_p50, stats.en_ms_p50, stats.mt_first_token_ms_p50) == (522, 830, 80)


def test_stats_message_is_sent() -> None:
    client = fake_client(FakeTranscriber(), stats_interval_s=0.05)
    with client, client.websocket_connect("/ws") as ws:
        ws.send_text(
            json.dumps({"type": "hello", "protocol_version": p.PROTOCOL_VERSION, "video_id": "v"})
        )
        ws.receive_json()
        for frame in to_frames(concat(speech(1), silence(1))):
            ws.send_bytes(p.encode_frame(frame))
        stats = None
        for _ in range(200):
            msg = ws.receive_json()
            if msg["type"] == "stats" and msg["ja_ms_p50"] is not None:
                stats = msg
                break
    assert stats is not None
    assert stats["asr_ms_p50"] >= 0
    assert stats["ja_ms_p50"] >= stats["asr_ms_p50"]
