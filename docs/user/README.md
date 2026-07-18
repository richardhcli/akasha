# Using akasha

akasha runs as one local daemon; every surface below is a client of its `/v1` API (`docs/mvp-spec.md` §1). Start here:

1. [`quickstart.md`](quickstart.md) — run the daemon, get a token, create your first node.
2. Pick a surface:
   - [`cli.md`](cli.md) — `akasha` command-line client
   - [`api.md`](api.md) — the localhost HTTP API directly
   - [`web-ui.md`](web-ui.md) — the daemon-served browser UI
   - [`obsidian.md`](obsidian.md) — the Obsidian plugin (bijective vault sync)
3. [`ops/autostart.md`](ops/autostart.md) — keep the daemon running across reboots.

**Not covered here:** schema, endpoint/CLI signatures, and grammar are specified once in [`../mvp-spec.md`](../mvp-spec.md) §4 — these pages link into it rather than repeating it. If a page here disagrees with the spec, the spec wins.

**Project maturity:** M0–M8 are done or code-complete against a live daemon (see [`../agents/task-status.md`](../agents/task-status.md)); M9 (hardening: Windows battery, soak) and M10 (dogfood tooling) are still open. There is no packaged installer yet — every workflow below runs from a source checkout via `uv`.
