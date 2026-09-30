# syntax=docker/dockerfile:1
# live-subs inference server (WP10). GPU image; models are NOT baked in:
# they live in the livesubs_models volume, filled once by `just fetch-models`.
FROM nvidia/cuda:12.4.1-base-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.12.9 /uv /usr/local/bin/uv

WORKDIR /app
# Dependencies first (cached layer). cuBLAS / cuDNN come from the nvidia-* wheels
# of the `gpu` extra, the driver from the NVIDIA container runtime.
# The uv cache is a BuildKit cache mount: 2.6 GB of wheels that must not end up in a layer.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv python install 3.13 \
 && uv sync --frozen --no-dev --extra gpu --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra gpu

RUN useradd --uid 10001 --create-home livesubs \
 && mkdir -p /models /glossaries && chown livesubs /models
USER livesubs

# CTranslate2 dlopens cuBLAS / cuDNN: point it at the nvidia-* wheels.
ENV NVIDIA_LIBS=/app/.venv/lib/python3.13/site-packages/nvidia
ENV LD_LIBRARY_PATH=$NVIDIA_LIBS/cublas/lib:$NVIDIA_LIBS/cudnn/lib:$NVIDIA_LIBS/cuda_nvrtc/lib \
    PATH=/app/.venv/bin:$PATH \
    HF_HOME=/models \
    HF_HUB_DISABLE_TELEMETRY=1 \
    LIVESUBS_HOST=0.0.0.0 \
    LIVESUBS_PORT=8765 \
    LIVESUBS_ASR_LOCAL_ONLY=true \
    LIVESUBS_GLOSSARY_DIR=/glossaries \
    LIVESUBS_LLM_BASE_URL=http://172.17.0.1:8001/v1

EXPOSE 8765
# Healthy = ASR model loaded and warm.
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD ["python", "-c", "import json,sys,urllib.request as u; h=json.load(u.urlopen('http://127.0.0.1:8765/health', timeout=4)); sys.exit(0 if h['asr']['warm'] else 1)"]
CMD ["python", "-m", "livesubs"]
