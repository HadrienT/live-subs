# WP 06 — Traduction JA → EN

| | |
|---|---|
| **Dépend de** | [05](05-streaming-asr.md) (douce) ; **externe** : `llama-server` d'AgenticEnv sur GPU |
| **Bloque** | [10](10-server-deploy.md), [12](12-quality-e2e.md) |
| **Branche** | `feat/translation` |

## Préalable externe — llama-server tourne sur CPU

Au 30/09/2026, `llama-server.service` occupe 32,7 Go de RAM et **aucune VRAM**
(`nvidia-smi` : 310 Mio sur GPU 0 pour un autre processus, 4 Mio sur GPU 1),
alors que `n_gpu_layers: all`. Probablement un démarrage au boot où le
backend CUDA ne s'est pas initialisé (le service démarre `After=network-online`,
pas après le chargement du driver NVIDIA), mais c'est à diagnostiquer dans
AgenticEnv. **Issue à ouvrir** : `gh issue create --repo HadrienT/AgenticEnv
--label cross-repo`. Tant que ce n'est pas réglé, on écrit le code et les
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

## 4. Banc de traduction

`benchmarks/mt/` : 200 segments `final` réels issus du lot 05, avec leur
contexte. Comparer :

| Modèle | Remarque |
|---|---|
| `Qwen3-Coder-30B-A3B-Instruct` (Q4_K_M) | Celui qui est servi aujourd'hui |
| `Qwen3-30B-A3B-Instruct-2507` (Q4_K_M) | Même architecture, généraliste : candidat naturel |
| `pfnet/plamo-2-translate` (GGUF) | Traducteur dédié JA↔EN |

Mesures : latence du premier jeton et latence totale (p50/p95), et une
évaluation de qualité **à l'aveugle** par le mainteneur sur 50 segments (A/B
anonymisé, script dans `benchmarks/mt/`). Si un modèle l'emporte nettement,
issue `cross-repo` sur AgenticEnv : changer le modèle servi, ou en servir un
second, reste une décision d'AgenticEnv ([ADR-003](../decisions.md#adr-003--traduction-par-le-llama-server-dagenticenv)).

## Critères d'acceptation

- [ ] `just replay` affiche la ligne anglaise qui se remplit en flux.
- [ ] Glossaire chargé par `channel_id` ; un nom du glossaire apparaît dans la
      traduction avec sa graphie imposée (test avec `FakeTranslator` + test
      `gpu` réel).
- [ ] Retard, fusion et délai maximal couverts par des tests.
- [ ] Résultats du banc versionnés dans `benchmarks/mt/results-<date>.md`.
