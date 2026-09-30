# WP 00 — Fondations & outillage

| | |
|---|---|
| **Dépend de** | — |
| **Bloque** | tous les lots |
| **Branche** | `chore/foundations` |

## Objectif

Un dépôt où `just lint` et `just test` passent au vert sur un squelette vide,
côté serveur comme côté extension, en local et en CI.

## Arborescence cible

```
live-subs/
├── CLAUDE.md
├── justfile
├── .env.example                # toutes les variables LIVESUBS_*
├── server/
│   ├── pyproject.toml          # paquet livesubs, uv, ruff, mypy strict, pytest
│   ├── src/livesubs/
│   │   ├── __init__.py
│   │   ├── config.py           # pydantic-settings, préfixe LIVESUBS_
│   │   ├── protocol.py         # lot 01
│   │   ├── app.py              # FastAPI + route /ws, /health (lot 03)
│   │   ├── session.py          # état par connexion (lot 03)
│   │   ├── vad.py              # lot 03
│   │   ├── asr/                # Transcriber + implémentations (lots 04–05)
│   │   ├── mt/                 # Translator + client llama-server (lot 06)
│   │   └── metrics.py          # lot 09
│   ├── tests/
│   └── Dockerfile              # lot 10
├── extension/
│   ├── package.json            # typescript, esbuild, eslint, vitest, web-ext
│   ├── manifest.json
│   ├── esbuild.mjs
│   ├── src/
│   │   ├── protocol.ts         # miroir de protocol.py (lot 01)
│   │   ├── content/            # capture + overlay (lots 07–08)
│   │   ├── worklet/            # AudioWorkletProcessor (lot 07)
│   │   ├── background/         # WebSocket, état (lot 07)
│   │   └── popup/ options/     # lot 08
│   └── test/
├── benchmarks/                 # lots 04 et 06 ; data/ ignoré par git
├── tools/replay.py             # lot 03
└── blueprint/
```

## Contenu

1. **Serveur** : `uv init` du paquet `livesubs` (Python 3.13), dépendances de
   base (fastapi, uvicorn[standard], pydantic-settings, numpy), groupe `dev`
   (pytest, pytest-asyncio, ruff, mypy). Extras `gpu` (faster-whisper,
   torch pour Silero si besoin) séparés, pour que la CI n'installe pas CUDA.
2. **Extension** : `npm init`, TypeScript strict, esbuild avec une entrée par
   contexte (content, background, worklet, popup, options), eslint, vitest,
   `web-ext` en devDependency. Manifeste MV3 minimal avec
   `browser_specific_settings.gecko.id` (obligatoire pour signer).
3. **justfile** : les commandes listées dans `CLAUDE.md`, même si certaines
   renvoient d'abord « pas encore implémenté ».
4. **pytest** : marqueurs `gpu` et `e2e`, exclus par défaut.
5. **CI GitHub Actions** (une fois le dépôt poussé) : job `server` (ruff, mypy,
   pytest sans GPU), job `extension` (tsc, eslint, vitest, `web-ext lint`).
6. `.env.example` documenté, `.env` ignoré.

## Critères d'acceptation

- [ ] `just lint` et `just test` verts sur une machine sans GPU.
- [ ] `just ext-run` ouvre un Firefox jetable avec l'extension chargée (icône
      et popup vides).
- [ ] `just run-server` répond `200` sur `/health`.
- [ ] La CI passe sur la première PR.
