import json
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from livesubs import protocol as p

SCHEMA_FILE = Path(p.__file__).with_name("protocol.schema.json")


def test_frame_roundtrip() -> None:
    pcm = (np.arange(p.FRAME_SAMPLES) - 800).astype(np.int16)
    frame = p.AudioFrame(sample_idx=123_456_789, media_time=4321.25, pcm=pcm, discontinuity=True)
    data = p.encode_frame(frame)
    assert len(data) == p.FRAME_HEADER_SIZE + 2 * p.FRAME_SAMPLES == 3220
    back = p.decode_frame(data)
    assert back.sample_idx == frame.sample_idx
    assert back.media_time == frame.media_time
    assert back.discontinuity
    np.testing.assert_array_equal(back.pcm, pcm)


def test_frame_layout_is_little_endian() -> None:
    data = p.encode_frame(p.AudioFrame(1, 0.5, np.array([1, -2], dtype=np.int16)))
    assert data[:4] == b"\x01\x00\x00\x00"
    assert data[4:12] == (1).to_bytes(8, "little")
    assert data[20:] == b"\x01\x00\xfe\xff"


@pytest.mark.parametrize(
    "data",
    [b"", b"\x01" * 10, b"\x02" + b"\x00" * 19, b"\x01" + b"\x00" * 19 + b"\x00"],
)
def test_bad_frames_rejected(data: bytes) -> None:
    with pytest.raises(p.FrameError):
        p.decode_frame(data)


def test_parse_client_messages() -> None:
    hello = p.parse_client_message('{"type":"hello","protocol_version":1,"video_id":"abc"}')
    assert isinstance(hello, p.Hello)
    assert hello.targets == ["ja", "en"]
    assert isinstance(p.parse_client_message('{"type":"pause"}'), p.Pause)
    with pytest.raises(ValidationError):
        p.parse_client_message('{"type":"hello","protocol_version":1}')
    with pytest.raises(ValidationError):
        p.parse_client_message('{"type":"final","seg_id":1}')  # server-only type
    with pytest.raises(ValidationError):
        p.parse_client_message('{"type":"pause","extra":1}')


def test_server_message_roundtrip() -> None:
    msg = p.Final(seg_id=3, ja="こんにちは", t0=1.0, t1=2.5, asr_ms=210.0)
    assert p.parse_server_message(p.dump(msg)) == msg


def test_schema_file_is_up_to_date() -> None:
    """If this fails: `just protocol-schema`, then mirror the change in protocol.ts."""
    committed = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    assert committed == p.describe_protocol()


def test_every_message_type_is_described() -> None:
    described = p.describe_protocol()["messages"]
    assert set(described) == {m.model_fields["type"].default for m in p.CLIENT_MESSAGES} | {
        m.model_fields["type"].default for m in p.SERVER_MESSAGES
    }
