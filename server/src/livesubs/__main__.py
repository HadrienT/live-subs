"""``python -m livesubs``: run the server with uvicorn."""

from __future__ import annotations

import os

# Before numpy is imported: unbounded, OpenBLAS spins one thread per core (56 here)
# for every mel spectrogram, on a host shared with other stacks (WP04 bench).
_threads = os.environ.get("LIVESUBS_CPU_THREADS", "4")
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, _threads)

import uvicorn  # noqa: E402

from livesubs.app import create_app  # noqa: E402
from livesubs.config import get_settings  # noqa: E402


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        create_app(settings), host=settings.host, port=settings.port, log_level=settings.log_level
    )


if __name__ == "__main__":
    main()
