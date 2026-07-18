# Obsidian plugin

Thin TypeScript client (`plugin-obsidian/`) that syncs a vault against the daemon under contract v1 — bijective within the contract, never a silent guess outside it (`docs/vision.md` §"Sync doctrine", `docs/mvp-spec.md` §4.7).

**Status: code-complete, runtime not yet verified against a live Obsidian install** (M6 in [`../agents/task-status.md`](../agents/task-status.md) — CI plugin-build hasn't run on a remote yet, and the manual test plan below hasn't been executed against a real vault).

## Install (manual, until packaged)

```bash
cd plugin-obsidian
npm ci
npm run build
```

Copy `manifest.json` + the built `main.js` into `<vault>/.obsidian/plugins/tm-hub/`, then enable the plugin in Obsidian's Community Plugins settings. In the plugin's settings tab, set the daemon URL (default `http://127.0.0.1:7433`) and your bearer token ([`quickstart.md`](quickstart.md) step 2).

## What it does

- Status bar shows sync state and open violation count.
- Command "Create node from selection" mints a node from selected text.
- Cut/copy preserve `^tm-<id8>` anchors so moves/copies stay in-contract.

## Contract you write in

A managed file is a lossless container: only anchored lines (`^tm-<id8>`, task checkboxes, `^tm-new` requests, embeds/refs) are parsed; everything else round-trips verbatim. Grammar is fully specified in [`../mvp-spec.md`](../mvp-spec.md) §4.7 — don't relearn it here.

## Full manual test script

`plugin-obsidian/TESTPLAN.md` is the authoritative, exhaustive verification script (settings persistence, status bar, minting, cut/copy anchor behavior). Run it against a real vault + daemon before relying on the plugin.
