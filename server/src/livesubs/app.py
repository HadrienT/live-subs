"""FastAPI application: ``/ws`` for sessions, ``/health`` for diagnostics."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from livesubs import __version__
from livesubs import protocol as p
from livesubs.asr.base import Transcriber
from livesubs.asr.fake import FakeTranscriber
from livesubs.asr.scheduler import GpuScheduler
from livesubs.config import Settings, get_settings
from livesubs.gpu import memory as gpu_memory
from livesubs.gpu import require_gpu
from livesubs.mt.base import Translator
from livesubs.mt.fake import FakeTranslator
from livesubs.mt.glossary import GlossaryStore
from livesubs.mt.llama import LlamaServerTranslator
from livesubs.mt.worker import TranslationWorker
from livesubs.pipeline import StreamingAsrSink
from livesubs.session import SegmentSink, Session, VadOnlySink
from livesubs.vad import EnergyVad, SileroVad, SpeechProbModel

log = logging.getLogger(__name__)


@dataclass
class Services:
    """Everything shared between sessions. Tests build it with fakes."""

    settings: Settings
    make_vad: Callable[[], SpeechProbModel]
    load_transcriber: Callable[[], Transcriber] | None = None
    asr_model: str = "none"
    asr_device: str = "none"
    translator: Translator | None = None
    glossaries: GlossaryStore = field(default_factory=lambda: GlossaryStore(None))
    scheduler: GpuScheduler | None = None
    sessions: dict[str, Session] = field(default_factory=dict)

    @property
    def warm(self) -> bool:
        return self.load_transcriber is None or self.scheduler is not None

    async def start(self) -> None:
        """Load and warm the ASR model before accepting sessions."""
        if self.load_transcriber is not None and self.scheduler is None:
            transcriber = await asyncio.to_thread(self.load_transcriber)
            self.asr_device = getattr(transcriber, "device", self.asr_device)
            warmup = getattr(transcriber, "warmup", None)
            if warmup is not None:
                await asyncio.to_thread(warmup)
            self.scheduler = GpuScheduler(transcriber)
            log.info("ASR ready: %s", transcriber.name)

    async def stop(self) -> None:
        if self.scheduler is not None:
            await asyncio.to_thread(self.scheduler.close)
        if self.translator is not None:
            await self.translator.aclose()

    @property
    def mt_model(self) -> str | None:
        return self.translator.name if self.translator is not None else None

    def make_sink(self, hello: p.Hello) -> SegmentSink:
        if self.load_transcriber is None:
            return VadOnlySink()
        if self.scheduler is None:
            raise NotReadyError("the ASR model is still loading")
        glossary = self.glossaries.get(hello.channel_id)
        worker = None
        if self.translator is not None:
            s = self.settings
            worker = TranslationWorker(
                self.translator,
                glossary=glossary,
                history_len=s.mt_history,
                merge_after=s.mt_merge_after,
                timeout_s=s.mt_timeout_s,
            )
        return StreamingAsrSink(
            self.scheduler,
            options=self.settings.asr_options(),
            prompt_terms=glossary.asr_prompt(),
            translation=worker,
        )


class NotReadyError(Exception):
    pass


def build_transcriber(settings: Settings) -> Callable[[], Transcriber] | None:
    match settings.asr_backend:
        case "none":
            return None
        case "fake":
            return FakeTranscriber
        case "faster-whisper":

            def load() -> Transcriber:
                from livesubs.asr.faster_whisper import FasterWhisperTranscriber

                device = require_gpu(settings.asr_device, allow_cpu=settings.allow_cpu)
                return FasterWhisperTranscriber(
                    settings.asr_model,
                    device=device,
                    compute_type=settings.asr_compute_type if device != "cpu" else "int8",
                    beam_size=settings.asr_beam_size,
                    local_files_only=settings.asr_local_only,
                    revision=settings.asr_revision,
                )

            return load
        case other:
            raise ValueError(f"unknown LIVESUBS_ASR_BACKEND {other!r}")


def build_translator(settings: Settings) -> Translator | None:
    match settings.mt_backend:
        case "none":
            return None
        case "fake":
            return FakeTranslator()
        case "llama":
            return LlamaServerTranslator(
                settings.llm_base_url,
                settings.llm_model,
                temperature=settings.llm_temperature,
                max_tokens=settings.llm_max_tokens,
                disable_thinking=settings.llm_disable_thinking,
            )
        case other:
            raise ValueError(f"unknown LIVESUBS_MT_BACKEND {other!r}")


def default_services(settings: Settings) -> Services:
    make_vad: Callable[[], SpeechProbModel] = (
        EnergyVad if settings.vad_backend == "energy" else SileroVad
    )
    load = build_transcriber(settings)
    return Services(
        settings=settings,
        make_vad=make_vad,
        load_transcriber=load,
        asr_model=settings.asr_model if load is not None else "none",
        asr_device=settings.asr_device if load is not None else "none",
        translator=build_translator(settings) if load is not None else None,
        glossaries=GlossaryStore(settings.glossary_dir),
    )


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

    try:
        sink = services.make_sink(first)
    except NotReadyError as e:
        await fatal("not_ready", str(e))
        return
    session = Session(
        first,
        vad=services.make_vad(),
        vad_params=settings.vad_params(),
        sink=sink,
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

    async def stats_loop() -> None:
        while True:
            await asyncio.sleep(settings.stats_interval_s)
            sched = services.scheduler
            outbox.put_nowait(
                session.metrics.stats(
                    queue_depth=sched.queue_depth if sched else 0,
                    gpu_busy=sched.busy if sched else False,
                )
            )

    sender_task = asyncio.create_task(sender())
    stats_task = asyncio.create_task(stats_loop())
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
        stats_task.cancel()
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

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await services.start()
        yield
        await services.stop()

    app = FastAPI(title="live-subs", version=__version__, lifespan=lifespan)
    app.state.services = services

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "protocol_version": p.PROTOCOL_VERSION,
            "asr": {
                "model": services.asr_model,
                "device": services.asr_device,
                "warm": services.warm,
            },
            "gpu": await asyncio.to_thread(gpu_memory, services.asr_device),
            "llm": await llm_health(),
            "sessions": len(services.sessions),
        }

    async def llm_health() -> dict[str, Any] | None:
        if services.translator is None:
            return None
        status = await services.translator.status()
        return {
            "base_url": settings.llm_base_url,
            "model": services.translator.name,
            "reachable": status.reachable,
            "active": status.active,
            "served": status.served,
        }

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        with contextlib.suppress(WebSocketDisconnect, _Closed):
            await _serve_session(ws, services)

    return app
