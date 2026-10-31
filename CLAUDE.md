# live-subs — notes for Claude Code

Near-live **Japanese + English subtitles** on YouTube streams watched in Firefox. Two
halves in this repository, which evolve together:

| Half | Where | What it does |
|---|---|---|
| Firefox extension | `extension/` | Captures the audio of YouTube's `<video>` element (`mozCaptureStream()`), brings it down to 16 kHz mono PCM, sends it to the server over WebSocket, shows the subtitles as an overlay on the player |
| Inference server | `server/` (Python package `livesubs`) | Receives the audio, segments speech (Silero VAD), transcribes Japanese (Japanese-specialized Whisper, GPU), translates to English (local LLM), sends the segments back |

The PC watching the stream and this server (`192.168.1.200`) are on the same switch.
**Nothing leaves the LAN**: no cloud API, neither for transcription nor for translation.

## The chain, layer by layer

```
<video> YouTube ─ mozCaptureStream ─► AudioWorklet (48k→16k, int16)
   └─ content script ──port──► background ──WebSocket──► server :8765
server: ingest → VAD → ASR (GPU) → JA segment ──► LLM (llama-server) → EN
   ◄── partial / final / translation (JSON) ── overlay in #movie_player
```

- **Wire contract**: `server/src/livesubs/protocol.py` is the source of truth;
  `extension/src/protocol.ts` is its **manual mirror**, guarded by a drift test (same
  pattern as `AgenticEnv` ↔ `agenticenv-chat`). Any protocol change touches both files in
  the same commit and increments `PROTOCOL_VERSION`.
- **ASR**: default model `kotoba-whisper-v2.0` (distilled from Whisper large-v3 on
  Japanese) through `faster-whisper` / CTranslate2, fp16. The final choice comes out of
  the benchmark of [work package 04](blueprint/wp/04-asr-benchmark.md). Do not change
  model without rerunning the benchmark.
- **Translation**: OpenAI-compatible client to the **`llama-server` of `~/AgenticEnv`**
  (llama.cpp; `configs/models.yaml` there is the model registry). URL:
  `LIVESUBS_LLM_BASE_URL`, default `http://127.0.0.1:8000/v1` outside Docker,
  `http://172.17.0.1:8001/v1` from a container (AgenticEnv's `llama-bridge` socket). Only
  one model is loaded at a time; AgenticEnv switches between **profiles**: `code`
  (Qwen3-Coder, coding agent) and `translate` (the best JA → EN translator, chosen by the
  benchmark of work package 06). live-subs requests its model by name
  (`LIVESUBS_LLM_MODEL`) and reports the `code` profile if it is loaded, never silently
  translating with it. This repository **does not drive** llama-server: models, profiles
  and the switching mechanism are changed in AgenticEnv, through an issue there. See
  [ADR-003](blueprint/decisions.md).

## GPU: shared resource

Two 16 GB Tesla V100 cards (compute capability 7.0: **no bf16**, only fp16 and
int8_float16), shared with `llama-server` (AgenticEnv, 20 GiB budget across both cards)
and the CUDA backend of `~/quant-modeling`.

- ASR reserves **at most 4 GiB on GPU 0** (`LIVESUBS_ASR_DEVICE=cuda:0`). Any overrun is
  measured and documented in work package 04 before being accepted.
- Never vLLM here: its recent versions dropped Volta.
- Before concluding that a model is slow, check that it actually runs on the GPU
  (`nvidia-smi --query-compute-apps=pid,used_memory --format=csv`).

## Tracking work: GitHub Issues, no handoff markdown

The project's "JIRA" is the **GitHub Issues** of
[`HadrienT/live-subs`](https://github.com/HadrienT/live-subs) (`gh` is authenticated,
account `HadrienT`). One issue per work package (`WP00`–`WP14`, label `wp`), and a pinned
**`📋 Board`** issue (live-subs#16) that groups them by milestone and lists the
AgenticEnv dependencies (AgenticEnv#15, AgenticEnv#16).

- **At the start of a session**: `gh issue list --state open` here, and on
  `HadrienT/AgenticEnv` the `cross-repo` issues blocking us.
- **Work package or issue finished** → close it, and tick the box in the Board.
- **Issue handled** → `gh issue close <n> --comment "done in <sha>"`.
- **A task concerning AgenticEnv** (served model, context, llama-bridge) →
  `gh issue create --repo HadrienT/AgenticEnv …`, label `cross-repo`.
- **Never** a markdown handoff file between sessions. A task that outlives the session is
  an issue.
- Large pieces of **design** live in `blueprint/wp/*.md`; the issue points to them, it
  does not replace them. `blueprint/README.md` holds the dependency graph: consult it
  before starting a work package to check that its prerequisites are done.

## Commands

| | |
|---|---|
| `just lint` | ruff + `mypy --strict` (server), eslint + `tsc --noEmit` + `web-ext lint` (extension): **green before any commit** |
| `just test` | pytest (excluding `gpu` / `e2e`) + vitest, including the protocol drift test |
| `just test-gpu` | tests marked `gpu`: real models on the V100s (outside CI) |
| `just run-server` | local server, `ws://0.0.0.0:8765` |
| `just replay <file.wav>` | replays a recording into the server the way the extension would, shows segments and latencies |
| `just bench-asr` / `just bench-mt` | benchmarks of work packages 04 and 06, results in `benchmarks/` |
| `just ext-build` | esbuild build of the extension into `extension/dist/` |
| `just ext-run` | `web-ext run`: disposable Firefox with the extension loaded |

## Conventions

- **Language: English by default.** Everything you produce is in English: code, identifiers, comments, commit messages, PRs, issues, documentation and interface text. The only exception is the conversation: reply in the language the maintainer writes in, so in French when they write in French. `blueprint/` is historically in French: new content there is in English too.
- **Commits: go through a branch, never directly on `main`.** End messages with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Python**: 3.13, `uv`, ruff (lint + format), `mypy --strict`. Anything touching the GPU
  is behind an interface (`Transcriber`, `Translator`) with a fake for tests: the default
  suite must pass without a GPU.
- **Extension**: strict TypeScript, esbuild, Firefox Manifest V3. No heavy runtime
  dependency: it is a content script injected into YouTube. The overlay lives in a
  **shadow DOM** attached to `#movie_player` (it follows fullscreen, and YouTube's CSS
  does not touch it).
- **Audio fixtures**: only openly licensed sources (Common Voice CC0, JSUT…) in the
  repository. Stream excerpts recorded for benchmarks stay in `benchmarks/data/`, **ignored
  by git**.
- Each measured latency is given as **p50 / p95** and relative to the end of speech (end
  of the VAD segment), not the start of the segment.

## Network and Docker

- The server listens on the LAN address (`192.168.1.200:8765`), **never** exposed through
  the quant-modeling Cloudflare tunnel. No strong authentication: an optional shared token
  (`LIVESUBS_TOKEN`) is enough on the LAN.
- **Fragile Docker networking on this host**: a veth teardown on `docker0` has already
  frozen the machine and cut the whole LAN (AgenticEnv#8). Never a bulk `docker rm -f` or a
  `docker network prune`; act one container at a time, explaining the blast radius. Other
  stacks run on this server (quant-modeling, quant-platform, data-ingest): do not touch
  them.
