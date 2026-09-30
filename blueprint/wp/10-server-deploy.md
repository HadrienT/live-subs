# WP 10 — Déploiement serveur

| | |
|---|---|
| **Dépend de** | [03](03-ingest-vad.md), [06](06-translation.md) |
| **Bloque** | [13](13-ahead-of-live.md) |
| **Branche** | `feat/deploy` |

## Objectif

Le serveur démarre avec la machine, écoute sur le LAN, a les modèles déjà en
cache, et se laisse diagnostiquer en une commande.

## 1. Image

- Base `nvidia/cuda:12.4.x-cudnn-runtime` (CTranslate2 veut cuDNN 9) + `uv`.
- Modèles **non** inclus dans l'image : volume `livesubs_models` monté sur
  `HF_HOME`, préchargé par `just fetch-models`. Le premier démarrage ne doit
  pas télécharger 1,5 Go en silence.
- Utilisateur non-root.

## 2. Compose

```yaml
services:
  livesubs:
    build: ./server
    ports: ["192.168.1.200:8765:8765"]          # LAN seulement, pas 0.0.0.0
    environment:
      LIVESUBS_ASR_DEVICE: cuda:0
      LIVESUBS_LLM_BASE_URL: http://172.17.0.1:8001/v1   # llama-bridge d'AgenticEnv
    deploy:
      resources:
        reservations:
          devices: [{driver: nvidia, device_ids: ["0"], capabilities: [gpu]}]
    extra_hosts: ["host.docker.internal:host-gateway"]
    restart: unless-stopped
```

Vérifier que le socket `llama-bridge` (écoute sur la passerelle `docker0`) est
joignable depuis le réseau du compose. Sinon, rattacher le service au réseau
`bridge` par défaut. **Ne pas** créer ni supprimer de réseau Docker sans
relire l'avertissement de `CLAUDE.md`.

## 3. Démarrage

`restart: unless-stopped` suffit si le démon Docker démarre au boot.
**Ne pas** reproduire l'erreur de `llama-server` : le conteneur a besoin du
driver NVIDIA ; au démarrage, vérifier que CUDA est bien là
(`ctranslate2.get_cuda_device_count() > 0`) et **refuser de démarrer sur CPU**
sans `LIVESUBS_ALLOW_CPU=1`, plutôt que de tourner lentement sans le dire.

## 4. Santé

`GET /health` → `{asr: {model, device, warm}, llm: {reachable, model,
on_gpu?}, sessions, gpu_mem_mib}`. `just status` l'interroge et affiche aussi
`nvidia-smi` pour les deux GPU.

## 5. Pare-feu

Vérifier que le port 8765 est ouvert depuis le LAN et **pas** depuis
l'extérieur (le routeur ne le redirige pas). Aucun passage par le tunnel
Cloudflare.

## Critères d'acceptation

- [ ] Après un redémarrage du serveur, `curl http://192.168.1.200:8765/health`
      depuis le PC répond `warm: true` sans intervention.
- [ ] Sans GPU visible, le service refuse de démarrer, avec un message clair.
- [ ] Les autres stacks du serveur (quant-modeling, quant-platform,
      data-ingest, AgenticEnv) n'ont pas été touchées.
