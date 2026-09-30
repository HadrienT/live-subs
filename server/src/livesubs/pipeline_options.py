"""Options of the ASR pipeline (kept apart from ``pipeline`` to avoid import cycles)."""

from __future__ import annotations

from dataclasses import dataclass, field

from livesubs.asr.filters import FilterParams


@dataclass(frozen=True, slots=True)
class AsrOptions:
    use_prompt: bool = True
    prompt_chars: int = 50
    filters: FilterParams = field(default_factory=FilterParams)
