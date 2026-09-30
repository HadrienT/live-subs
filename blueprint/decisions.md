# Décisions techniques

Format ADR allégé : le choix, la raison, ce qu'on a écarté et pourquoi. Une
décision n'est pas un dogme, mais la révoquer demande d'écrire ici pourquoi,
pas de la contourner en douce dans le code.

---

## ADR-001 — Capture dans le navigateur, sur l'élément `<video>`

**Décision.** L'extension capture l'audio de l'élément `<video>` de YouTube par
`HTMLMediaElement.mozCaptureStream()` (ou `captureStream()` s'il existe),
l'envoie dans un `AudioContext`, et **reroute le flux vers
`audioContext.destination`** pour que le son reste audible.

**Pourquoi.** C'est exactement l'audio que la personne entend, au moment où
elle l'entend : pas de deuxième connexion au stream, pas de décalage entre ce
que le serveur transcrit et ce que le lecteur joue, et le point de lecture
suit tout seul (DVR, pause, retour en arrière). Les lives YouTube n'ont pas de
DRM, donc la capture n'est pas bloquée.

**Écarté.**
- *`tabCapture`* : n'existe pas dans Firefox.
- *Capture système (PulseAudio / PipeWire du PC)* : exige un logiciel natif sur
  le PC en plus de l'extension, et capte tous les sons (notifications, autre
  onglet).
- *Le serveur tire le flux lui-même (yt-dlp / streamlink)* : très bon pour la
  qualité audio et permet d'avoir de l'**avance** sur la lecture, mais il faut
  synchroniser deux lectures du même direct. Gardé comme mode optionnel
  ([lot 13](wp/13-ahead-of-live.md)), pas comme chemin principal.

**Risques à lever au [lot 02](wp/02-capture-spike.md)** : Firefox coupe la
sortie de l'élément une fois capturé (d'où le reroutage) ; l'effet du bouton
muet / volume de YouTube sur le flux capturé ; la CSP de YouTube face à
`audioWorklet.addModule()` ; le changement de `src` lors des pubs et de la
navigation SPA.

---

## ADR-002 — ASR : Whisper spécialisé japonais via faster-whisper, choisi au banc

**Décision.** Modèle par défaut **`kotoba-whisper-v2.0`** (Whisper large-v3
distillé sur ReazonSpeech, ~6× plus rapide que large-v3 et au moins aussi bon
en japonais d'après ses auteurs), servi par **`faster-whisper`** (CTranslate2)
en `float16` sur GPU 0. Le choix définitif sort du [lot 04](wp/04-asr-benchmark.md) :
CER sur de vrais extraits de streams, et latence mesurée sur V100.

**Pourquoi.** Le japonais est le point faible des modèles multilingues
génériques sur la parole spontanée (streams, rires, parole rapide). Un modèle
distillé garde l'encodeur de large-v3 mais n'a que 2 couches de décodeur : c'est
le décodeur qui coûte en temps réel, parce qu'on re-décode la fenêtre en cours
toutes les secondes pour les `partial`. CTranslate2 marche sur Volta (fp16,
int8_float16), sans dépendre de flash-attention.

**Écarté a priori (mais mesuré au banc).**
- *Whisper large-v3* : référence de qualité, 5–6× plus lent au décodage.
- *large-v3-turbo* : rapide, mais distillé pour la transcription multilingue en
  général, pas pour le japonais.
- *ReazonSpeech v2 (NeMo RNN-T / k2 Zipformer)* : entraînés sur 35 000 h de TV
  japonaise, de vrais modèles en flux. Candidats sérieux ; ils ont contre eux
  une pile d'exécution différente (NeMo, sherpa-onnx), à n'adopter que si le
  banc le justifie.
- *anime-whisper* : affiné sur du doublage d'anime ; à tester si les streams
  suivis sont des VTubers.
- *Traduction directe par Whisper (`task=translate`)* : qualité nettement
  inférieure à un LLM, pas de contexte, et on perdrait la ligne japonaise.

---

## ADR-003 — Traduction par le `llama-server` d'AgenticEnv

**Décision.** Le traducteur est un **client OpenAI-compatible** (`/v1/chat/completions`
en flux) vers le `llama-server` déjà en service pour AgenticEnv. live-subs ne
lance pas de deuxième LLM. Le modèle, le contexte et le placement GPU se
décident dans `~/AgenticEnv/configs/models.yaml`, pas ici.

**Pourquoi.** Le serveur a 32 Go de VRAM au total et llama-server en réserve
déjà 20 Gio. Un deuxième LLM de 7–14B prendrait ce qui reste et entrerait en
conflit avec l'ASR et le backend CUDA de quant-modeling. Qwen3-30B-A3B n'a que
~3B paramètres actifs par jeton : il est rapide, et la famille Qwen3 est solide
en japonais. `cont_batching` permet de partager l'instance avec l'agent de code.

**Conditions.**
- **llama-server doit tourner sur GPU.** Au 30/09/2026, il tourne sur CPU
  (voir [README §2](README.md#2-ce-qui-existe-déjà-sur-le-serveur)). À corriger
  dans AgenticEnv avant le lot 06.
- Un Qwen3-**Coder** n'est pas le meilleur choix pour traduire. Le banc de
  traduction du [lot 06](wp/06-translation.md) le compare à
  `Qwen3-30B-A3B-Instruct-2507` (même taille, même vitesse, généraliste) et à un
  traducteur dédié (`plamo-2-translate`). Si l'écart est net, la décision
  (changer le modèle servi, ou en servir deux) se prend **dans AgenticEnv**, par
  une issue `cross-repo`.
- Quand l'agent de code est en plein tour, la traduction attend son créneau.
  On mesure ce retard (lot 09) avant d'envisager une instance dédiée.

**Écarté.**
- *vLLM* : ses versions récentes ne supportent plus Volta (CC 7.0).
- *Ollama* : une couche de plus au-dessus de llama.cpp, qui est déjà là.
- *NLLB / M2M100 / opus-mt ja-en* : rapides, mais phrase par phrase sans
  contexte : les pronoms omis du japonais et les noms propres des streams leur
  échappent.
- *API cloud (DeepL, Claude…)* : contraire au « rien ne sort du LAN ».

---

## ADR-004 — WebSocket ouvert par le background, pas par le content script

**Décision.** Le content script capture l'audio et pousse les trames PCM au
**background** de l'extension par un `runtime.Port`. C'est le background qui
ouvre `ws://192.168.1.200:8765` et renvoie les segments au content script.

**Pourquoi.** youtube.com est en HTTPS. Un `ws://` ouvert depuis le contexte de
la page tombe sous le blocage du contenu mixte et sous la CSP `connect-src` de
YouTube. Le background a une origine `moz-extension://`, qui n'est soumise
qu'aux permissions d'hôte déclarées dans le manifeste. Un seul point de
connexion permet aussi de gérer proprement la reconnexion, plusieurs onglets et
l'état affiché dans le popup. Le coût (sérialiser 32 ko/s entre deux contextes)
est négligeable.

**Écarté.**
- *`wss://` avec un certificat auto-signé* : il faudrait faire accepter le
  certificat à Firefox, et le renouveler. Si on en a besoin un jour, ce sera un
  certificat d'une CA locale ; pas en v1.
- *WebSocket depuis le content script* : à retester au lot 02, mais on ne bâtit
  pas dessus.

---

## ADR-005 — Serveur : un seul processus Python asyncio, GPU derrière des interfaces

**Décision.** Un processus FastAPI/uvicorn. Chaque connexion est une session
(tampon, VAD, état du segment). L'ASR et le LLM sont derrière des `Protocol`
(`Transcriber`, `Translator`). L'inférence GPU tourne dans un exécuteur à un
seul thread, derrière une file : **un décodage à la fois sur le GPU**.

**Pourquoi.** Une personne regarde un stream à la fois. Une file de requêtes
GPU explicite évite les pics de VRAM et rend la latence prévisible. Avec les
interfaces, toute la suite de tests tourne sans GPU, sur de faux modèles.

**Écarté.**
- *Kafka / Redis entre les étapes* : aucune utilité pour un seul flux, et une
  latence ajoutée à chaque saut.
- *Un serveur Triton / whisper.cpp séparé* : un service de plus à exploiter
  pour zéro gain à ce volume.

---

## ADR-006 — Extension : Manifest V3 Firefox, TypeScript, esbuild, web-ext

**Décision.** MV3 (Firefox garde les background scripts « event page », pas
d'obligation de service worker), TypeScript strict, bundle esbuild (même
outillage qu'`agenticenv-chat`), `web-ext` pour lancer, vérifier et signer.

**Pourquoi.** MV3 est la cible à long terme de Firefox. esbuild produit un
bundle par contexte (content, background, popup, worklet) sans configuration.
Pas de framework UI : l'overlay tient en deux lignes de texte, et le popup en
quelques contrôles.

**Écarté.** *React dans le content script* : 40 ko injectés dans chaque page
YouTube pour afficher deux `<div>`.
