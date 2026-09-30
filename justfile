# live-subs — task runner. `just` lists the recipes.

set dotenv-load := true

server := "server"
ext := "extension"

default:
    @just --list

# Lint everything: ruff + mypy --strict (server), eslint + tsc + web-ext lint (extension)
lint: lint-server lint-ext

lint-server:
    cd {{server}} && uv run ruff check . && uv run ruff format --check . && uv run mypy

lint-ext:
    cd {{ext}} && npx eslint . && npx tsc --noEmit && node esbuild.mjs && npx web-ext lint --source-dir dist

# Unit tests without GPU: pytest (excluding gpu/e2e) + vitest (incl. protocol drift test)
test: test-server test-ext

test-server:
    cd {{server}} && uv run pytest

test-ext:
    cd {{ext}} && npx vitest run

# Tests that load the real models on the V100s, incl. the golden replay (not in CI)
test-gpu:
    cd {{server}} && uv run --extra gpu pytest -m gpu

# Regenerate the golden outputs (WP12): review `git diff` before committing them
golden-update:
    cd {{server}} && LIVESUBS_GOLDEN_UPDATE=1 uv run --extra gpu pytest -m "gpu and e2e" tests/e2e -v

# Rebuild the golden fixtures from the public CC0 set (needs `just bench-prepare`)
golden-fixtures:
    cd {{server}} && uv run --group bench python ../benchmarks/make_fixtures.py

# Run the server locally on ws://0.0.0.0:8765
run-server:
    cd {{server}} && uv run --extra gpu --extra ahead python -m livesubs

# Replay an audio file into a running server, like the extension would
replay file *args:
    cd {{server}} && uv run python ../tools/replay.py "{{absolute_path(file)}}" {{args}}

# ASR benchmark (WP04)
bench-asr *args:
    cd {{server}} && uv run --extra gpu --group bench python ../benchmarks/asr/run.py {{args}}

# Translation benchmark (WP06)
bench-mt *args:
    cd {{server}} && uv run python ../benchmarks/mt/run.py {{args}}

# Build the extension into extension/dist/
ext-build:
    cd {{ext}} && node esbuild.mjs

# Production build + lint + AMO "unlisted" signing → extension/dist-signed/*.xpi (WP11)
# Needs WEB_EXT_API_KEY / WEB_EXT_API_SECRET in .env (addons.mozilla.org → API keys).
ext-sign:
    @test -n "${WEB_EXT_API_KEY:-}" -a -n "${WEB_EXT_API_SECRET:-}" || { echo "WEB_EXT_API_KEY / WEB_EXT_API_SECRET missing in .env"; exit 1; }
    cd {{ext}} && rm -rf dist && node esbuild.mjs --production && npx web-ext lint --source-dir dist
    cd {{ext}} && npx web-ext sign --channel=unlisted --source-dir dist --artifacts-dir dist-signed --api-key "$WEB_EXT_API_KEY" --api-secret "$WEB_EXT_API_SECRET"
    @ls -1 {{ext}}/dist-signed/*.xpi

# Disposable Firefox with the extension loaded
ext-run: ext-build
    cd {{ext}} && npx web-ext run --source-dir dist --start-url https://www.youtube.com/

# Scripted fake server for extension development without GPU
fake-server port="8765":
    cd {{ext}} && npx tsx test/fake-server.ts {{port}}

# Regenerate the protocol description compared by the TS drift test
protocol-schema:
    cd {{server}} && uv run python -m livesubs.protocol > src/livesubs/protocol.schema.json

# Build the public CC0 benchmark set (Common Voice 8.0 ja) in benchmarks/data/public/
bench-prepare *args:
    cd {{server}} && uv run --group bench python ../benchmarks/prepare_public.py {{args}}

# Blind A/B review of two translation models (WP06 §4)
bench-mt-blind *args:
    cd {{server}} && uv run python ../benchmarks/mt/blind.py {{args}}

# ---------------------------------------------------------------- deployment (WP10)

# Build the server image
docker-build:
    docker compose build

# Download the ASR model into the livesubs_models volume (once, before `deploy`)
fetch-models:
    docker compose run --rm --no-deps livesubs python -m livesubs.fetch

# Start / update the server container (one container, default bridge network)
deploy:
    docker compose up -d livesubs

# Health of the server + VRAM of both GPUs
status url="http://192.168.1.200:8765":
    @curl -fsS {{url}}/health | python3 -m json.tool || echo "server unreachable at {{url}}"
    @nvidia-smi --query-gpu=index,name,memory.used,memory.total,utilization.gpu --format=csv
    @nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv
