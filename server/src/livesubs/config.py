"""Runtime configuration. Every field is overridable by a ``LIVESUBS_*`` variable."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from livesubs.vad import VadParams


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LIVESUBS_", env_file=".env", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8765
    token: str | None = None
    log_level: str = "info"
    hello_timeout_s: float = 10.0

    # --- ingest & VAD (WP03)
    ring_seconds: float = 30.0
    vad_backend: str = "silero"  # "silero" | "energy" (tests, no model)
    vad_threshold: float = 0.5
    min_speech_ms: int = 250
    min_silence_ms: int = 400
    max_segment_s: float = 12.0
    pad_ms: int = 200
    partial_interval_s: float = 1.0

    def vad_params(self) -> VadParams:
        return VadParams(
            threshold=self.vad_threshold,
            min_speech_ms=self.min_speech_ms,
            min_silence_ms=self.min_silence_ms,
            max_segment_s=self.max_segment_s,
            pad_ms=self.pad_ms,
            update_interval_s=self.partial_interval_s,
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
