# WP 03 — Ingestion & VAD

| | |
|---|---|
| **Dépend de** | [01](01-protocol.md) |
| **Bloque** | [05](05-streaming-asr.md), [10](10-server-deploy.md), [12](12-quality-e2e.md) |
| **Branche** | `feat/ingest-vad` |

## Objectif

Le serveur accepte une session WebSocket, reçoit les trames, et produit des
**segments de parole** horodatés. On ne transcrit pas encore : on les écrit
en WAV pour vérifier le découpage à l'oreille.

## 1. Session

`session.py` : une instance par connexion.

- Vérifie `hello` (version, jeton si `LIVESUBS_TOKEN` est défini), répond
  `ready`.
- **Tampon circulaire** float32 de 30 s, indexé par `sample_idx`. Les trames
  arrivées dans le désordre ou en double sont ignorées ; un trou (trame perdue)
  est rempli de silence et journalisé.
- Table `sample_idx → media_time` (une entrée par trame) pour convertir les
  bornes des segments (lot 01 §3).
- Le drapeau `discontinuité` et `pause` **ferment** le segment en cours sans
  attendre le silence.

## 2. VAD

**Silero VAD v5** en ONNX sur CPU (≈ 1 ms par fenêtre de 32 ms ; pas besoin du
GPU). Paramètres de départ, tous dans `config.py` :

| Paramètre | Défaut | Rôle |
|---|---|---|
| `vad_threshold` | 0,5 | Probabilité de parole |
| `min_speech_ms` | 250 | Ignore les bruits brefs |
| `min_silence_ms` | 400 | Fin de phrase → `final` |
| `max_segment_s` | 12 | Coupe forcée d'une parole ininterrompue, au creux de probabilité le plus bas des 2 dernières secondes |
| `pad_ms` | 200 | Marge avant / après, pour ne pas manger les consonnes |

Sortie : `SpeechSegment(seg_id, start_idx, end_idx | None, is_closed)`. Un
segment ouvert est republié à chaque nouvelle seconde de parole : c'est ce qui
cadencera les `partial` au lot 05.

Point d'attention propre aux streams : **musique de fond et bruits de jeu**.
Mesurer le taux de faux segments sur l'enregistrement du lot 02. Si la musique
déclenche le VAD, le filtre `no_speech_prob` de l'ASR (lot 05) fait la seconde
barrière.

## 3. `tools/replay.py`

Lit un WAV / MP3 / une URL de fichier (via ffmpeg), le découpe en trames
exactement comme l'extension, les envoie **au rythme réel** (ou `--speed 4`)
et affiche les messages reçus avec leur latence. C'est l'outil de travail de
tous les lots serveur, et la base du test bout en bout du lot 12.

## Critères d'acceptation

- [ ] `just replay benchmarks/data/<extrait>.wav --dump-segments out/` écrit
      un WAV par segment ; à l'écoute, les coupures tombent entre les phrases.
- [ ] Tests : trames désordonnées, trou, discontinuité, `pause` au milieu d'un
      segment, coupe forcée à `max_segment_s`.
- [ ] Deux sessions simultanées restent indépendantes.
