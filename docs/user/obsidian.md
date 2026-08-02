# Obsidian plugin

Thin TypeScript client (`plugin-obsidian/`) that syncs a vault against the daemon under contract v1 — bijective within the contract, never a silent guess outside it (`docs/vision.md` §"Sync doctrine", `docs/mvp-spec.md` §4.7).

**Status: code-complete and CI-green (`plugin-build`).** It's genuinely runtime-exercised, not just code-reviewed: live sessions against a real Obsidian install already found and fixed two real bugs that only showed up under real use — every plugin fetch initially failing CORS preflight, and the daemon's watcher mistakenly tracking Obsidian's own `.obsidian/workspace.json` as if it were contract content (both in `../mvp-debug-plan.md`, D4 and D7). Two things are still open, tracked separately: running the exhaustive `TESTPLAN.md` script below end-to-end against a real vault and signing off (M6's own remaining runtime DoD, [`../agents/task-status.md`](../agents/task-status.md)), and marking real personal-note spans as tracked claims ([M11's T11.2](../agents/task-status.md)).

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
- Cut/copy of a managed line keeps its `^tm-<id8>` anchor — this is Obsidian's own native plain-text behavior, not special handling the plugin adds; confirming it holds up in practice is exactly what the manual test plan below is for.

## Contract you write in

A file only participates once its YAML front matter contains `tm: 1` — add that by hand the first time (`docs/mvp-spec.md` §4.7); anchors and commands in a file without it are silently ignored, not an error. Once managed, a file is a lossless container: only anchored lines (`^tm-<id8>`, task checkboxes, `^tm-new` requests, embeds/refs) are parsed; everything else round-trips verbatim. Grammar is fully specified in [`../mvp-spec.md`](../mvp-spec.md) §4.7 — don't relearn it here.

## Full manual test script

`plugin-obsidian/TESTPLAN.md` is the authoritative, exhaustive verification script (settings persistence, status bar, minting, cut/copy anchor behavior). Run it against a real vault + daemon before relying on the plugin.
