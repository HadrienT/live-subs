# WP 12 — Qualité & tests bout en bout

| | |
|---|---|
| **Dépend de** | [03](03-ingest-vad.md), [05](05-streaming-asr.md), [06](06-translation.md) (douce) |
| **Bloque** | — |
| **Branche** | `test/e2e` |

## Objectif

Pouvoir modifier le VAD, le prompt ou le modèle en sachant en quelques
minutes si c'est mieux ou pire, sans ouvrir Firefox.

## 1. Trois étages

| Étage | Où | GPU | En CI |
|---|---|---|---|
| Unitaires | `server/tests/`, `extension/test/` | non (faux ASR / MT) | oui |
| Rejeu doré | `server/tests/e2e/` : `tools/replay.py` sur des fixtures libres → comparaison aux sorties de référence | oui (marqueur `gpu`) | non |
| Manuel | checklist ci-dessous, sur un vrai live | oui | non |

## 2. Fixtures

- **Versionnées** : 3–5 extraits de 20–40 s sous licence libre (Common Voice
  ja CC0, JSUT), dont un avec musique de fond **mixée à la main** (musique
  libre) pour tester les hallucinations.
- **Non versionnées** : les extraits de vrais streams de `benchmarks/data/`.

## 3. Rejeu doré

Pour chaque fixture : CER du texte final concaténé ≤ seuil (fixé par le lot 04
+ 2 points de marge), nombre de segments dans ± 20 %, zéro `final` sur les
passages musicaux, latences p95 sous le budget. Les sorties de référence se
régénèrent par `just golden-update`, toujours relues avant d'être commitées.

## 4. Checklist manuelle (avant chaque version de l'extension)

- [ ] Live réel, 15 min : sous-titres JA et EN présents, latences du popup
      dans le budget.
- [ ] Une pub au milieu : pas de sous-titre de la pub, reprise propre.
- [ ] Changement de vidéo dans l'onglet ; retour dans le DVR ; plein écran.
- [ ] Serveur arrêté puis relancé pendant la lecture.
- [ ] Deux onglets YouTube, un seul capté.
