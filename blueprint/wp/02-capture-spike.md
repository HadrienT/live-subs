# WP 02 — Spike : capture audio dans Firefox sur YouTube

| | |
|---|---|
| **Dépend de** | [00](00-foundations.md) (douce) |
| **Bloque** | [07](07-extension-capture.md) |
| **Branche** | `spike/capture` — le code du spike n'est pas destiné à `main` tel quel |
| **Durée visée** | court. C'est une levée de risque, pas une fonctionnalité |

## Objectif

Répondre **par l'expérience** aux questions dont dépend toute
l'architecture côté navigateur ([ADR-001](../decisions.md#adr-001--capture-dans-le-navigateur-sur-lélément-video),
[ADR-004](../decisions.md#adr-004--websocket-ouvert-par-le-background-pas-par-le-content-script)),
et consigner les réponses dans `decisions.md`.

Livrable : une extension minimale qui, sur un live YouTube, enregistre 60 s
d'audio en WAV 16 kHz mono (téléchargé par le navigateur) et l'envoie en même
temps à un petit serveur d'écho WebSocket sur `192.168.1.200`.

## Questions, et comment y répondre

| # | Question | Test | Plan B |
|---|---|---|---|
| Q1 | `mozCaptureStream()` fonctionne-t-il sur le `<video>` d'un **live** (MSE) ? | Capturer, vérifier que le WAV contient la voix | `captureStream()` s'il existe ; sinon WebAudio `createMediaElementSource` |
| Q2 | Le son reste-t-il audible ? | Écouter. S'il est coupé, connecter la source à `audioContext.destination` | `createMediaElementSource(video)` capture ET joue |
| Q3 | Le **volume / muet** de YouTube agit-il sur le flux capturé ? | Couper le son de YouTube, vérifier le WAV | Documenter : « baisser le volume système, pas celui de YouTube » |
| Q4 | `audioWorklet.addModule()` passe-t-il la CSP de YouTube depuis un content script, avec une URL `moz-extension://` ? | Charger le worklet, regarder la console | Blob URL ; sinon `ScriptProcessorNode` (déprécié mais présent) |
| Q5 | Le rééchantillonnage 48 k → 16 k dans le worklet est-il propre ? | Spectrogramme du WAV, écoute | Filtre passe-bas + décimation, ou rééchantillonner côté serveur (`soxr`) |
| Q6 | Le WebSocket `ws://192.168.1.200` s'ouvre-t-il depuis le **background** ? Et depuis le content script ? | Les deux, regarder la console et le serveur d'écho | Si ni l'un ni l'autre : `wss://` avec une CA locale |
| Q7 | Que devient le flux pendant une **pub**, un changement de vidéo (navigation SPA), un passage en qualité différente, un retour dans le DVR ? | Observer `video.src`, `emptied`, `loadedmetadata`, la classe `.ad-showing` sur `#movie_player` | Recréer la capture à chaque changement de source |
| Q8 | Débit réel content → background par `runtime.Port` à 10 trames/s ? | Compter les messages perdus, mesurer le délai | Grouper par 200 ms |

## Critères d'acceptation

- [ ] Un WAV de 60 s propre, capté sur un vrai live japonais, rangé dans
      `benchmarks/data/` (hors git) : il servira aussi au lot 04.
- [ ] Chaque question Q1–Q8 a sa réponse, datée, avec la version de Firefox,
      dans `decisions.md` (nouvel ADR ou note sous ADR-001 / ADR-004).
- [ ] Si une réponse invalide ADR-001 ou ADR-004, le blueprint est corrigé
      avant de démarrer le lot 07.
