# WP 07 — Extension : capture & transport

| | |
|---|---|
| **Dépend de** | [02](02-capture-spike.md) (dure), [01](01-protocol.md) (dure) |
| **Bloque** | [08](08-extension-overlay.md) |
| **Branche** | `feat/ext-capture` |

## Objectif

Du `<video>` de YouTube au serveur : une capture qui démarre toute seule sur
un stream, survit aux pubs et à la navigation, et se reconnecte sans rien
demander. Les choix techniques sont ceux que le spike (lot 02) a validés. Ce
lot ne fait que les industrialiser.

## 1. Content script (`src/content/capture.ts`)

- **Cible** : `youtube.com/watch*` et `youtube.com/live/*`. Activation
  **par onglet**, depuis le popup ou par raccourci. Par défaut pas
  d'automatisme, sauf pour les chaînes cochées « toujours » (lot 08).
- **Trouver le lecteur** : `#movie_player video.html5-main-video`, avec un
  `MutationObserver`, parce que YouTube recrée l'élément.
- **Navigation SPA** : écouter `yt-navigate-finish` ; nouveau `video_id` →
  nouvelle session (nouveau `hello`).
- **Pubs** : `#movie_player.ad-showing` → `pause` au serveur, puis `resume`
  avec le drapeau de discontinuité.
- **Seek / DVR / pause du lecteur** : `seeking`, `pause`, `play` → même
  mécanique de discontinuité.
- **Graphe audio** : source capturée → `AudioWorkletNode` (rééchantillonnage,
  trames de 100 ms en int16) ; sortie vers `destination` si le spike l'a jugé
  nécessaire (Q2).
- Les trames partent par `runtime.Port` vers le background, avec
  `sample_idx` et `media_time`.

## 2. Worklet (`src/worklet/downsampler.ts`)

Filtre passe-bas (FIR ~ 31 coefficients, coupure 7,2 kHz) puis décimation
48 k → 16 k (ou 44,1 k → 16 k par rééchantillonnage fractionnaire selon
`sampleRate`). Conversion float → int16 avec saturation. Testé en vitest sur
des sinusoïdes (atténuation hors bande, pas de repliement audible).

## 3. Background (`src/background/`)

- Une connexion WebSocket **par onglet actif**, créée au premier `hello`.
- Reconnexion avec backoff exponentiel plafonné (0,5 → 8 s). Pendant la coupure,
  les trames sont **jetées**, pas mises en tampon : du retard rattrapé plus tard
  ne sert à rien en direct.
- Renvoie au content script les messages serveur de l'onglet.
- Tient l'état affiché par le popup : connecté / reconnexion / erreur, modèle,
  latences (`stats`).
- URL du serveur et jeton dans `storage.local`, défaut
  `ws://192.168.1.200:8765/ws`.

## 4. Faux serveur

`extension/test/fake-server.ts` : un serveur WebSocket Node qui répond `ready`
et renvoie des `partial` / `final` / `translation` scriptés, pour développer
l'extension sans GPU (`just ext-run` + `just fake-server`).

## Critères d'acceptation

- [ ] Sur un live réel, 30 min sans intervention : pubs, passage en plein écran,
      changement de qualité ; le serveur ne voit pas de trou non signalé.
- [ ] Coupure du serveur puis redémarrage → reconnexion seule, en ≤ 10 s.
- [ ] Changement de vidéo dans le même onglet → nouvelle session, nouveau
      `video_id`.
- [ ] Le son du stream n'est jamais coupé ni doublé.
