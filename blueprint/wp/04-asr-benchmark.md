# WP 04 — Banc d'essai ASR japonais

| | |
|---|---|
| **Dépend de** | [00](00-foundations.md) ; des extraits de vrais streams (le lot [02](02-capture-spike.md) en fournit un premier) |
| **Bloque** | [05](05-streaming-asr.md) (douce : le 05 démarre avec le modèle par défaut) |
| **Branche** | `bench/asr` |

## Objectif

Choisir le modèle de transcription **sur données**, sur le genre de stream
qu'on regarde vraiment et sur ce matériel (V100, fp16, pas de bf16), puis
consigner le choix dans [ADR-002](../decisions.md#adr-002--asr--whisper-spécialisé-japonais-via-faster-whisper-choisi-au-banc).

## 1. Candidats

| Modèle | Pile | Pourquoi il est là |
|---|---|---|
| `kotoba-tech/kotoba-whisper-v2.0-faster` | faster-whisper | Défaut pressenti : distillé large-v3, spécialisé japonais |
| `openai/whisper-large-v3` | faster-whisper | Référence de qualité |
| `large-v3-turbo` | faster-whisper | Rapide, généraliste |
| `litagin/anime-whisper` | transformers (ou conversion CT2) | Si les streams suivis sont des VTubers / voix jouées |
| `reazon-research/reazonspeech-nemo-v2` ou `-k2-v2` | NeMo / sherpa-onnx | Entraîné sur la TV japonaise, conçu pour le flux |

Vérifier au démarrage du lot que chaque identifiant existe toujours sur
Hugging Face et noter la révision exacte (hash) testée.

## 2. Données

- **Jeu « streams »** : 30 à 60 min d'extraits de 3 à 5 streams représentatifs
  (bavardage, jeu avec musique, plusieurs voix, chant si pertinent),
  enregistrés par l'extension de spike. Transcrits à la main, ou en partant de
  la sortie de large-v3 **corrigée à la main** (le corriger, pas le recopier,
  sinon le banc favorise large-v3). Rangé dans `benchmarks/data/` (hors git).
- **Jeu public** : un sous-ensemble de Common Voice ja (CC0) ou JSUT, pour
  un chiffre reproductible par n'importe qui. Il peut être versionné.

## 3. Mesures

Pour chaque modèle × chaque réglage (`beam_size` 1 et 5, `compute_type`
float16 et int8_float16) :

| Mesure | Comment |
|---|---|
| **CER** | Après normalisation (NFKC, ponctuation retirée, chiffres unifiés) ; le WER n'a pas de sens en japonais |
| Taux d'hallucination | Sortie non vide sur des segments sans parole (musique seule) |
| Latence de décodage | p50 / p95 sur des segments de 2, 5 et 10 s, **GPU chaud**, un décodage à la fois |
| VRAM de pointe | `torch.cuda.max_memory_allocated` ou `nvidia-smi` |
| Stabilité des `partial` | Nombre de caractères réécrits entre deux décodages successifs d'un segment qui grandit (mesure le scintillement à l'écran) |

## 4. Règle de décision

Contrainte dure : **latence p95 ≤ 500 ms sur un segment de 5 s** et **VRAM ≤
4 Gio**. Parmi les modèles qui la respectent, le **CER le plus bas sur le jeu
« streams »** l'emporte. Un écart de CER < 1 point départage sur la latence.

## Livrables

- `benchmarks/asr/run.py` + `just bench-asr`, idempotent, qui écrit
  `benchmarks/asr/results-<date>.md` (tableau) et `.json`.
- ADR-002 mis à jour avec le chiffre qui a décidé.

## Critères d'acceptation

- [ ] Au moins les trois modèles faster-whisper mesurés sur les deux jeux.
- [ ] Résultats versionnés, données brutes non versionnées.
- [ ] Le modèle retenu et son `compute_type` sont les défauts de `config.py`.
