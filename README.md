# live-subs

Near-live Japanese and English subtitles for YouTube streams in Firefox,
transcribed and translated on a local GPU server. Nothing leaves the LAN.

**[How it works: interactive map](https://hadrient.github.io/atlas-pages/live-subs/)**

[![CI](https://github.com/HadrienT/live-subs/actions/workflows/ci.yml/badge.svg)](https://github.com/HadrienT/live-subs/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-asyncio-3776AB?logo=python&logoColor=white)
![TypeScript](https://img.shields.io/badge/TypeScript-Firefox%20MV3-3178C6?logo=typescript&logoColor=white)

Measured on a Tesla V100: Japanese appears **0.32 s** (p50) after the end of a
sentence, and English **0.56 s**.

## How it works

A Firefox extension captures the player's audio and streams it to a server, which
detects speech, transcribes Japanese with kotoba-whisper (a Whisper model
specialized for Japanese) and translates it with a local LLM. The extension renders
both as an overlay on the player.

```text
<video> ─ mozCaptureStream ─► AudioWorklet (→16 kHz int16) ─► background ─ ws ─► server :8765
server: Silero VAD → kotoba-whisper (GPU) → partial / final JA ─► llama-server → EN
overlay in #movie_player ◄── partial / final / translation (JSON) ──┘
```

## Features

- **Two-line overlay**: Japanese stabilizes as it is transcribed, and English streams
  in. The overlay can be moved, and `Alt+Shift+D` cycles the displayed languages.
- **Ahead mode**: the server pulls the stream itself and the player stays about 6 s
  behind the live edge, so subtitles appear *with* the sentence instead of after it.
  It falls back to normal capture if the stream cannot be pulled or aligned.
- **Per-channel [glossaries](glossaries/README.md)**: names and game terms prime the
  transcription and force their English spelling.
- **Transcript**: a panel above the live chat with click-to-seek, and SRT or VTT export.
- **Latency HUD**: p50 and p95 latencies for both languages.

## Running it

The server needs a CUDA GPU, [just](https://just.systems/), and an OpenAI-compatible
`llama-server` for translation, provided here by
[AgenticEnv](https://github.com/HadrienT/AgenticEnv). Without a server capable of
translating, Japanese subtitles still work and the overlay says why English is missing.

```sh
cp .env.example .env            # every LIVESUBS_* variable is documented there
just run-server                 # ws://0.0.0.0:8765/ws
just replay clip.wav            # replays a file like the extension would, prints latencies
just fake-server                # scripted server with no GPU, for extension work
just ext-run                    # disposable Firefox with the extension loaded
```

Docker deployment, signing the extension and everyday use are in
[`docs/usage.md`](docs/usage.md). The server is meant for a trusted LAN: never forward
its port.

## Development

| Command | Purpose |
|---|---|
| `just lint` | ruff, `mypy --strict`, eslint, tsc, web-ext lint |
| `just test` | pytest (no GPU) and vitest, including the protocol drift test |
| `just test-gpu` | Real models, including the golden replay |
| `just bench-asr`, `just bench-mt` | Benchmarks used to choose the models |

Design and decisions are in [`blueprint/`](blueprint/README.md), in French.

## License

[MIT](LICENSE)
