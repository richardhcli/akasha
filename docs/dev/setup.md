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
```

`make` itself may not be installed in every sandbox; each target is a thin wrapper around the `uv run ...` command shown in [`../../Makefile`](../../Makefile) — run that directly if `make` is unavailable.

## Obsidian plugin toolchain

```bash
cd plugin-obsidian
npm ci
npm run build      # esbuild -> main.js
npx tsc --noEmit   # typecheck only
```

## Where things live

Repo layout, DB schema, and module responsibilities are specified once in [`../mvp-spec.md`](../mvp-spec.md) §2–§4 — not repeated here.
