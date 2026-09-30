"""Per-channel glossaries: ``glossaries/<channel_id>.toml``.

    name = "Channel display name"          # optional
    [terms]
    "兎田ぺこら" = "Usada Pekora"
    "ぺこら" = "Pekora"

The Japanese terms prime the ASR prompt (WP05 §5); the pairs force the English
spelling in the translation prompt (WP06 §2).
"""

from __future__ import annotations

import logging
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True, slots=True)
class Glossary:
    name: str = ""
    terms: dict[str, str] = field(default_factory=dict)

    def asr_prompt(self, limit: int = 60) -> str:
        """Japanese terms for Whisper's initial prompt, longest first, within ``limit`` chars."""
        out = ""
        for ja in sorted(self.terms, key=len, reverse=True):
            if len(out) + len(ja) + 1 > limit:
                break
            out += ("、" if out else "") + ja
        return out

    def relevant(self, ja_text: str) -> dict[str, str]:
        """Pairs whose Japanese term appears in ``ja_text``."""
        return {ja: en for ja, en in self.terms.items() if ja in ja_text}


EMPTY = Glossary()


class GlossaryStore:
    """Loads ``<dir>/<channel_id>.toml`` on demand; re-reads a file when it changes."""

    def __init__(self, directory: Path | None) -> None:
        self.directory = directory
        self._cache: dict[str, tuple[float, Glossary]] = {}

    def get(self, channel_id: str | None) -> Glossary:
        if not channel_id or self.directory is None or not _SAFE_ID.match(channel_id):
            return EMPTY
        path = self.directory / f"{channel_id}.toml"
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            return EMPTY
        cached = self._cache.get(channel_id)
        if cached and cached[0] == mtime:
            return cached[1]
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            glossary = Glossary(
                name=str(data.get("name", "")),
                terms={str(k): str(v) for k, v in dict(data.get("terms", {})).items()},
            )
        except (tomllib.TOMLDecodeError, OSError, ValueError) as e:
            log.warning("glossary %s unreadable: %s", path, e)
            return EMPTY
        self._cache[channel_id] = (mtime, glossary)
        log.info("glossary %s: %d terms", channel_id, len(glossary.terms))
        return glossary
