import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from livesubs import protocol as p
from livesubs.asr.fake import FakeTranscriber
from livesubs.mt.base import TranslationContext, build_messages
from livesubs.mt.fake import FakeTranslator
from livesubs.mt.glossary import Glossary, GlossaryStore
from livesubs.mt.llama import LlamaServerTranslator, MtUnreachableError
from livesubs.mt.worker import TranslationWorker
from livesubs.segments import FinalSegment

from .audio import concat, silence, speech, to_frames
from .test_streaming_asr import drain, fake_client

GLOSSARY = Glossary(name="Test Channel", terms={"ぺこら": "Pekora", "兎田ぺこら": "Usada Pekora"})


def seg(i: int, ja: str = "こんにちは") -> FinalSegment:
    return FinalSegment(i, ja, float(i), i + 1.0, speech_end_at=0.0)


# ------------------------------------------------------------------ glossary & prompt


def test_glossary_store(tmp_path: Path) -> None:
    (tmp_path / "UCabc.toml").write_text(
        'name = "Pekora Ch."\n[terms]\n"ぺこら" = "Pekora"\n"兎田ぺこら" = "Usada Pekora"\n',
        encoding="utf-8",
    )
    store = GlossaryStore(tmp_path)
    g = store.get("UCabc")
    assert g.name == "Pekora Ch."
    assert g.terms["ぺこら"] == "Pekora"
    assert g.asr_prompt() == "兎田ぺこら、ぺこら"
    assert store.get("unknown").terms == {}
    assert store.get("../etc/passwd").terms == {}
    assert store.get(None).terms == {}


def test_prompt_has_history_turns_and_relevant_glossary() -> None:
    ctx = TranslationContext(
        history=[("おはよう", "Good morning")], glossary=GLOSSARY, channel_name="Test Channel"
    )
    messages = build_messages("ぺこらです", ctx)
    assert messages[0]["role"] == "system"
    assert "ぺこら → Pekora" in messages[0]["content"]
    assert "兎田ぺこら" not in messages[0]["content"]  # not in the text: not listed
    assert messages[1:] == [
        {"role": "user", "content": "おはよう"},
        {"role": "assistant", "content": "Good morning"},
        {"role": "user", "content": "ぺこらです"},
    ]


# ------------------------------------------------------------------ llama-server client


def sse(*deltas: str) -> str:
    chunks = [{"choices": [{"delta": {"content": d}}]} for d in deltas]
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"


def llama(handler: Any, model: str = "translate") -> LlamaServerTranslator:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return LlamaServerTranslator("http://llm/v1", model, client=client)


async def test_llama_streams_one_line() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, text=sse(" Hello", ", every", "one!\nNote: x", " more"))

    tr = llama(handler)
    out = [piece async for piece in tr.translate(seg(1), TranslationContext())]
    assert "".join(out) == "Hello, everyone!"
    assert seen["stream"] is True
    assert seen["model"] == "translate"
    assert seen["chat_template_kwargs"] == {"enable_thinking": False}
    assert seen["max_tokens"] == 120


async def test_llama_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "Qwen3-Coder-30B-A3B-Instruct"}]})

    status = await llama(handler).status()
    assert status.reachable
    assert not status.active
    assert status.served == ["Qwen3-Coder-30B-A3B-Instruct"]
    assert (await llama(handler, "Qwen3-Coder-30B-A3B-Instruct").status()).active


async def test_llama_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    tr = llama(handler)
    assert not (await tr.status()).reachable
    with pytest.raises(MtUnreachableError):
        [piece async for piece in tr.translate(seg(1), TranslationContext())]


# ------------------------------------------------------------------ worker


class StubSession:
    def __init__(self) -> None:
        self.sent: list[p.ServerMessage] = []
        self.targets = ["ja", "en"]

    def emit(self, msg: p.ServerMessage) -> None:
        self.sent.append(msg)

    def of(self, kind: type) -> list[Any]:
        return [m for m in self.sent if isinstance(m, kind)]


async def run_worker(
    worker: TranslationWorker, segs: list[FinalSegment], wait: float = 0.5
) -> StubSession:
    session = StubSession()
    for s in segs:
        worker.submit(session, s)  # type: ignore[arg-type]
    await asyncio.sleep(wait)
    await worker.aclose()
    return session


async def test_worker_in_order_with_deltas_and_history() -> None:
    fake = FakeTranslator()
    session = await run_worker(TranslationWorker(fake), [seg(1, "いち"), seg(2, "に")], 0.1)
    assert [t.seg_id for t in session.of(p.Translation)] == [1, 2]
    assert session.of(p.Translation)[0].en == "EN(いち)"
    assert "".join(d.en_delta for d in session.of(p.TranslationDelta) if d.seg_id == 2) == "EN(に)"
    # the second request saw the first pair as context
    assert fake.requests[1][1].history == [("いち", "EN(いち)")]


async def test_worker_merges_backlog() -> None:
    fake = FakeTranslator(first_token_s=0.05)
    worker = TranslationWorker(fake, merge_after=3)
    session = StubSession()
    worker.submit(session, seg(1, "s1"))  # type: ignore[arg-type]
    await asyncio.sleep(0.01)  # seg 1 is being translated…
    for i in range(2, 7):  # …while five more pile up
        worker.submit(session, seg(i, f"s{i}"))  # type: ignore[arg-type]
    await asyncio.sleep(0.3)
    await worker.aclose()
    translations = session.of(p.Translation)
    # seg 1 goes alone; 2..6 (5 > 3 waiting) are merged into one request
    assert [t.seg_id for t in translations] == [1, 6]
    assert translations[1].merged == [2, 3, 4, 5]
    assert fake.requests[1][0] == "s2 s3 s4 s5 s6"
    assert worker.merges == 1


async def test_worker_timeout() -> None:
    worker = TranslationWorker(FakeTranslator(first_token_s=1.0), timeout_s=0.1)
    session = await run_worker(worker, [seg(1)], 0.3)
    errors = session.of(p.Error)
    assert [(e.code, e.seg_id, e.fatal) for e in errors] == [("mt_timeout", 1, False)]
    assert session.of(p.Translation) == []


async def test_worker_refuses_to_translate_with_inactive_model() -> None:
    fake = FakeTranslator(active=False)
    session = await run_worker(TranslationWorker(fake), [seg(1)], 0.1)
    assert [e.code for e in session.of(p.Error)] == ["mt_model_inactive"]
    assert fake.requests == []  # never translated with the coding model
    assert session.of(p.TranslationDelta) == []


async def test_worker_skips_when_en_not_wanted() -> None:
    fake = FakeTranslator()
    worker = TranslationWorker(fake)
    session = StubSession()
    session.targets = ["ja"]
    worker.submit(session, seg(1))  # type: ignore[arg-type]
    await asyncio.sleep(0.05)
    await worker.aclose()
    assert fake.requests == []


# ------------------------------------------------------------------ end to end


def test_glossary_spelling_end_to_end(tmp_path: Path) -> None:
    (tmp_path / "UCpeko.toml").write_text('[terms]\n"ぺこら" = "Pekora"\n', encoding="utf-8")
    fake_asr = FakeTranscriber(script=lambda a: "ぺこらだよ")
    client = fake_client(fake_asr, mt_backend="fake", glossary_dir=tmp_path)
    with client, client.websocket_connect("/ws") as ws:
        ws.send_text(
            json.dumps(
                {
                    "type": "hello",
                    "protocol_version": p.PROTOCOL_VERSION,
                    "video_id": "v",
                    "channel_id": "UCpeko",
                }
            )
        )
        ready = ws.receive_json()
        assert ready["mt_model"] == "fake-mt"
        for frame in to_frames(concat(speech(1.5), silence(1))):
            ws.send_bytes(p.encode_frame(frame))
        msgs = drain(ws)
    translation = next(m for m in msgs if m["type"] == "translation")
    assert "Pekora" in translation["en"]
    assert any(m["type"] == "translation_delta" for m in msgs)
    # the ASR prompt was primed with the glossary terms
    assert any("ぺこら" in (prompt or "") for _, prompt in fake_asr.calls)


async def test_empty_translation_is_retried_without_cache() -> None:
    calls: list[bool] = []

    class FlakyTranslator(FakeTranslator):
        async def translate(
            self, seg: FinalSegment, ctx: TranslationContext, *, retry: bool = False
        ) -> Any:
            calls.append(retry)
            if retry:
                yield "Did you forget?"

    session = await run_worker(
        TranslationWorker(FlakyTranslator()), [seg(1, "忘れちゃったの?")], 0.1
    )
    assert calls == [False, True]
    assert [t.en for t in session.of(p.Translation)] == ["Did you forget?"]
