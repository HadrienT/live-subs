"""Runtime configuration. Every field is overridable by a ``LIVESUBS_*`` variable."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from livesubs.asr.filters import FilterParams
from livesubs.pipeline_options import AsrOptions
from livesubs.vad import VadParams


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LIVESUBS_", env_file=".env", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8765
    token: str | None = None
    log_level: str = "info"
    hello_timeout_s: float = 10.0
    stats_interval_s: float = 5.0

    # --- ingest & VAD (WP03)
    ring_seconds: float = 30.0
    vad_backend: str = "silero"  # "silero" | "energy" (tests, no model)
    vad_threshold: float = 0.5
    min_speech_ms: int = 250
    min_silence_ms: int = 400
    max_segment_s: float = 12.0
    pad_ms: int = 200
    partial_interval_s: float = 1.0
    vad_reset_after_s: float = 1.0  # 0 = never reset Silero's state (ADR-008)

    # --- ASR (WP04/WP05). Defaults are the WP04 bench winner (ADR-002).
    asr_backend: str = "faster-whisper"  # "faster-whisper" | "fake" (tests, no GPU)
    asr_model: str = "kotoba-whisper-v2.0"
    asr_device: str = "cuda:0"
    asr_compute_type: str = "float16"
    asr_beam_size: int = 1
    asr_revision: str | None = None  # pin a Hugging Face commit (WP04 records the tested one)
    asr_local_only: bool = False  # true in the container: models come from `just fetch-models`
    allow_cpu: bool = False  # never fall back to CPU silently (WP10 §3)
    asr_use_prompt: bool = True
    asr_prompt_chars: int = 50
    # Hallucination guard (livesubs.asr.filters)
    asr_no_speech_prob: float = 0.6
    asr_no_speech_logprob: float = -1.0
    asr_max_compression_ratio: float = 2.4
    asr_sparse_logprob: float = -0.3
    asr_sparse_chars_per_s: float = 2.0
    # CPU threads for numpy/OpenBLAS and CTranslate2: the host is shared.
    cpu_threads: int = 4

    # --- Translation (WP06): AgenticEnv's llama-server, OpenAI-compatible (ADR-003)
    mt_backend: str = "llama"  # "llama" | "fake" | "none"
    llm_base_url: str = "http://127.0.0.1:8000/v1"  # in Docker: http://172.17.0.1:8001/v1
    # served_name of AgenticEnv's `translate` profile (configs/models.yaml there)
    llm_model: str = "plamo-2-translate"
    llm_temperature: float = 0.2
    llm_max_tokens: int = 120
    llm_disable_thinking: bool = True
    # How the model is prompted: "chat" (instruct models, /v1/chat/completions),
    # "plamo" (PLaMo 2 Translate's raw format, /v1/completions), or "auto":
    # plamo when the model name contains "plamo".
    llm_prompt_format: str = "auto"
    mt_timeout_s: float = 8.0
    mt_merge_after: int = 3
    mt_history: int = 6
    # --- Ahead-of-live mode (WP13): the server pulls the live with yt-dlp + ffmpeg
    ahead_enabled: bool = True
    ahead_capture_window_s: float = 15.0  # captured audio used to align the timelines
    ahead_max_lag_s: float = 120.0  # how far ahead of the player the pulled live may be
    ahead_fail_after_s: float = 45.0  # then fall back to transcribing the capture
    # With seconds of lead there is no hurry to close a sentence: wait for a real
    # pause, so the translator gets whole sentences instead of breath-cut halves.
    ahead_min_silence_ms: int = 800
    # Replays: how far ahead of the player the server reads (and transcribes)
    ahead_replay_lead_s: float = 60.0
    glossary_dir: Path | None = Path(__file__).resolve().parents[3] / "glossaries"

    def vad_params(self) -> VadParams:
        return VadParams(
            threshold=self.vad_threshold,
            min_speech_ms=self.min_speech_ms,
            min_silence_ms=self.min_silence_ms,
            max_segment_s=self.max_segment_s,
            pad_ms=self.pad_ms,
            update_interval_s=self.partial_interval_s,
            reset_after_s=self.vad_reset_after_s,
        )

    def asr_options(self) -> AsrOptions:
        return AsrOptions(
            use_prompt=self.asr_use_prompt,
            prompt_chars=self.asr_prompt_chars,
            filters=FilterParams(
                no_speech_prob=self.asr_no_speech_prob,
                no_speech_logprob=self.asr_no_speech_logprob,
                max_compression_ratio=self.asr_max_compression_ratio,
                sparse_logprob=self.asr_sparse_logprob,
                sparse_chars_per_s=self.asr_sparse_chars_per_s,
            ),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
