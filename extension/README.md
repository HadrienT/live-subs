# live-subs — Firefox extension

Near-live Japanese + English subtitles for YouTube, transcribed and translated by
a server on your own LAN (see the [repository README](../README.md)).

## Build (reproducible, for reviewers)

Requirements: Node.js 20+, npm.

```sh
cd extension
npm ci
node esbuild.mjs --production   # bundles src/ into dist/ (one file per context)
```

`dist/` is the submitted add-on: `content.js`, `background.js`, `worklet.js`,
`popup.js`, `options.js` are esbuild bundles of `src/content/`, `src/background/`,
`src/worklet/`, `src/popup/`, `src/options/`; `static/` and `manifest.json` are
copied as is (the version comes from `package.json`). No remote code, no
third-party runtime dependency.

Tests: `npx vitest run`. Lint: `npx eslint . && npx tsc --noEmit && npx web-ext lint -s dist`.
