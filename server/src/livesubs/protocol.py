"""Wire protocol between the extension and the server — the source of truth.

``extension/src/protocol.ts`` mirrors this file by hand. Any change here goes in
the same commit as the TS change, bumps ``PROTOCOL_VERSION`` and regenerates
``protocol.schema.json`` (``just protocol-schema``). The drift tests on both sides
fail otherwise.

Transport: one WebSocket per capturing tab. Client → server: JSON text control
messages and binary audio frames. Server → client: JSON only.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from typing import Annotated, Any, Literal, get_args

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

PROTOCOL_VERSION = 1
SAMPLE_RATE = 16_000
FRAME_SAMPLES = 1_600  # nominal frame: 100 ms

# --------------------------------------------------------------------------- frames

FRAME_KIND_AUDIO = 0x01
FLAG_DISCONTINUITY = 0x01
_HEADER = struct.Struct("<BBHQd")  # kind, flags, reserved, sample_idx, media_time
FRAME_HEADER_SIZE = _HEADER.size  # 20 bytes


class FrameError(ValueError):
    """A binary frame that does not follow the layout."""


@dataclass(frozen=True, slots=True)
class AudioFrame:
    sample_idx: int
    media_time: float
    pcm: NDArray[np.int16]
    discontinuity: bool = False

    @property
    def flags(self) -> int:
        return FLAG_DISCONTINUITY if self.discontinuity else 0


def encode_frame(frame: AudioFrame) -> bytes:
    header = _HEADER.pack(FRAME_KIND_AUDIO, frame.flags, 0, frame.sample_idx, frame.media_time)
    return header + frame.pcm.astype("<i2", copy=False).tobytes()


def decode_frame(data: bytes) -> AudioFrame:
    if len(data) < FRAME_HEADER_SIZE:
        raise FrameError(f"frame too short: {len(data)} bytes")
    kind, flags, _reserved, sample_idx, media_time = _HEADER.unpack_from(data)
    if kind != FRAME_KIND_AUDIO:
        raise FrameError(f"unknown frame kind 0x{kind:02x}")
    payload = data[FRAME_HEADER_SIZE:]
    if len(payload) % 2:
        raise FrameError("odd PCM payload length")
    pcm = np.frombuffer(payload, dtype="<i2").astype(np.int16)
    return AudioFrame(sample_idx, media_time, pcm, bool(flags & FLAG_DISCONTINUITY))


# --------------------------------------------------------------------------- messages

Lang = Literal["ja", "en"]


DEFAULT_TARGETS: tuple[Lang, ...] = ("ja", "en")


class _Message(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# client → server


class Hello(_Message):
    type: Literal["hello"] = "hello"
    protocol_version: int
    token: str | None = None
    video_id: str
    channel_id: str | None = None
    title: str | None = None
    targets: list[Lang] = Field(default_factory=lambda: list(DEFAULT_TARGETS))


class Pause(_Message):
    type: Literal["pause"] = "pause"


class Resume(_Message):
    type: Literal["resume"] = "resume"


class Config(_Message):
    type: Literal["config"] = "config"
    targets: list[Lang] | None = None
    show_partials: bool | None = None


class Ping(_Message):
    type: Literal["ping"] = "ping"
    ts: float


# server → client


class Ready(_Message):
    type: Literal["ready"] = "ready"
    session_id: str
    asr_model: str
    mt_model: str | None
    sample_rate: int = SAMPLE_RATE


class Partial(_Message):
    """Provisional transcript of the open segment: stable prefix + tail that may change."""

    type: Literal["partial"] = "partial"
    seg_id: int
    ja_stable: str
    ja_unstable: str
    t0: float
    t1: float


class Final(_Message):
    """Definitive transcript; replaces every ``partial`` with the same ``seg_id``."""

    type: Literal["final"] = "final"
    seg_id: int
    ja: str
    t0: float
    t1: float
    asr_ms: float


class TranslationDelta(_Message):
    type: Literal["translation_delta"] = "translation_delta"
    seg_id: int
    en_delta: str


class Translation(_Message):
    """Final translation of ``seg_id``. ``merged`` lists earlier segments folded into
    the same request when translation fell behind (their own EN line stays empty)."""

    type: Literal["translation"] = "translation"
    seg_id: int
    en: str
    mt_ms: float
    merged: list[int] = Field(default_factory=list)


class Stats(_Message):
    """Sent every few seconds. Latencies are rolling, in ms, measured from end of speech."""

    type: Literal["stats"] = "stats"
    queue_depth: int
    gpu_busy: bool
    asr_ms_p50: float | None = None
    asr_ms_p95: float | None = None
    ja_ms_p50: float | None = None
    ja_ms_p95: float | None = None
    en_ms_p50: float | None = None
    en_ms_p95: float | None = None
    mt_first_token_ms_p50: float | None = None


ErrorCode = Literal[
    "protocol_mismatch",
    "unauthorized",
    "bad_message",
    "bad_frame",
    "not_ready",
    "mt_timeout",
    "mt_unreachable",
    "mt_model_inactive",
    "internal",
]


class Error(_Message):
    """``fatal=True``: the server closes the connection after sending it."""

    type: Literal["error"] = "error"
    code: ErrorCode
    message: str
    fatal: bool = False
    seg_id: int | None = None


class Pong(_Message):
    type: Literal["pong"] = "pong"
    ts: float


ClientMessage = Annotated[Hello | Pause | Resume | Config | Ping, Field(discriminator="type")]
ServerMessage = Annotated[
    Ready | Partial | Final | TranslationDelta | Translation | Stats | Error | Pong,
    Field(discriminator="type"),
]

_client_adapter: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)
_server_adapter: TypeAdapter[ServerMessage] = TypeAdapter(ServerMessage)

CLIENT_MESSAGES: tuple[type[_Message], ...] = (Hello, Pause, Resume, Config, Ping)
SERVER_MESSAGES: tuple[type[_Message], ...] = (
    Ready,
    Partial,
    Final,
    TranslationDelta,
    Translation,
    Stats,
    Error,
    Pong,
)


def parse_client_message(raw: str | bytes) -> ClientMessage:
    """Raises ``pydantic.ValidationError`` on anything that is not a known client message."""
    return _client_adapter.validate_json(raw)


def parse_server_message(raw: str | bytes) -> ServerMessage:
    return _server_adapter.validate_json(raw)


def dump(message: _Message) -> str:
    return message.model_dump_json()


# --------------------------------------------------------------------------- drift schema


def _field_type(prop: dict[str, Any]) -> tuple[str, bool]:
    """Reduce a JSON-schema property to (type, nullable)."""
    if "anyOf" in prop:
        kinds = [p for p in prop["anyOf"] if p.get("type") != "null"]
        nullable = len(kinds) < len(prop["anyOf"])
        return _field_type(kinds[0])[0], nullable
    if "const" in prop or "enum" in prop:
        return "string", False
    return str(prop["type"]), False


def describe_protocol() -> dict[str, Any]:
    """Language-neutral description of the protocol, compared by the TS drift test."""
    messages: dict[str, Any] = {}
    for direction, models in (("c2s", CLIENT_MESSAGES), ("s2c", SERVER_MESSAGES)):
        for model in models:
            schema = model.model_json_schema()
            required = set(schema.get("required", []))
            fields: dict[str, Any] = {}
            for name, prop in schema["properties"].items():
                if name == "type":
                    continue
                kind, nullable = _field_type(prop)
                fields[name] = {
                    "type": kind,
                    "required": name in required,
                    "nullable": nullable,
                }
            msg_type = model.model_fields["type"].default
            messages[msg_type] = {"direction": direction, "fields": fields}
    return {
        "protocol_version": PROTOCOL_VERSION,
        "sample_rate": SAMPLE_RATE,
        "frame": {
            "header_size": FRAME_HEADER_SIZE,
            "kind_audio": FRAME_KIND_AUDIO,
            "flag_discontinuity": FLAG_DISCONTINUITY,
        },
        "error_codes": list(get_args(ErrorCode)),
        "messages": messages,
    }


if __name__ == "__main__":
    print(json.dumps(describe_protocol(), indent=2, ensure_ascii=False))
