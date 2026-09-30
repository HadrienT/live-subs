"""Runtime configuration. Every field is overridable by a ``LIVESUBS_*`` variable."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LIVESUBS_", env_file=".env", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8765
    token: str | None = None
    log_level: str = "info"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
