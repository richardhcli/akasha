# Quickstart

No packaged installer exists yet (Phase 4+ per `docs/vision.md` §7.11); run from a source checkout via [`uv`](https://docs.astral.sh/uv/).

## 1. Install

```bash
git clone <this-repo> akasha && cd akasha
uv sync
```

## 2. Mint your first token

`POST /v1/tokens` is human-only and itself requires an existing bearer token, so there is currently no bootstrap endpoint or CLI verb for the very first token (tracked as an open gap — see `docs/spec-questions.md` T11.1). Until one lands, mint it directly through the store, the same way `scripts/dev/seed_and_run.py` does:

If you have Git Bash/WSL, `scripts/dogfood/init.sh <name>` does this step plus starting the daemon and registering a sync root, all in one command — see `docs/dogfood/README.md`. The manual steps below are the same underlying calls, useful on plain PowerShell or when you want a single ad hoc token instead of a whole scratch instance.

```bash
uv run python -c "
from akasha.api import auth
from akasha.config import default_db_path
from akasha.kernel import store

conn = store.connect(default_db_path())
store.run_migrations(conn)
raw = auth.mint_secret()
token = store.create_token(conn, name='me', token_class='human', secret_hash=auth.hash_secret(raw))
print(auth.format_bearer_token(token['id'], raw))
"
```

Save the printed `<token_id>.<secret>` string — it is shown once and not recoverable.

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

**Known friction in the steps above (tracked, not forgotten):** minting the first token
requires a raw Python one-liner, and there's no CLI verb to register a sync root. (The web UI
used to need a devtools console command to authenticate — that's fixed: it now has an in-page
token form, see [`web-ui.md`](web-ui.md).) A full audit and proposed fix plan for what's left is in
[`../onboarding-ux-report.md`](../onboarding-ux-report.md).
