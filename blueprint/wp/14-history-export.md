# WP 14 — *(optionnel)* Historique & export

| | |
|---|---|
| **Dépend de** | [08](08-extension-overlay.md) |
| **Bloque** | — |
| **Branche** | `feat/history` |

## Objectif

Relire ce qui vient d'être dit, et garder une trace d'un stream.

## Contenu

- **Panneau de transcript** repliable, à côté du chat du live : liste
  défilante des couples JA / EN horodatés en `media_time` ; un clic → seek de la
  vidéo à ce moment (si le DVR le permet).
- Recherche plein texte dans la session en cours.
- **Export SRT / VTT** (JA, EN, ou bilingue) de la session, depuis le popup.
- Stockage : `storage.local` limité aux N dernières sessions (quota), ou côté
  serveur en SQLite si on veut garder l'historique de plusieurs streams.
- Aide à la lecture (optionnel) : furigana sur le japonais par
  `pykakasi` / `fugashi` côté serveur, affichés au survol.

## Critères d'acceptation

- [ ] Export SRT d'un stream de 1 h, ouvrable dans VLC avec la VOD du même
      stream, décalage constant et documenté.
- [ ] Le panneau n'ajoute aucune latence à l'overlay principal.
