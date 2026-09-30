# WP 05 — Transcription en flux

| | |
|---|---|
| **Dépend de** | [03](03-ingest-vad.md) (dure), [04](04-asr-benchmark.md) (douce) |
| **Bloque** | [06](06-translation.md), [09](09-sync-latency.md), [12](12-quality-e2e.md) |
| **Branche** | `feat/streaming-asr` |

## Objectif

Transformer les segments VAD en messages `partial` et `final` : un japonais
juste, qui s'affiche pendant qu'on parle et qui ne scintille pas.

## 1. Interface

```python
class Transcriber(Protocol):
    name: str
    def transcribe(self, audio: NDArray[np.float32], *, prompt: str | None) -> AsrResult: ...

@dataclass(frozen=True)
class AsrResult:
    text: str
    words: list[Word]          # horodatage par mot si le modèle le donne
    no_speech_prob: float
    avg_logprob: float
    compression_ratio: float
```

Implémentations : `FasterWhisperTranscriber` (le défaut) et `FakeTranscriber`
(renvoie un texte scripté, pour les tests). Un autre moteur (ReazonSpeech…) ne
s'ajoute que si le lot 04 le retient.

## 2. Ordonnancement GPU

Un **seul thread d'inférence**, alimenté par une file à priorités :

1. `final` d'un segment qui vient de se fermer (priorité haute) ;
2. `partial` d'un segment ouvert (priorité basse, **coalescés** : si un nouveau
   `partial` du même segment arrive avant que l'ancien soit servi, l'ancien est
   jeté).

Conséquence : même si le GPU est pris ailleurs (llama-server, quant-modeling),
on ne perd jamais un `final`, et on n'empile jamais les `partial`.

## 3. Partials sans scintillement

Re-décoder la fenêtre ouverte toutes les ~1 s donne des textes qui changent en
queue. On applique l'**accord local** (LocalAgreement-2, cf. *whisper_streaming*,
Macháček et al., 2023) : le préfixe commun aux deux derniers décodages est
« stable » et s'affiche en blanc ; le reste s'affiche en gris. Le `partial`
porte donc `ja_stable` et `ja_unstable` (lot 01 §3).

## 4. Garde-fous contre les hallucinations

Whisper invente du texte sur la musique et le silence. Un segment est **jeté**
(ni `partial` ni `final`) si :

- `no_speech_prob > 0,6` **et** `avg_logprob < -1,0` ;
- `compression_ratio > 2,4` (répétitions en boucle) ;
- le texte est dans une liste noire de formules typiques des sous-titres
  d'entraînement (`ご視聴ありがとうございました`, `チャンネル登録…`, etc.), à
  compléter à partir du banc.

## 5. Contexte

`initial_prompt` = les ~50 derniers caractères finaux de la session + les
termes du glossaire de la chaîne (noms des personnes du stream, jeu en cours).
Ça aide beaucoup sur les noms propres, mais un contexte faux se propage :
désactivable dans la config, et vidé à chaque discontinuité.

## 6. Ponctuation

kotoba-whisper ponctue peu. Si le banc confirme que ça gêne la traduction, on
ajoute un ponctuateur léger (ou on laisse le LLM du lot 06 s'en charger ; c'est
gratuit, puisqu'il relit déjà le segment).

## Critères d'acceptation

- [ ] `just replay <extrait>` affiche des `partial` puis un `final` par phrase.
- [ ] Sur l'extrait du lot 02 : latence `final` p50 ≤ 1,5 s après la fin de
      parole, mesurée par `replay`.
- [ ] Musique seule pendant 60 s → zéro `final` émis.
- [ ] Tests avec `FakeTranscriber` : priorités, coalescence, filtre
      d'hallucination, accord local.
