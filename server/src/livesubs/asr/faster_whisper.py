"""``FasterWhisperTranscriber``: Whisper models through CTranslate2 (GPU, fp16)."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
from numpy.typing import NDArray

from livesubs.asr.base import AsrResult, compression_ratio

log = logging.getLogger(__name__)

# Known-good repository ids; any other CTranslate2 Whisper repo or local path works too.
MODELS = {
    "kotoba-whisper-v2.0": "kotoba-tech/kotoba-whisper-v2.0-faster",
    "large-v3": "Systran/faster-whisper-large-v3",
    "large-v3-turbo": "dropbox-dash/faster-whisper-large-v3-turbo",
}


def parse_device(device: str) -> tuple[str, int]:
    """``cuda:1`` → (``cuda``, 1); ``cpu`` → (``cpu``, 0)."""
    kind, _, index = device.partition(":")
    return kind, int(index or 0)


class FasterWhisperTranscriber:
    def __init__(
        self,
        model: str = "kotoba-whisper-v2.0",
        *,
        device: str = "cuda:0",
        compute_type: str = "float16",
        beam_size: int = 1,
        download_root: str | None = None,
        local_files_only: bool = False,
        revision: str | None = None,
    ) -> None:
        from faster_whisper import WhisperModel

        self.name = model
        self.repo = MODELS.get(model, model)
        self.device = device
        self.compute_type = compute_type
        self.beam_size = beam_size
        kind, index = parse_device(device)
        extra: dict[str, Any] = {"revision": revision} if revision else {}
        self._model = WhisperModel(
            self.repo,
            device=kind,
            device_index=index,
            compute_type=compute_type,
            cpu_threads=4,
            num_workers=1,
            download_root=download_root,
            local_files_only=local_files_only,
            **extra,
        )
        log.info("loaded %s (%s) on %s as %s", model, self.repo, device, compute_type)

    def transcribe(self, audio: NDArray[np.float32], *, prompt: str | None) -> AsrResult:
        segments, _info = self._model.transcribe(
            audio,
            language="ja",
            task="transcribe",
            beam_size=self.beam_size,
            # One pass, no temperature fallback: predictable latency. Bad outputs are
            # caught by the hallucination filter instead of being re-decoded.
            temperature=0.0,
            condition_on_previous_text=False,
            initial_prompt=prompt or None,
            without_timestamps=True,
            vad_filter=False,
        )
        parts = list(segments)
        text = "".join(s.text for s in parts).strip()
        if not parts:
            return AsrResult(text="", no_speech_prob=1.0, avg_logprob=0.0)
        weights = np.array([max(len(s.tokens), 1) for s in parts], dtype=np.float64)
        avg_logprob = float(np.average([s.avg_logprob for s in parts], weights=weights))
        return AsrResult(
            text=text,
            no_speech_prob=float(max(s.no_speech_prob for s in parts)),
            avg_logprob=avg_logprob,
            compression_ratio=compression_ratio(text),
        )

    def warmup(self) -> None:
        """First decode on a fresh model allocates buffers: do it before serving."""
        self.transcribe(np.zeros(16_000, dtype=np.float32), prompt=None)
