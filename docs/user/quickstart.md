# Quickstart

No packaged installer exists yet (Phase 4+ per `docs/vision.md` §7.11); run from a source checkout via [`uv`](https://docs.astral.sh/uv/).

## 1. Install

```bash
git clone <this-repo> akasha && cd akasha
uv sync
```

## 2. Mint your first token

`POST /v1/tokens` is human-only and itself requires an existing bearer token, so a brand-new database has no way to authenticate the call that would mint its first token. `akasha init` closes that gap: it talks to the store directly (the same "not a pure HTTP client" exception `daemon` already has, see `cli.md`) rather than a new HTTP endpoint, and mints exactly one `human`-class token.

If you have Git Bash/WSL, `scripts/dogfood/init.sh <name>` does this step plus starting the daemon and registering a sync root, all in one command — see `docs/dogfood/README.md`.

```bash
uv run akasha init --name me
```

Save the printed `<token_id>.<secret>` string — it is shown once and not recoverable. Running `akasha init` again once a token already exists is a clean, documented no-op-with-error (exit code 4) — it will not overwrite or add a second token; use `token create` (below, once the daemon is running) for additional tokens.

```bash
export AKASHA_TOKEN='<paste the bearer value here>'
```

## 3. Start the daemon

```bash
uv run akasha daemon
```

Serves `http://127.0.0.1:7433` (config/db at `~/.config/tm-daemon/` on Linux/macOS, `%APPDATA%\tm-daemon\` on Windows — see [`../mvp-spec.md`](../mvp-spec.md) §3). Leave this running; open a second terminal for the next steps. To keep it running across reboots, see [`ops/autostart.md`](ops/autostart.md).

## 4. Create and read a node

```bash
uv run akasha --token "$AKASHA_TOKEN" new claim "caffeine impairs sleep"
uv run akasha --token "$AKASHA_TOKEN" search caffeine
uv run akasha --token "$AKASHA_TOKEN" get <id-from-above>
```

## Next steps

- Full verb/endpoint reference: [`cli.md`](cli.md), [`api.md`](api.md)
- Browser UI at `http://127.0.0.1:7433/`: [`web-ui.md`](web-ui.md)
- Sync an Obsidian vault: [`obsidian.md`](obsidian.md)

**Known friction in the steps above (tracked, not forgotten):** there's no CLI verb to register
a sync root. (Minting the first token used to require a raw Python one-liner — fixed by
`akasha init`, task T12.1. The web UI used to need a devtools console command to authenticate —
that's fixed too: it now has an in-page token form, see [`web-ui.md`](web-ui.md).) A full audit
and proposed fix plan for what's left is in [`../onboarding-ux-report.md`](../onboarding-ux-report.md).
