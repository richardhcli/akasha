# Setup

Python 3.12+, managed by [`uv`](https://docs.astral.sh/uv/). No other runtime dependency for the Python daemon.

```bash
git clone <this-repo> akasha && cd akasha
uv sync   # installs the default "dev" dependency-group (pytest, hypothesis, ruff, pyright, playwright)
```

## Makefile targets

```bash
make check     # ruff check + pyright --strict + unit/property tests -- run before any task is DONE
make battery   # scripted vault edit-battery (tests/battery) -- run before closing any M5+ task
make run       # uv run python -m akasha.cli.main daemon
make dev-ui    # seed a throwaway graph + serve it, for manually driving the web UI (scripts/dev/seed_and_run.py)
make demo-transclusion        # real isolated daemon + 3 mirrored files: edit one, watch the others change (Ctrl-C to stop)
make demo-transclusion-check  # same, non-interactive: proves mirroring end to end from scratch, exit 0/1
```

`make` itself may not be installed in every sandbox; each target is a thin wrapper around the `uv run ...` command shown in [`../../Makefile`](../../Makefile) — run that directly if `make` is unavailable.

## Running from a checkout

```bash
uv run akasha daemon      # foreground; Ctrl-C stops it (what `make run` does)
uv run akasha setup <dir> # or the two-command flow from docs/user/quickstart.md
uv build --wheel          # a wheel ships the migrations (tests/integration/test_wheel_install.py)
uv tool install .         # what an end user runs; needs no checkout afterwards
```

To keep a scratch daemon away from your real one, give it its own `config.toml` (`port`, `db_path`) and pass `--config` to `daemon`/`up`/`down`/`setup`, or point `HOME` (`APPDATA` on Windows) at a temp directory — the config, database, lock, `daemon.log` and `tm-token` all live in one directory.

## Obsidian plugin toolchain

```bash
cd plugin-obsidian
npm ci
npm run build      # esbuild -> main.js
npx tsc --noEmit   # typecheck only
```

## Where things live

Repo layout, DB schema, and module responsibilities are specified once in [`../mvp-spec.md`](../mvp-spec.md) §2–§4 — not repeated here.
