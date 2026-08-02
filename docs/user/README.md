# Using akasha

akasha runs as one local daemon; every surface below is a client of its `/v1` API (`docs/mvp-spec.md` §1). Start here:

1. [`quickstart.md`](quickstart.md) — run the daemon, get a token, create your first node.
2. Pick a surface:
   - [`cli.md`](cli.md) — `akasha` command-line client
   - [`api.md`](api.md) — the localhost HTTP API directly
   - [`web-ui.md`](web-ui.md) — the daemon-served browser UI
   - [`obsidian.md`](obsidian.md) — the Obsidian plugin (bijective vault sync)
3. [`dogfood-windows.md`](dogfood-windows.md) — run a temporary, no-install Windows stack (daemon + CLI + Obsidian plugin) for day-to-day dogfooding.
4. [`ops/autostart.md`](ops/autostart.md) — keep the daemon running across reboots.

**Fastest path to a fresh environment:** if you have Git Bash/WSL, `scripts/dogfood/init.sh <name>` does [`quickstart.md`](quickstart.md)'s steps 2–3 (mint a token, start the daemon) plus registering a sync root, all in one command against a scratch DB — see `docs/dogfood/README.md`. For a persistent, autostarting daemon on Windows instead of a foreground one, see `docs/dogfood/windows-service.md` (note its own scripts are wired to a disposable scratch instance, not a drop-in for your real vault — see [`ops/autostart.md`](ops/autostart.md)). Both are scripts under `scripts/` — see `scripts/README.md` for the full inventory.

**Not covered here:** schema, endpoint/CLI signatures, and grammar are specified once in [`../mvp-spec.md`](../mvp-spec.md) §4 — these pages link into it rather than repeating it. If a page here disagrees with the spec, the spec wins.

**Project maturity:** M0–M10 are done or code-complete against a live daemon (see [`../agents/task-status.md`](../agents/task-status.md)); all nine MVP acceptance stories are green, including real Windows dev-host runs and a green hosted `windows-latest`/`ubuntu-latest` CI run (see [`../acceptance.md`](../acceptance.md)) — the one-month Phase 2 dogfood gate this repo's `docs/dogfood-plan.md` is the operating manual for is now underway. M11 (dogfood smoke test) is in progress — its remaining step is the human-only "mark real spans and use it" leg. There is no packaged installer yet — every workflow below runs from a source checkout via `uv`. A packaged Windows executable (installer, tray icon, no manual token/config steps) is planned — see [`../onboarding-ux-report.md`](../onboarding-ux-report.md) for the audit and proposed task breakdown (M12).
