# live-subs

Near-live Japanese + English subtitles for YouTube streams watched in Firefox.
A Firefox extension captures the player's audio and streams it over the LAN to
a GPU server that transcribes Japanese (kotoba-whisper, a Whisper model
specialised for Japanese) and translates it to English (local LLM through
AgenticEnv's llama-server). Nothing leaves the LAN.

```
<video> ─ mozCaptureStream ─► AudioWorklet (→16 kHz int16) ─► background ─ ws ─► server :8765
server: Silero VAD → kotoba-whisper (GPU 0) → partial / final JA ─► llama-server → EN
overlay in #movie_player ◄── partial / final / translation (JSON) ──┘
```

Design, decisions and work packages: [`blueprint/`](blueprint/README.md).
Measured on the V100s (ADR-007): Japanese shown **0.32 s** (p50) after the end
of a sentence, English **0.56 s**.

## Server

### Run it from source (development)

```sh
just run-server                 # ws://0.0.0.0:8765/ws, loads kotoba-whisper on cuda:0
just replay clip.wav            # replays a file like the extension would, prints latencies
just fake-server                # scripted server, no GPU, for extension work
```

Configuration: `LIVESUBS_*` variables, all documented in [`.env.example`](.env.example).
The translation model is requested by name (`LIVESUBS_LLM_MODEL`, default
`translate`). If llama-server serves another model (AgenticEnv's `code`
profile), live-subs says so — banner in the overlay and the popup — and does
not translate (ADR-003).

### Deploy it (Docker, starts with the machine)

Read `CLAUDE.md` « Réseau et Docker » first: Docker networking on this host is
fragile. The compose file creates **no** network (default `bridge`) and one
container.

```sh
just docker-build               # image live-subs-server (~3.4 GB: CUDA libraries)
just fetch-models               # once: ASR model into the livesubs_models volume
just deploy                     # docker compose up -d, restart: unless-stopped
just status                     # /health + nvidia-smi of both GPUs
```

The container listens on `192.168.1.200:8765` only, never on the Cloudflare
tunnel. It refuses to start if GPU 0 is not visible (no silent CPU fallback;
`LIVESUBS_ALLOW_CPU=1` to force it). `/health` reports `asr.warm`, the VRAM of
GPU 0 and whether llama-server is reachable and serves the right model.

Port 8765 must be reachable from the LAN and **not** forwarded by the router.

## Extension

### Install it for good (signed, unlisted)

Firefox Release only installs signed extensions. Mozilla signs "unlisted"
extensions without publishing them on the store:

1. Create API keys on addons.mozilla.org (Developer Hub → *Manage API Keys*) and
   put them in `.env` as `WEB_EXT_API_KEY` / `WEB_EXT_API_SECRET` (never commit
   them).
2. `just ext-sign` → production build, `web-ext lint`, signing:
   `extension/dist-signed/live_subs-<version>.xpi`.
3. On the PC: open the `.xpi` in Firefox (drag it onto a tab, or
   `about:addons` → ⚙ → *Install Add-on From File*). It stays installed across
   restarts.

**Update**: bump `version` in `extension/package.json` (the manifest takes it
from there), `just ext-sign`, install the new `.xpi` over the old one; settings
are kept. There is no automatic update: Firefox only follows an `update_url`
over HTTPS, and the server has no TLS on the LAN. Check that the server's
`PROTOCOL_VERSION` matches: a mismatch shows « mettre à jour l'extension » in
the popup.

**Development**: `just ext-run` opens a disposable Firefox with the extension
loaded (temporary add-on, gone at restart).

### Use it

- Open a stream (`youtube.com/watch?v=…` or `/live/…`), click the extension
  icon → *Activer sur cet onglet* (or `Alt+Shift+S`). Tick *Toujours activer sur
  cette chaîne* to start automatically on that channel.
- Two lines at the bottom of the player: Japanese (grey = still changing),
  English (italic while it streams in). Drag the small handle on the left of
  the subtitles to move them; double-click it to reset.
- `Alt+Shift+D` cycles JA + EN → EN → JA. `Alt+L` shows the latency HUD.
- The popup shows the connection, the models, the JA / EN latency (p50 / p95
  over 5 min), and exports the transcript as SRT / VTT. A transcript panel sits
  above the live chat; click a line to jump there.
- Options (server URL, token, size, opacity, previous sentence, capture
  fallbacks): popup → *Options*.
- **Ahead mode** (options → *Mode « en avance »*): the server pulls the live
  itself and the player is kept ~6 s behind the live edge, so subtitles appear
  *with* the sentence instead of after it. Falls back to normal capture by
  itself if the live cannot be pulled or aligned (ADR-009).

Glossaries per channel (names, fan names, game terms, with their English
spelling): [`glossaries/`](glossaries/README.md).

## Development

| | |
|---|---|
| `just lint` | ruff + mypy --strict, eslint + tsc + web-ext lint — green before any commit |
| `just test` | pytest (no GPU) + vitest, including the protocol drift test |
| `just test-gpu` | real models on the V100s, including the golden replay |
| `just golden-update` | regenerate the golden outputs (review the diff before committing) |
| `just bench-prepare` / `bench-asr` / `bench-mt` | benchmarks of WP04 / WP06 |
| `just protocol-schema` | after changing `protocol.py`; mirror it in `protocol.ts` |

Work is tracked in GitHub Issues (one per work package, board in #16).

### Manual checklist before each extension release (WP12 §4)

- [ ] Real live, 15 min: JA and EN present, popup latencies within budget.
- [ ] An ad in the middle: no subtitles of the ad, clean resume.
- [ ] Change video in the tab; seek back in the DVR; fullscreen.
- [ ] Server stopped then restarted during playback: reconnects by itself.
- [ ] Two YouTube tabs, only one captured.
