# WP 01 — Protocole du fil

| | |
|---|---|
| **Dépend de** | [00](00-foundations.md) |
| **Bloque** | [03](03-ingest-vad.md), [07](07-extension-capture.md), [08](08-extension-overlay.md) |
| **Branche** | `feat/protocol` |

## Objectif

Un contrat unique entre l'extension et le serveur, défini en Python
(`server/src/livesubs/protocol.py`, modèles Pydantic) et **reflété à la main**
en TypeScript (`extension/src/protocol.ts`), avec un test qui échoue dès que
les deux divergent.

## 1. Transport

Une connexion WebSocket par onglet qui capture : `ws://<hôte>:8765/ws`.

- **Client → serveur, texte** : messages de contrôle JSON.
- **Client → serveur, binaire** : trames audio.
- **Serveur → client** : uniquement du JSON.

## 2. Trames audio (binaire)

```
offset  taille  champ
0       1       kind        = 0x01 (audio)
1       1       flags       bit0 = discontinuité (seek, pub, reprise)
2       2       reserved
4       8       sample_idx  uint64 LE : index du 1er échantillon depuis le début de la session
12      8       media_time  float64 LE : video.currentTime au moment de la capture (s)
20      n×2     pcm         int16 LE, mono, 16 000 Hz
```

`sample_idx` est l'horloge de référence du serveur. `media_time` sert au lot 09
pour replacer chaque segment dans le temps de la vidéo. Taille nominale
d'une trame : 100 ms, soit 1 600 échantillons (3 220 octets).

## 3. Messages de contrôle

| Sens | `type` | Champs | Rôle |
|---|---|---|---|
| C→S | `hello` | `protocol_version`, `token?`, `video_id`, `channel_id?`, `title?`, `targets: ["ja","en"]` | Ouvre la session ; `channel_id` sélectionne le glossaire (lot 06) |
| S→C | `ready` | `session_id`, `asr_model`, `mt_model`, `sample_rate` | Le serveur accepte |
| C→S | `pause` / `resume` | — | Pub, onglet en pause : le serveur ferme proprement le segment en cours |
| C→S | `config` | `targets?`, `show_partials?` | Réglages en cours de route |
| S→C | `partial` | `seg_id`, `ja_stable`, `ja_unstable`, `t0`, `t1` | Transcription provisoire du segment ouvert (préfixe stable + queue qui peut encore changer, lot 05 §3) |
| S→C | `final` | `seg_id`, `ja`, `t0`, `t1`, `asr_ms` | Transcription définitive |
| S→C | `translation_delta` | `seg_id`, `en_delta` | Jetons de traduction en flux |
| S→C | `translation` | `seg_id`, `en`, `mt_ms` | Traduction définitive |
| S→C | `stats` | `queue_depth`, `gpu_busy`, latences p50/p95 glissantes | Toutes les 5 s, pour le popup |
| S→C | `error` | `code`, `message`, `fatal` | `fatal=true` : le serveur ferme |
| C→S | `ping` / S→C `pong` | `ts` | Mesure du RTT |

`t0` et `t1` sont exprimés **en `media_time`** (secondes de la vidéo) : le
serveur fait la conversion à partir des trames reçues. `seg_id` est un entier
croissant par session. Un `final` remplace tous les `partial` du même `seg_id`.

## 4. Versionnage

`PROTOCOL_VERSION` (entier) dans les deux fichiers. Le serveur refuse un
`hello` d'une version différente avec `error{code:"protocol_mismatch",
fatal:true}` ; le popup affiche « mettre à jour l'extension ».

## 5. Test de dérive

`extension/test/protocol-drift.test.ts` lit `server/src/livesubs/protocol.py`
(même dépôt, donc toujours présent) et vérifie que chaque `type`, chaque champ
et `PROTOCOL_VERSION` existent des deux côtés. Côté Python, un test exporte le
JSON Schema des messages ; le test TS le compare à ses propres types.

## Critères d'acceptation

- [ ] Modèles Pydantic + union discriminée par `type` ; encodeur / décodeur de
      trame binaire avec tests aller-retour.
- [ ] `protocol.ts` : types, garde de type par message, encodeur de trame.
- [ ] Le test de dérive casse si on ajoute un champ d'un seul côté (vérifié à
      la main une fois).
