# WP 08 — Extension : overlay & réglages

| | |
|---|---|
| **Dépend de** | [07](07-extension-capture.md), [01](01-protocol.md) |
| **Bloque** | [09](09-sync-latency.md), [11](11-extension-packaging.md), [14](14-history-export.md) |
| **Branche** | `feat/ext-overlay` |

## Objectif

Des sous-titres qu'on lit sans effort, par-dessus le lecteur, en fenêtre comme
en plein écran.

## 1. Overlay

- Un hôte **shadow DOM** inséré dans `#movie_player` : il suit le plein écran
  et le mode cinéma, et le CSS de YouTube ne l'atteint pas.
- Deux lignes, en bas, au-dessus des contrôles (remonter quand la barre de
  contrôle est visible, `#movie_player.ytp-autohide` absent) :
  - **JA** : partie stable en blanc, partie instable en gris (lot 05 §3) ;
  - **EN** : se remplit en flux, en italique tant que la traduction n'est pas
    finie.
- Chaque ligne garde le **dernier `final`** affiché jusqu'au suivant, ou
  jusqu'à 6 s de silence.
- Lisibilité : fond semi-opaque, police système CJK (`"Noto Sans JP",
  "Hiragino Sans", sans-serif`), taille relative à la hauteur du lecteur.
- Option « deux dernières phrases » : la précédente en plus petit, au-dessus.
- Déplaçable à la souris ; position mémorisée.

## 2. Popup

- Interrupteur on/off pour l'onglet, état de connexion, modèle ASR / MT.
- Latences p50 JA / EN (message `stats`), profondeur de file.
- Choix d'affichage : JA + EN / EN seul / JA seul.
- « Toujours activer sur cette chaîne ».

## 3. Page d'options

URL du serveur, jeton, taille et opacité, afficher les `partial` ou non,
raccourcis clavier (`commands` du manifeste : activer/désactiver, basculer
l'affichage).

## Critères d'acceptation

- [ ] Lisible en fenêtre, en mode cinéma et en plein écran, sur un 1080p et un
      écran 4K.
- [ ] N'intercepte aucun clic destiné au lecteur, hors poignée de déplacement.
- [ ] Les réglages survivent au redémarrage de Firefox.
- [ ] Tests vitest du réducteur d'état de l'overlay (remplacement des `partial`
      par le `final`, fusion des `translation_delta`, expiration).
