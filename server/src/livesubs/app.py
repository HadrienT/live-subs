"""FastAPI application: ``/ws`` for sessions, ``/health`` for diagnostics."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from livesubs import __version__
from livesubs import protocol as p
from livesubs.config import Settings, get_settings
from livesubs.session import SegmentSink, Session, VadOnlySink
from livesubs.vad import EnergyVad, SileroVad, SpeechProbModel

log = logging.getLogger(__name__)


@dataclass
class Services:
    """Everything shared between sessions. Tests build it with fakes."""

    settings: Settings
    make_vad: Callable[[], SpeechProbModel]
    make_sink: Callable[[p.Hello], SegmentSink]
    asr_model: str = "none"
    mt_model: str | None = None
    sessions: dict[str, Session] = field(default_factory=dict)


def default_services(settings: Settings) -> Services:
    make_vad: Callable[[], SpeechProbModel] = (
        EnergyVad if settings.vad_backend == "energy" else SileroVad
    )
    return Services(settings=settings, make_vad=make_vad, make_sink=lambda _hello: VadOnlySink())


class _Closed(Exception):
    pass


async def _serve_session(ws: WebSocket, services: Services) -> None:
    settings = services.settings
    outbox: asyncio.Queue[p.ServerMessage | None] = asyncio.Queue()

    async def send_now(msg: p.ServerMessage) -> None:
        await ws.send_text(p.dump(msg))

    async def fatal(code: p.ErrorCode, message: str) -> None:
        await send_now(p.Error(code=code, message=message, fatal=True))
        await ws.close(code=1008)
        raise _Closed

    # --- hello
    try:
        raw = await asyncio.wait_for(ws.receive_text(), settings.hello_timeout_s)
        first = p.parse_client_message(raw)
    except (TimeoutError, ValidationError, KeyError):
        await fatal("bad_message", "expected a hello message first")
        return
    if not isinstance(first, p.Hello):
        await fatal("not_ready", "expected a hello message first")
        return
    if first.protocol_version != p.PROTOCOL_VERSION:
        await fatal(
            "protocol_mismatch",
            f"server speaks protocol {p.PROTOCOL_VERSION}, client {first.protocol_version}: "
            "update the extension",
        )
    if settings.token and first.token != settings.token:
        await fatal("unauthorized", "bad or missing token")

    session = Session(
        first,
        vad=services.make_vad(),
        vad_params=settings.vad_params(),
        sink=services.make_sink(first),
        emit=outbox.put_nowait,
        ring_seconds=settings.ring_seconds,
    )
    services.sessions[session.id] = session
    log.info("session %s: video=%s channel=%s", session.id, first.video_id, first.channel_id)
    await send_now(
        p.Ready(session_id=session.id, asr_model=services.asr_model, mt_model=services.mt_model)
    )

    async def sender() -> None:
        while (msg := await outbox.get()) is not None:
            await ws.send_text(p.dump(msg))

    sender_task = asyncio.create_task(sender())
    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            if (data := message.get("bytes")) is not None:
                try:
                    session.on_frame(p.decode_frame(data))
                except p.FrameError as e:
                    outbox.put_nowait(p.Error(code="bad_frame", message=str(e)))
            elif (text := message.get("text")) is not None:
                try:
                    msg = p.parse_client_message(text)
                except ValidationError as e:
                    outbox.put_nowait(p.Error(code="bad_message", message=str(e)[:200]))
                    continue
                match msg:
                    case p.Pause():
                        session.on_pause()
                    case p.Resume():
                        session.on_resume()
                    case p.Config():
                        session.on_config(msg)
                    case p.Ping(ts=ts):
                        outbox.put_nowait(p.Pong(ts=ts))
                    case p.Hello():
                        outbox.put_nowait(
                            p.Error(code="bad_message", message="one hello per connection")
                        )
    finally:
        services.sessions.pop(session.id, None)
        await session.close()
        outbox.put_nowait(None)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(sender_task, 2.0)
        sender_task.cancel()
        log.info("session %s closed", session.id)


def create_app(settings: Settings | None = None, services: Services | None = None) -> FastAPI:
    settings = settings or get_settings()
    services = services or default_services(settings)
    app = FastAPI(title="live-subs", version=__version__)
    app.state.services = services

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "protocol_version": p.PROTOCOL_VERSION,
            "sessions": len(services.sessions),
        }

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        with contextlib.suppress(WebSocketDisconnect, _Closed):
            await _serve_session(ws, services)

    return app
