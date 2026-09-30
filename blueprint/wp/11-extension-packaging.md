# WP 11 — Packaging de l'extension

| | |
|---|---|
| **Dépend de** | [08](08-extension-overlay.md) |
| **Bloque** | — |
| **Branche** | `chore/ext-packaging` |

## Objectif

Une extension installée **pour de bon** dans le Firefox du PC, pas un module
temporaire à recharger après chaque redémarrage.

## Options

| Voie | Pour | Contre |
|---|---|---|
| **Signature AMO « non listée »** (`web-ext sign --channel=unlisted`) | Marche sur Firefox Release ; Mozilla signe sans publier sur le store ; mises à jour par `update_url` | Compte AMO + clés API ; revue automatique à chaque version |
| Firefox Developer Edition / Nightly avec `xpinstall.signatures.required=false` | Aucune démarche | Oblige à changer de Firefox sur le PC |
| `about:debugging` → module temporaire | Immédiat | Perdu à chaque redémarrage ; pour le développement seulement |

**Recommandé : signature non listée.** Les clés AMO vont dans `.env`
(`WEB_EXT_API_KEY`, `WEB_EXT_API_SECRET`), jamais dans le dépôt.

## Contenu

- `just ext-sign` : build de production, `web-ext lint`, `web-ext sign`, `.xpi`
  dans `extension/dist-signed/`.
- Version de l'extension = version du `package.json`, et le lot 01 impose que
  `PROTOCOL_VERSION` soit compatible avec le serveur déployé.
- Optionnel : le serveur sert `updates.json` et le dernier `.xpi` sur le LAN
  (`/ext/updates.json`) pour que Firefox mette à jour tout seul.
- Permissions minimales dans le manifeste : `storage`, hôtes
  `*://*.youtube.com/*` et `ws://192.168.1.200/*` (à ajuster si l'URL du serveur
  est configurable : `optional_host_permissions` demandées à l'enregistrement
  de l'URL).

## Critères d'acceptation

- [ ] `.xpi` signé installé sur le Firefox Release du PC, présent après
      redémarrage.
- [ ] `web-ext lint` sans erreur ni avertissement non justifié.
- [ ] Procédure d'installation et de mise à jour décrite dans le `README.md`.
