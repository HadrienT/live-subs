# Interdépendances

Le graphe visuel est dans [`README.md §6`](README.md#6-graphe-de-dépendances).
Ce fichier dit **de quoi** chaque dépendance est faite, pour savoir laquelle
bloque vraiment et laquelle peut être court-circuitée par un contrat écrit à
l'avance.

`dure` = impossible de commencer avant. `douce` = on peut commencer contre un
contrat figé (message du protocole, interface Python, faux modèle) et brancher
le vrai plus tard. `externe` = hors de ce dépôt.

| WP | Dépend de | Nature | Ce qui transite exactement |
|---|---|---|---|
| 00 Fondations | — | — | — |
| 01 Protocole | 00 | dure | Paquet `livesubs`, projet TS, commande de test commune |
| 02 Spike capture | 00 | douce | Le spike peut vivre dans `extension/spike/` avant l'outillage complet |
| 03 Ingest & VAD | 01 | dure | Format des trames audio, messages `hello` / `ready` / `error` |
| 04 Banc ASR | 00 | dure | `uv`, marqueur pytest `gpu`, `benchmarks/` |
| 04 Banc ASR | *extraits de streams* | **externe** | 30–60 min d'audio réel annoté (voir lot 04 §2) |
| 05 ASR en flux | 03 | dure | Segments VAD (`SpeechSegment`), horloge en échantillons |
| 05 ASR en flux | 04 | douce | Le choix du modèle. Le lot 05 démarre avec `kotoba-whisper-v2.0`, l'interface `Transcriber` isole le changement |
| 06 Traduction | 05 | douce | Messages `final` ; l'interface `Translator` se teste sur des segments figés |
| 06 Traduction | *AgenticEnv* | **externe** | `llama-server` **sur GPU**, joignable (`127.0.0.1:8000` ou `172.17.0.1:8001`) |
| 06 Traduction | *AgenticEnv* | **externe** (douce) | Profils `code` / `translate` et leur bascule ; en attendant, le lot 06 teste en chargeant le candidat à la main |
| 07 Ext. capture | 02 | dure | La méthode de capture validée, la réponse sur AudioWorklet vs ScriptProcessor |
| 07 Ext. capture | 01 | dure | `protocol.ts`, format des trames |
| 08 Ext. overlay | 07 | dure | Le port content ↔ background, les messages reçus |
| 09 Synchro & latence | 05, 08 | dure | Horodatage des trames, bornes des segments, affichage |
| 10 Déploiement serveur | 03, 06 | dure | Le serveur complet et ses variables d'environnement |
| 11 Packaging ext. | 08 | dure | Une extension fonctionnelle à signer |
| 12 Qualité & E2E | 03, 05, 06 | douce | Les tests unitaires naissent avec chaque lot ; le 12 ajoute le rejeu doré et la CI |
| 13 En avance | 09, 10 | dure | Mesures de latence, serveur déployé |
| 14 Historique | 08 | dure | L'overlay et le stockage de l'extension |

## Ce qui peut avancer en parallèle

- **02 (spike)** et **04 (banc ASR)** n'ont besoin que de l'outillage : ce sont
  les deux plus gros risques du projet, à lancer en premier.
- **03 → 05** côté serveur et **07 → 08** côté extension ne se croisent qu'au
  protocole (01). Avec `just replay` (lot 03), le serveur se développe sans
  navigateur ; avec un faux serveur (lot 07), l'extension se développe sans GPU.
- **06** peut commencer sur un llama-server encore sur CPU pour le code et les
  prompts, mais **aucune mesure de latence de traduction n'a de valeur** avant
  que le problème côté AgenticEnv soit réglé.
