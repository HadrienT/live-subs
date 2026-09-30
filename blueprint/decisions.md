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

**Résultat du banc — 30/09/2026 (choix provisoire).**
[`benchmarks/asr/results-2026-09-30.md`](../benchmarks/asr/results-2026-09-30.md),
V100, 200 énoncés de Common Voice 8.0 ja :

| | CER % | CER kana % | 5 s p50 / p95 ms | VRAM Mio |
|---|---|---|---|---|
| **kotoba-whisper-v2.0 fp16 beam 1** (retenu) | 8,65 | 3,55 | 110 / 117 | 2 046 |
| large-v3 int8_float16 beam 5 (meilleur CER) | 8,19 | 3,10 | 276 / 420 | 2 110 |
| large-v3 fp16 beam 1 | 8,32 | 3,12 | 212 / 295 | 3 678 |
| large-v3-turbo fp16 beam 1 | 14,69 | 7,87 | 125 / 140 | 2 142 |

Toutes les configurations respectent les contraintes dures (p95 ≤ 500 ms à 5 s,
≤ 4 Gio). Les CER sont à moins d'un point les uns des autres, sauf turbo en
beam 1 ; la règle départage donc sur la latence, et kotoba est 2 à 2,5× plus
rapide que large-v3 : c'est ce qui compte pour re-décoder les `partial`
chaque seconde. **Provisoire** : le jeu « streams » (parole spontanée, musique
de fond) n'existe pas encore, il viendra des enregistrements du lot 02. Si
large-v3 y prend plus d'un point d'avance, on repasse le banc. anime-whisper et
ReazonSpeech n'ont pas été mesurés : à ajouter si le jeu « streams » est fait
de VTubers.

**Hallucinations : le seuil `no_speech_prob` de Whisper ne marche pas ici.**
Sur 18 extraits de non-parole synthétique (musique, effets de jeu, bruit,
silence pur), kotoba écrit « ごめん » ou « ごちそう » sur **100 %** d'entre eux,
avec `no_speech_prob` ≈ 0,1 (< 0,2 partout, parole comprise) : le décodeur
distillé a perdu ce signal. Ce qui discrimine, c'est `avg_logprob` (parole :
p1 = −0,27 ; hallucinations : −0,37 à −0,86) combiné à une sortie creuse (3
caractères pour 5 à 10 s d'audio). D'où la règle `sparse_low_confidence` de
`livesubs.asr.filters` : rejet si `avg_logprob < −0,3` **et** moins de 2
caractères par seconde. Résultat : 0 % d'hallucination après filtre, 0 % de
vraie parole rejetée (0,5 à 1,5 % pour turbo). Le VAD reste la première
barrière : Silero ne s'est ouvert sur aucun de ces extraits.

**Threads CPU.** Sans limite, OpenBLAS lance un thread par cœur (56) pour le
mel-spectrogramme de faster-whisper : 110 s de CPU pour 2,6 s de décodage, sur
un hôte partagé. Plafonné à 2–4 threads : 5 s de CPU, même latence. Le serveur
fixe `OMP_NUM_THREADS` / `OPENBLAS_NUM_THREADS` (`LIVESUBS_CPU_THREADS`, 4).

**VRAM partagée.** Pendant le banc, `llama-server` est repassé sur GPU
(AgenticEnv#15) et occupe ~12,9 Gio sur GPU 0 et ~12,2 Gio sur GPU 1 : il reste
~3 Gio sur GPU 0. kotoba (2 Gio) y tient ; large-v3 fp16 (3,7 Gio) n'y tient
plus et a été mesuré sur GPU 1.

---

## ADR-003 — Traduction par le `llama-server` d'AgenticEnv, avec des profils de modèle

**Décision.** Le traducteur est un **client OpenAI-compatible** (`/v1/chat/completions`
en flux) vers le `llama-server` d'AgenticEnv. live-subs ne lance pas son propre
LLM. Le serveur ne garde **qu'un modèle chargé à la fois**, mais on passe
**facilement** de l'un à l'autre selon l'activité :

| Profil | Modèle | Pour |
|---|---|---|
| `code` | `Qwen3-Coder-30B-A3B-Instruct` (celui d'aujourd'hui) | Agent OpenHands, assistant de scripting de quant-modeling |
| `translate` | **le meilleur traducteur JA → EN** qui tient dans le budget VRAM, choisi au banc du [lot 06](wp/06-translation.md) | live-subs |

Les profils, le mécanisme de bascule et le registre des modèles vivent **dans
AgenticEnv** (`configs/models.yaml`). live-subs demande un modèle par son nom
(`LIVESUBS_LLM_MODEL`) et, si le modèle chargé n'est pas celui-là, le **dit**
(`error{code:"mt_model_inactive"}`, bandeau dans le popup) et propose la
bascule si AgenticEnv l'expose. Il ne traduit jamais en silence avec le modèle
de code.

**Pourquoi.**
- Un modèle de code n'est pas un bon traducteur, et un traducteur n'est pas un
  bon agent de code. Chaque activité veut le meilleur modèle pour elle.
- 32 Go de VRAM au total, dont 20 Gio réservés au LLM : **deux** gros modèles
  à la fois, plus l'ASR et le backend CUDA de quant-modeling, ne tiennent pas
  sans rogner le contexte de l'agent de code.
- On ne fait jamais les deux en même temps : on regarde un stream **ou** on
  code. Une bascule qui prend 10 à 30 s (chargement du GGUF) est acceptable.

**Mécanisme de bascule : à trancher dans AgenticEnv.** Pistes, par ordre de
préférence :
1. un proxy d'échange à la demande (`llama-swap`, ou le mode « router » de
   llama.cpp s'il est disponible dans la version installée) : le modèle est
   chargé d'après le champ `model` de la requête, un seul résident. Aucun
   geste manuel, mais risque de « ping-pong » si les deux activités tournent
   en même temps ;
2. une commande explicite `just llm-use <profil>` (sudoers étroit, comme au
   WP08f d'AgenticEnv), déclenchable aussi depuis le panneau Components
   d'agenticenv-chat et depuis le popup de live-subs.

**Conditions.**
- **llama-server doit tourner sur GPU.** Au 30/09/2026, il tourne sur CPU
  (voir [README §2](README.md#2-ce-qui-existe-déjà-sur-le-serveur)).
- Le choix du modèle `translate` sort du banc du lot 06. Candidats :
  `plamo-2-translate` (traducteur dédié JA↔EN), `Qwen3-30B-A3B-Instruct-2507`,
  Gemma 3 27B. Condition : servable par llama.cpp sur Volta, ≤ 20 Gio avec le
  contexte nécessaire (8k suffisent pour traduire).

**Modèle du profil `translate` (30/09/2026) : PLaMo 2 Translate, Q8_0.**
Téléchargé depuis `mitmul/plamo-2-translate-GGUF` (révision `4d65036c`), 9,8 Gio.
C'est le seul candidat spécialisé en traduction, et il reste le premier à passer
au banc du lot 06. Il **n'est pas un modèle de chat** : il attend un prompt brut en
blocs `<|plamo:op|>` (`dataset / translation`, puis des tours
`input lang=Japanese` / `output lang=English`), `temperature 0`, et un arrêt sur
`<|plamo:op|>`. live-subs l'appelle donc sur `/v1/completions`
(`livesubs.mt.plamo`, choisi automatiquement quand le nom du modèle contient
« plamo »), sans template de chat côté llama-server. Le contexte glissant passe
par des tours précédents ; le glossaire, qu'il ne peut pas lire comme une
consigne, par des tours déjà traduits (« ぺこら » → « Pekora »). Licence PLaMo
Community : l'usage personnel est libre.

**Écarté.**
- *Traduire avec Qwen3-Coder* : c'était la proposition initiale, écartée par le
  mainteneur. On veut le meilleur traducteur, pas un compromis.
- *Deux instances permanentes* : la VRAM ne suffit pas sans rogner le contexte
  de l'agent de code.
- *vLLM* : ses versions récentes ne supportent plus Volta (CC 7.0).
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

**Note (lot 11, 30/09/2026) — CSP des pages d'extension.** En Manifest V3, la
CSP par défaut de Firefox pour les pages d'extension, background compris, est
`script-src 'self'; upgrade-insecure-requests;`. Cette dernière directive
réécrirait `ws://192.168.1.200:8765` en `wss://`, qui n'existe pas sur le LAN.
Le manifeste déclare donc explicitement
`"extension_pages": "script-src 'self'; object-src 'self'"`. À confirmer
au lot 02 (Q6) : la connexion depuis le background doit s'ouvrir avec cette
CSP, et échouer si on la retire.

**Note — mises à jour.** Firefox ne suit un `update_url` qu'en HTTPS : le
serveur du LAN, en HTTP, ne peut pas servir les mises à jour automatiques de
l'extension. On réinstalle le `.xpi` signé à chaque version (README).

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

---

## ADR-007 — Latences mesurées et réglages retenus (lot 09)

**Mesure du 30/09/2026**, serveur sur l'hôte (hors Docker), kotoba-whisper-v2.0
fp16 sur GPU 0, `llama-server` sur GPU (Qwen3-Coder-30B-A3B, **témoin** : le
profil `translate` n'existe pas encore), rejeu **temps réel** par
`just replay` de Common Voice 8.0 ja enchaîné (pauses de 0,3 à 1,5 s). Latences
depuis la fin du segment VAD (`t1`, qui inclut 200 ms de marge après la
parole), côté client du rejeu ; les `stats` du serveur donnent les mêmes
chiffres à 3 ms près.

| | p50 | p95 | budget p50 (README §4) |
|---|---|---|---|
| ASR (décodage seul) | 109 ms | 121 ms | 300 ms |
| **JA affiché** | **0,32 s** | **0,42 s** | 0,8–1,5 s |
| MT premier jeton | 68 ms | — | — |
| **EN affiché** | **0,56 s** | **0,92 s** | 1,5–2,5 s |

Sur 5 min (59 segments) : JA p50 0,32 s / p95 0,42 s. Traduction : 11
segments de 50 s. Rapporté à la fin réelle de la parole, ajouter les 200 ms de
marge ; côté navigateur, ajouter la trame de 100 ms et le LAN (< 5 ms).

**Réglages** : on garde les défauts (`min_silence_ms` 400, `partial` toutes
les 1 s, fusion au-delà de 3 segments). Rien ne justifie de les resserrer, la
marge sur le budget est de 3× en JA et en EN : `min_silence_ms` plus court
couperait les phrases aux respirations, pour gagner des millisecondes dont on
n'a pas besoin.

**À refaire** sur un vrai stream (lot 02) et avec le modèle du profil
`translate`, puis **en plein tour de l'agent de code** : ce qui manque ici,
c'est l'effet du partage de llama-server, que seule une mesure pendant un tour
d'OpenHands donnera. La fusion des traductions en retard (lot 06 §3) est la
parade prévue.

---

## ADR-008 — Remettre l'état de Silero à zéro après 1 s sans parole

**Constat (rejeu doré du lot 12, 30/09/2026).** Des débuts de phrase
disparaissaient (« 個人情報を集めようとする… » → « 集めようとする… »), une
phrase courte entière aussi (« はいはい »), et la phrase qui suit 10 s de
musique était amputée. Isolés, ces extraits sont bien détectés par Silero
(≈ 50 % de fenêtres de parole) ; dans le flux, non. C'est **l'état récurrent**
de Silero : après une voix forte, puis une voix 20 dB plus faible, ou après de
la musique, il reste bas trop longtemps.

**Décision.** La session remet l'état de Silero à zéro dès qu'il a vu 1 s de
suite sans parole (probabilité < 0,2) et qu'aucun segment n'est ouvert
(`LIVESUBS_VAD_RESET_AFTER_S`, 0 pour désactiver).

**Chiffres** (VAD → kotoba par segment → CER bout en bout, filtre compris) :

| | speech_a | speech_b | speech_bgm | music_gap | CV enchaîné 5 min |
|---|---|---|---|---|---|
| sans remise à zéro | 10,1 % | 6,7 % | 12,9 % | 15,1 % | 9,5 % |
| seuil 0,35 | 6,7 % | 6,7 % | 12,9 % | 9,4 % | 9,3 % |
| **remise à zéro après 1 s** | **2,2 %** | **3,4 %** | 12,9 % | **0,0 %** | **9,1 %** |
| remise à zéro après 0,5 s | 2,2 % | 3,4 % | 12,9 % | 13,2 % | 9,1 % |

Aucun faux segment sur la musique dans aucune variante. Le seuil reste à 0,5.
À revérifier sur de vrais streams (bruits de jeu, musique chantée), où une
remise à zéro trop fréquente pourrait ouvrir des segments sur du bruit : le
filtre d'hallucinations reste la seconde barrière.

---

## ADR-009 — Mode « en avance » : le serveur tire le direct, alignement par enveloppes (lot 13)

**Décision.** Avec `hello.mode = "ahead"`, le serveur tire lui-même le direct
(`yt-dlp` au bord du direct → `ffmpeg` → PCM 16 kHz) et y fait tourner la
chaîne habituelle (session « interne », mêmes VAD / ASR / MT). L'audio capté
par l'extension ne sert plus qu'à **aligner** les deux lectures :
corrélation croisée normalisée des enveloppes log-RMS à 100 Hz sur 15 s de
capture. Les `partial` / `final` de la session interne sont re-datés en temps
de la vidéo (`t − offset`) ; l'extension garde le lecteur `aheadDelayS` (6 s)
derrière le direct, et l'overlay affiche chaque ligne à son `t0`. Le serveur
n'accepte qu'un **identifiant de vidéo** YouTube validé, jamais une URL du
client. Protocole v2 : `hello.mode`, message `ahead_status`
(`aligning` / `aligned` / `failed`, `offset_s`, `lead_s`).

**Repli.** Source impossible (yt-dlp / ffmpeg absents, direct terminé) ou pas
d'alignement en 45 s → `ahead_status{failed}` et la session transcrit la
capture comme en mode normal, sans reconnexion.

**Validé le 30/09/2026.**
- Aligneur : décalage retrouvé à 10 ms près sur de la parole (Common Voice) et
  sur l'audio d'un vrai direct (WeatherNews), capture dégradée (aller-retour
  48 kHz, −6 dB, bruit). Sur une musique très rythmée (128 bpm), le pic est
  juste mais peu marqué : l'aligneur **refuse de conclure** (netteté < 1,25) et
  réessaie, plutôt que de donner un faux décalage.
- Source réelle, dans l'image Docker : premier audio 6,5 s après le lancement,
  puis débit temps réel. Deux pièges corrigés : yt-dlp a lui-même besoin de
  ffmpeg pour les directs (HLS) et de son chemin absolu.
- Bout en bout (serveur + faux ASR, source simulée, lecteur 5 s derrière) :
  aligné à < 50 ms, `lead_s` ≈ 5 s, `final` reçus avant que le lecteur n'y
  arrive ; replis testés.

**Pas encore validé.** Une session réelle depuis Firefox. Et la transcription
de parole tirée d'un vrai direct : au moment du test (minuit à Tokyo), le
direct diffusait de la musique. yt-dlp signale aussi qu'il n'a pas de runtime
JS (deno) : les directs essayés ont fourni leur format audio sans, mais
YouTube peut changer ça. Les directs réservés aux membres restent hors
périmètre (cookies).
