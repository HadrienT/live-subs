# Deployment and usage

## Deploy the server (Docker, starts with the machine)

Read the *Network and Docker* section of [`CLAUDE.md`](../CLAUDE.md) first: Docker
networking on this host is fragile. The compose file creates **no** network (default `bridge`) and one
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

## Install the extension (signed, unlisted)

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

## Use it

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
  itself if the live cannot be pulled or aligned.

Glossaries per channel (names, fan names, game terms, with their English
spelling): [`glossaries/`](../glossaries/README.md).

## Manual checklist before each extension release

- [ ] Real live, 15 min: JA and EN present, popup latencies within budget.
- [ ] An ad in the middle: no subtitles of the ad, clean resume.
- [ ] Change video in the tab; seek back in the DVR; fullscreen.
- [ ] Server stopped then restarted during playback: reconnects by itself.
- [ ] Two YouTube tabs, only one captured.
