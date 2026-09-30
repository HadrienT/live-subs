"""``PlamoTranslator``: PFN's PLaMo 2 Translate through llama-server.

PLaMo 2 Translate is not a chat model: it expects a raw prompt made of
``<|plamo:op|>`` blocks and stops at the next one (model card, 2025):

    <|plamo:op|>dataset
    translation
    <|plamo:op|>input lang=Japanese
    <previous line>
    <|plamo:op|>output lang=English
    <its translation>
    <|plamo:op|>input lang=Japanese
    <line to translate>
    <|plamo:op|>output lang=English

Previous lines are repeated input/output turns (the model's own multi-turn
format, see its playground chat template): that is our sliding context. It does
not follow instructions, so the glossary cannot be a rule: relevant terms are
given as already-translated turns, which primes their spelling.

Sent to ``/v1/completions`` (never chat: no template involved), temperature 0,
stop on ``<|plamo:op|>``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from livesubs.mt.base import TranslationContext
from livesubs.mt.llama import LlamaServerTranslator
from livesubs.segments import FinalSegment

OP = "<|plamo:op|>"


def _clean(text: str) -> str:
    """One line, and never a block marker the model would read as structure."""
    return " ".join(text.replace(OP, " ").split())


def build_prompt(ja: str, ctx: TranslationContext) -> str:
    turns: list[tuple[str, str]] = []
    seen_text = ja + "".join(past_ja for past_ja, _ in ctx.history)
    turns += sorted(ctx.glossary.relevant(seen_text).items(), key=lambda kv: -len(kv[0]))
    turns += ctx.history
    parts = [f"{OP}dataset\ntranslation\n"]
    for src, dst in turns:
        parts.append(
            f"{OP}input lang=Japanese\n{_clean(src)}\n{OP}output lang=English\n{_clean(dst)}\n"
        )
    parts.append(f"{OP}input lang=Japanese\n{_clean(ja)}\n{OP}output lang=English\n")
    return "".join(parts)


def _completion_text(chunk: dict[str, Any]) -> str:
    choices = chunk.get("choices") or [{}]
    return str(choices[0].get("text") or "")


class PlamoTranslator(LlamaServerTranslator):
    def payload(self, ja: str, ctx: TranslationContext) -> dict[str, Any]:
        return {
            "model": self.model,
            "prompt": build_prompt(ja, ctx),
            "stream": True,
            "temperature": 0.0,  # as in the model card
            "max_tokens": self.max_tokens,
            "stop": [OP],
        }

    async def translate(self, seg: FinalSegment, ctx: TranslationContext) -> AsyncIterator[str]:
        async for piece in self._stream(
            "/completions", self.payload(seg.ja, ctx), _completion_text
        ):
            yield piece
