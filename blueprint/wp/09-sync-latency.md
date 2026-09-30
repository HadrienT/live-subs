# WP 09 — Synchronisation & latence

| | |
|---|---|
| **Dépend de** | [05](05-streaming-asr.md), [08](08-extension-overlay.md) |
| **Bloque** | [13](13-ahead-of-live.md) |
| **Branche** | `feat/sync-latency` |

## Objectif

Savoir, chiffres à l'appui, combien de temps s'écoule entre une parole et son
sous-titre, et afficher chaque sous-titre au bon moment de la vidéo, même
après une pause ou un retour dans le DVR.

## 1. Trois horloges

| Horloge | Où | Sert à |
|---|---|---|
| `sample_idx` | serveur | Découpage, ordre, bornes des segments |
| `media_time` | vidéo | Replacer un segment dans la vidéo |
| horloge murale | les deux (`performance.now()`, `time.monotonic()`) | Mesurer la latence |

Le serveur convertit les bornes en `media_time` (lot 01). L'extension n'affiche
un segment que si la vidéo est **au-delà** de son `t0`, et l'oublie si
l'utilisateur est revenu avant `t0` (seek arrière) : on n'affiche pas un
sous-titre pour un passage qu'on n'a pas encore réentendu.

## 2. Mesures bout en bout

Pour chaque segment, l'extension calcule `latence = media_time_affichage −
t1` (où `t1` est la fin de la parole) séparément pour JA et EN. Elle en garde
les p50 / p95 glissants sur 5 min, affichés dans le popup et en HUD optionnel
(`Alt+L`).

Côté serveur, `metrics.py` décompose : attente VAD, file GPU, décodage ASR,
premier jeton MT, MT totale. Journalisés en JSON par segment, résumés dans le
message `stats`.

## 3. Réglages qui en découlent

Avec les mesures en main, régler `min_silence_ms`, la cadence des `partial` et
le seuil de fusion des traductions (lot 06 §3). Documenter les valeurs retenues
et les chiffres qui les justifient dans `decisions.md`.

## Critères d'acceptation

- [ ] Latences JA / EN visibles dans le popup, sur un vrai stream.
- [ ] Objectifs du [README §4](../README.md#4-budget-de-latence-cible-à-mesurer-au-lot-09)
      atteints, ou écart expliqué et chiffré.
- [ ] Seek arrière de 30 s dans le DVR : pas de sous-titre fantôme de « l'avenir ».
- [ ] Mesure faite deux fois : agent de code d'AgenticEnv au repos, puis en
      plein tour (effet du partage de llama-server).
