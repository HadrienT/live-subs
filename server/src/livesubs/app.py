"""FastAPI application: ``/ws`` for sessions, ``/health`` for diagnostics."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from livesubs import __version__
from livesubs.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="live-subs", version=__version__)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "version": __version__}

    return app
