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

# Tests that load the real models on the V100s (not in CI)
test-gpu:
    cd {{server}} && uv run --extra gpu pytest -m gpu

# Run the server locally on ws://0.0.0.0:8765
run-server:
    cd {{server}} && uv run --extra gpu python -m livesubs

# Replay an audio file into a running server, like the extension would
replay file *args:
    cd {{server}} && uv run python ../tools/replay.py "{{absolute_path(file)}}" {{args}}

# ASR benchmark (WP04)
bench-asr *args:
    cd {{server}} && uv run --extra gpu python ../benchmarks/asr/run.py {{args}}

# Translation benchmark (WP06)
bench-mt *args:
    cd {{server}} && uv run python ../benchmarks/mt/run.py {{args}}

# Build the extension into extension/dist/
ext-build:
    cd {{ext}} && node esbuild.mjs

# Disposable Firefox with the extension loaded
ext-run: ext-build
    cd {{ext}} && npx web-ext run --source-dir dist --start-url https://www.youtube.com/

# Scripted fake server for extension development without GPU
fake-server port="8765":
    cd {{ext}} && npx tsx test/fake-server.ts {{port}}

# Regenerate the protocol description compared by the TS drift test
protocol-schema:
    cd {{server}} && uv run python -m livesubs.protocol > src/livesubs/protocol.schema.json
