"""The ``Translator`` interface and the translation prompt."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol

from livesubs.mt.glossary import EMPTY, Glossary
from livesubs.segments import FinalSegment

SYSTEM_PROMPT = """\
You translate the Japanese subtitles of a live stream into English, one line at a time.
- Output only the English translation of the last line: one line, no quotes, no notes, \
no romaji, no explanations.
- Spoken, natural register, as a streamer would say it in English. Keep it short enough \
to read as a subtitle.
- Japanese often omits the subject: infer it from the previous lines.
- If the Japanese looks like a transcription error, translate the most plausible meaning \
without mentioning it.
- Keep names and terms from the glossary exactly as given."""


@dataclass(frozen=True, slots=True)
class TranslationContext:
    history: list[tuple[str, str]] = field(default_factory=list)  # (ja, en), oldest first
    glossary: Glossary = EMPTY
    channel_name: str = ""


@dataclass(frozen=True, slots=True)
class ModelStatus:
    reachable: bool
    active: bool  # the requested model is served (or swappable on demand)
    served: list[str]


class Translator(Protocol):
    name: str

    def translate(
        self, seg: FinalSegment, ctx: TranslationContext, *, retry: bool = False
    ) -> AsyncIterator[str]:
        """Yields the English text in pieces as it is generated. ``retry``: second
        attempt after an empty answer (bypass any server-side cache)."""
        ...

    async def status(self, *, fresh: bool = False) -> ModelStatus: ...

    async def aclose(self) -> None: ...


def build_messages(ja: str, ctx: TranslationContext) -> list[dict[str, str]]:
    """Chat messages: system rules (+ glossary), previous lines as turns, then this line."""
    system = SYSTEM_PROMPT
    if ctx.channel_name:
        system += f"\nStream: {ctx.channel_name}."
    terms = dict(ctx.glossary.relevant(ja))
    for past_ja, _ in ctx.history:
        terms.update(ctx.glossary.relevant(past_ja))
    if terms:
        system += "\nGlossary (Japanese → English, mandatory spelling):\n" + "\n".join(
            f"- {k} → {v}" for k, v in terms.items()
        )
    messages = [{"role": "system", "content": system}]
    for past_ja, past_en in ctx.history:
        messages.append({"role": "user", "content": past_ja})
        messages.append({"role": "assistant", "content": past_en})
    messages.append({"role": "user", "content": ja})
    return messages
