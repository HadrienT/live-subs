# WP 13 — *(optionnel)* Mode « en avance »

| | |
|---|---|
| **Dépend de** | [09](09-sync-latency.md), [10](10-server-deploy.md) |
| **Bloque** | — |
| **Branche** | `feat/ahead-of-live` |

## Idée

La capture navigateur ([ADR-001](../decisions.md#adr-001--capture-dans-le-navigateur-sur-lélément-video))
a une limite indépassable : le sous-titre arrive **après** la parole. Pour un
sous-titre synchrone, il faut que le serveur entende l'audio **avant**
l'utilisateur.

Sur un live, c'est possible : le serveur tire lui-même le flux au bord du
direct (`yt-dlp` / `streamlink`, audio seul), et l'extension place la vidéo
quelques secondes derrière le direct (le DVR de YouTube le permet). Les
sous-titres sont alors prêts avant que la phrase soit jouée et s'affichent au
bon `media_time`.

## Ce qu'il faut résoudre

- **Aligner les deux lectures** : faire correspondre le temps du flux tiré par
  le serveur et le `currentTime` du lecteur. Piste : les deux lisent les mêmes
  segments HLS / DASH ; sinon, corrélation croisée de l'enveloppe audio entre
  quelques secondes captées par l'extension et le flux du serveur.
- **Retard choisi** : latence EN p95 mesurée au lot 09 + marge (≈ 4–6 s).
- **Coût** : un deuxième téléchargement du flux (audio seul, ≈ 128 kbit/s).
- Les lives réservés aux membres ou sous restriction d'âge demandent des
  cookies : hors périmètre en v1.

## Critères d'acceptation

- [ ] Sur un live public, sous-titres EN affichés **pendant** la phrase,
      décalage mesuré < 300 ms.
- [ ] Repli automatique vers la capture navigateur si l'alignement échoue.
