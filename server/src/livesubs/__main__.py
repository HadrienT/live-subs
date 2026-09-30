"""``python -m livesubs``: run the server with uvicorn."""

from __future__ import annotations

import uvicorn

from livesubs.app import create_app
from livesubs.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        create_app(settings), host=settings.host, port=settings.port, log_level=settings.log_level
    )


if __name__ == "__main__":
    main()
