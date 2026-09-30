# WP 06 — Traduction JA → EN

| | |
|---|---|
| **Dépend de** | [05](05-streaming-asr.md) (douce) ; **externe** : `llama-server` d'AgenticEnv sur GPU, et les profils de modèle `code` / `translate` ([ADR-003](../decisions.md#adr-003--traduction-par-le-llama-server-dagenticenv-avec-des-profils-de-modèle)) |
| **Bloque** | [10](10-server-deploy.md), [12](12-quality-e2e.md) |
| **Branche** | `feat/translation` |

## Préalable externe — llama-server tourne sur CPU

Au 30/09/2026, `llama-server.service` occupe 32,7 Go de RAM et **aucune VRAM**
(`nvidia-smi` : 310 Mio sur GPU 0 pour un autre processus, 4 Mio sur GPU 1),
alors que `n_gpu_layers: all`. Probablement un démarrage au boot où le
backend CUDA ne s'est pas initialisé (le service démarre `After=network-online`,
pas après le chargement du driver NVIDIA), mais c'est à diagnostiquer dans
AgenticEnv : [AgenticEnv#15](https://github.com/HadrienT/AgenticEnv/issues/15).
Les profils `code` / `translate` y sont suivis par
[AgenticEnv#16](https://github.com/HadrienT/AgenticEnv/issues/16). Tant que ce n'est pas réglé, on écrit le code et les
prompts, mais on ne mesure aucune latence.

## 1. Interface

```python
class Translator(Protocol):
    name: str
    def translate(self, seg: FinalSegment, ctx: TranslationContext) -> AsyncIterator[str]: ...
```

`LlamaServerTranslator` (client `httpx` en flux sur `/v1/chat/completions`) et
`FakeTranslator`. Pas de SDK `openai` : une requête SSE tient en 40 lignes et
évite une dépendance.

## 2. Prompt

- **Système** : traducteur de sous-titres de live stream japonais → anglais ;
  sortie = la traduction seule, une ligne, registre oral, pas de notes ; garder
  les noms propres du glossaire tels quels ; si le japonais est manifestement
  une erreur de transcription, traduire au mieux sans le signaler.
- **Contexte glissant** : les 6 derniers couples (JA, EN) de la session, en
  tours précédents. Le japonais omet souvent le sujet ; le contexte le rend.
- **Glossaire par chaîne** : `glossaries/<channel_id>.toml` (noms, surnoms,
  termes de jeu, avec leur graphie anglaise). Le `channel_id` vient du `hello`.
- Qwen3 : désactiver le raisonnement (`/no_think` ou
  `chat_template_kwargs: {enable_thinking: false}` selon le modèle servi),
  `temperature` 0,2, `max_tokens` 120.

## 3. Comportement en flux

- Les jetons partent en `translation_delta` dès qu'ils arrivent ; `translation`
  clôt le segment.
- **Une traduction à la fois par session, dans l'ordre des segments.** Si le
  retard dépasse 3 segments (llama-server occupé par l'agent de code), on
  **fusionne** les segments en attente en une seule requête plutôt que de
  prendre du retard indéfiniment.
- Délai maximal 8 s par segment → `error{code:"mt_timeout", fatal:false}`, et
  la ligne anglaise reste vide pour ce segment.
- On ne traduit **jamais** les `partial` en v1 (coût GPU × 5 pour un texte qui
  change). À reconsidérer au lot 09 si la latence le justifie.

## 4. Banc de traduction — il désigne le modèle du profil `translate`

Le modèle servi pendant qu'on regarde un stream est **le meilleur traducteur**
qui tient sur les V100, pas le modèle de code
([ADR-003](../decisions.md#adr-003--traduction-par-le-llama-server-dagenticenv-avec-des-profils-de-modèle)).
Ce banc le choisit.

`benchmarks/mt/` : 200 segments `final` réels issus du lot 05, avec leur
contexte. Candidats (vérifier au démarrage que chacun existe en GGUF et se
charge dans la version de llama.cpp installée) :

| Modèle | Remarque |
|---|---|
| `pfnet/plamo-2-translate` | Traducteur dédié JA↔EN (PFN). Architecture hybride : vérifier le support llama.cpp |
| `Qwen3-30B-A3B-Instruct-2507` (Q4_K_M) | MoE ~3B actifs, très rapide, bon en japonais |
| Gemma 3 27B (Q4_K_M) | Réputé solide en traduction, dense donc plus lent |
| `Qwen3-Coder-30B-A3B-Instruct` | **Témoin** seulement : le modèle de code actuel |

Mesures : latence du premier jeton et latence totale (p50/p95) **sur GPU**,
VRAM avec un contexte de 8k, et une évaluation de qualité **à l'aveugle** par
le mainteneur sur 50 segments (A/B anonymisé, script dans `benchmarks/mt/`).
Le gagnant est enregistré comme profil `translate` dans
`~/AgenticEnv/configs/models.yaml`, par une PR sur AgenticEnv.

## 5. Modèle inactif

Le traducteur envoie `model: $LIVESUBS_LLM_MODEL`. Si `/v1/models` annonce un
autre modèle (le profil `code` est chargé) et qu'AgenticEnv n'échange pas les
modèles à la demande :
`error{code:"mt_model_inactive", fatal:false}`, la ligne anglaise est
remplacée par un bandeau « LLM en mode code — passer en mode traduction », et
le popup propose la bascule si AgenticEnv l'expose. On ne traduit **jamais**
avec le modèle de code sans le dire.

## Critères d'acceptation

- [ ] `just replay` affiche la ligne anglaise qui se remplit en flux.
- [ ] Glossaire chargé par `channel_id` ; un nom du glossaire apparaît dans la
      traduction avec sa graphie imposée (test avec `FakeTranslator` + test
      `gpu` réel).
- [ ] Retard, fusion et délai maximal couverts par des tests.
- [ ] Résultats du banc versionnés dans `benchmarks/mt/results-<date>.md`, et
      profil `translate` enregistré dans AgenticEnv.
- [ ] Avec le profil `code` chargé, le bandeau « mode code » s'affiche au lieu
      d'une traduction.
