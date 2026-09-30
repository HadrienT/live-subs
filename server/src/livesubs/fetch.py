"""``python -m livesubs.fetch``: download the configured ASR model into ``HF_HOME``.

The container starts with ``LIVESUBS_ASR_LOCAL_ONLY=true``: it never downloads
1.5 GB silently on first boot; ``just fetch-models`` does it once, on purpose.
"""

from __future__ import annotations

from huggingface_hub import snapshot_download

from livesubs.asr.faster_whisper import MODELS
from livesubs.config import get_settings


def main() -> None:
    settings = get_settings()
    repo = MODELS.get(settings.asr_model, settings.asr_model)
    path = snapshot_download(repo, revision=settings.asr_revision)
    print(f"{settings.asr_model}: {repo} → {path}")


if __name__ == "__main__":
    main()
