# live-subs — notes pour Claude Code

Sous-titres **japonais + anglais quasi en direct** sur les streams YouTube
regardés dans Firefox. Deux moitiés dans ce dépôt, qui évoluent ensemble :

| Moitié | Où | Ce qu'elle fait |
|---|---|---|
| Extension Firefox | `extension/` | Capture l'audio de l'élément `<video>` de YouTube (`mozCaptureStream()`), le ramène à du PCM 16 kHz mono, l'envoie au serveur par WebSocket, affiche les sous-titres en surimpression du lecteur |
| Serveur d'inférence | `server/` (paquet Python `livesubs`) | Reçoit l'audio, découpe la parole (Silero VAD), transcrit en japonais (Whisper spécialisé japonais, GPU), traduit en anglais (LLM local), renvoie les segments |

Le PC qui regarde le stream et ce serveur (`192.168.1.200`) sont sur le même
switch. **Rien ne sort du LAN** : pas d'API cloud, ni pour la transcription ni
pour la traduction.

## Chaîne, couche par couche

```
<video> YouTube ─ mozCaptureStream ─► AudioWorklet (48k→16k, int16)
   └─ content script ──port──► background ──WebSocket──► serveur :8765
serveur : ingest → VAD → ASR (GPU) → segment JA ──► LLM (llama-server) → EN
   ◄── partial / final / translation (JSON) ── overlay dans #movie_player
```

- **Contrat du fil** : `server/src/livesubs/protocol.py` est la source de vérité ;
  `extension/src/protocol.ts` en est le **miroir manuel**, protégé par un test de
  dérive (même motif qu'`AgenticEnv` ↔ `agenticenv-chat`). Toute évolution du
  protocole touche les deux fichiers dans le même commit et incrémente
  `PROTOCOL_VERSION`.
- **ASR** : modèle par défaut `kotoba-whisper-v2.0` (distillé de Whisper
  large-v3 sur du japonais) via `faster-whisper` / CTranslate2, fp16. Le choix
  définitif sort du banc d'essai du [lot 04](blueprint/wp/04-asr-benchmark.md).
  Ne pas changer de modèle sans repasser le banc.
- **Traduction** : client OpenAI-compatible vers le **`llama-server`
  d'`~/AgenticEnv`** (llama.cpp, `configs/models.yaml` y est le registre des
  modèles). URL : `LIVESUBS_LLM_BASE_URL`, défaut `http://127.0.0.1:8000/v1`
  hors Docker, `http://172.17.0.1:8001/v1` depuis un conteneur (socket
  `llama-bridge` d'AgenticEnv). Un seul modèle est chargé à la fois ;
  AgenticEnv bascule entre des **profils** : `code` (Qwen3-Coder, agent de code)
  et `translate` (le meilleur traducteur JA → EN, choisi au banc du lot 06).
  live-subs demande son modèle par nom (`LIVESUBS_LLM_MODEL`) et signale le
  profil `code` s'il est chargé, sans jamais traduire avec lui en silence. Ce
  dépôt **ne pilote pas** llama-server : modèles, profils et mécanisme de
  bascule se changent dans AgenticEnv, par une issue là-bas. Voir
  [ADR-003](blueprint/decisions.md).

## GPU — ressource partagée

Deux Tesla V100 16 Go (compute capability 7.0 : **pas de bf16**, fp16 et
int8_float16 seulement), partagées avec `llama-server` (AgenticEnv, budget
20 Gio sur les deux cartes) et le backend CUDA de `~/quant-modeling`.

- L'ASR se réserve **au plus 4 Gio sur GPU 0** (`LIVESUBS_ASR_DEVICE=cuda:0`).
  Tout dépassement se mesure et se documente dans le lot 04 avant d'être accepté.
- Jamais de vLLM ici : ses versions récentes ont abandonné Volta.
- Avant de conclure qu'un modèle est lent, vérifier qu'il tourne vraiment sur
  GPU (`nvidia-smi --query-compute-apps=pid,used_memory --format=csv`).

## Suivi du travail — GitHub Issues, pas de markdown de handoff

Le « JIRA » du projet, ce sont les **GitHub Issues** de
[`HadrienT/live-subs`](https://github.com/HadrienT/live-subs) (privé ; `gh` est
authentifié, compte `HadrienT`). Une issue par lot (`WP00`–`WP14`, label `wp`),
et une issue épinglée **`📋 Board`** (live-subs#16) qui les range par jalon et
liste les dépendances AgenticEnv (AgenticEnv#15, AgenticEnv#16).

- **Au démarrage d'une session** : `gh issue list --state open` ici, et sur
  `HadrienT/AgenticEnv` les issues `cross-repo` qui nous bloquent.
- **Lot ou issue terminé** → fermer, et cocher la case dans le Board.
- **Issue traitée** → `gh issue close <n> --comment "fait dans <sha>"`.
- **Une tâche qui concerne AgenticEnv** (modèle servi, contexte, llama-bridge) →
  `gh issue create --repo HadrienT/AgenticEnv …`, label `cross-repo`.
- **Jamais** de fichier markdown de passation entre sessions. Une tâche qui
  survit à la session est une issue.
- Les gros morceaux de **conception** vivent dans `blueprint/wp/*.md` ; l'issue
  y renvoie, elle ne les remplace pas. `blueprint/README.md` porte le graphe de
  dépendances : le consulter avant de démarrer un lot pour vérifier que ses
  prérequis sont faits.

## Commandes

Cible (mise en place par le [lot 00](blueprint/wp/00-foundations.md)) :

| | |
|---|---|
| `just lint` | ruff + `mypy --strict` (serveur), eslint + `tsc --noEmit` + `web-ext lint` (extension) — **vert avant tout commit** |
| `just test` | pytest (hors `gpu` / `e2e`) + vitest, dont le test de dérive du protocole |
| `just test-gpu` | tests marqués `gpu` : vrais modèles sur les V100 (hors CI) |
| `just run-server` | serveur en local, `ws://0.0.0.0:8765` |
| `just replay <fichier.wav>` | rejoue un enregistrement dans le serveur comme le ferait l'extension, affiche les segments et les latences |
| `just bench-asr` / `just bench-mt` | bancs d'essai des lots 04 et 06, résultats dans `benchmarks/` |
| `just ext-build` | build esbuild de l'extension dans `extension/dist/` |
| `just ext-run` | `web-ext run` : Firefox jetable avec l'extension chargée |

## Conventions

- **Conversation avec le mainteneur : en français.** Code, identifiants,
  commentaires, messages de commit : en anglais. `blueprint/` est en français.
- **Commits : passer par une branche, jamais directement sur `main`.** Terminer
  les messages par `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Python** : 3.13, `uv`, ruff (lint + format), `mypy --strict`. Tout ce qui
  touche au GPU est derrière une interface (`Transcriber`, `Translator`) avec un
  faux pour les tests : la suite par défaut doit passer sans GPU.
- **Extension** : TypeScript strict, esbuild, Manifest V3 Firefox. Aucune
  dépendance runtime lourde : c'est un content script injecté dans YouTube.
  L'overlay vit dans un **shadow DOM** accroché à `#movie_player` (il suit le
  plein écran, et le CSS de YouTube ne le touche pas).
- **Fixtures audio** : uniquement des sources à licence libre (Common Voice CC0,
  JSUT…) dans le dépôt. Les extraits de streams enregistrés pour les bancs
  restent dans `benchmarks/data/`, **ignoré par git**.
- Chaque latence mesurée se donne en **p50 / p95** et se rapporte à la fin de
  la parole (fin du segment VAD), pas au début du segment.

## Réseau et Docker

- Le serveur écoute sur l'adresse LAN (`192.168.1.200:8765`), **jamais** exposé
  par le tunnel Cloudflare de quant-modeling. Pas d'authentification forte : un
  jeton partagé optionnel (`LIVESUBS_TOKEN`) suffit sur le LAN.
- **Réseau Docker fragile sur cet hôte** : un teardown de veth sur `docker0` a
  déjà figé la machine et coupé tout le LAN (AgenticEnv#8). Jamais de
  `docker rm -f` en masse ni de `docker network prune` ; agir un conteneur à la
  fois, en expliquant le rayon d'impact. D'autres stacks tournent sur ce serveur
  (quant-modeling, quant-platform, data-ingest) : ne pas les toucher.
