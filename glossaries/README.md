# Glossaries

One file per YouTube channel: `<channel_id>.toml` (the `UC…` id sent in `hello`).
Japanese terms prime the ASR prompt; the pairs force the English spelling in the
translation. Files are re-read when they change, no restart needed.

```toml
name = "Channel display name"   # optional, given to the translator as context
[terms]
"兎田ぺこら" = "Usada Pekora"
"ぺこら" = "Pekora"
"野うさぎ" = "Nousagi"          # fan name
```
