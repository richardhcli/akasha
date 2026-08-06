# CLI

`akasha <verb>` is a pure HTTP client of the daemon's `/v1` API — it never touches SQLite directly (`docs/mvp-spec.md` §4.12, task T4.8). Run `uv run akasha daemon` first (see [`quickstart.md`](quickstart.md)). Two verbs are deliberate exceptions to the "pure HTTP client" rule, documented in `cli/main.py`'s own module docstring: `daemon` *is* the server process (it doesn't speak HTTP to one), and `init` (task T12.1) talks to the store directly to bootstrap the very first human token on a fresh DB, since `POST /v1/tokens` requires a token that doesn't exist yet.

**Full verb list, flags, and exit codes are specified once in [`../mvp-spec.md`](../mvp-spec.md) §4.12 — this page does not repeat that table.** Authoritative in-tool reference:

```bash
uv run akasha --help
uv run akasha <verb> --help
```

## Common invocations

```bash
# create
uv run akasha --token "$AKASHA_TOKEN" new claim "some claim text" --facet name=span

# read
uv run akasha --token "$AKASHA_TOKEN" get <id>
uv run akasha --token "$AKASHA_TOKEN" get <id> --as-of 2026-01-01T00:00:00Z

# edit (change class defaults to patch — the least-invalidating class)
uv run akasha --token "$AKASHA_TOKEN" set <id> --body "revised text" --class minor

# close / re-open a task node (build-plan T13.4, spec §4.12/§4.11 task_state)
uv run akasha --token "$AKASHA_TOKEN" set <id> --task-state done
uv run akasha --token "$AKASHA_TOKEN" set <id> --task-state open

# search
uv run akasha --token "$AKASHA_TOKEN" search "some query"

# graph reads (build-plan T14.1) -- plain output is one ASCII line per
# edge/commit; add --json for the machine-readable cli/v1 envelope
uv run akasha --token "$AKASHA_TOKEN" neighborhood <id>
uv run akasha --token "$AKASHA_TOKEN" neighborhood <id> --hops 2
uv run akasha --token "$AKASHA_TOKEN" history <id>

# review queue
uv run akasha --token "$AKASHA_TOKEN" review list
uv run akasha --token "$AKASHA_TOKEN" review resolve <review-id> still_holds

# tokens (human-only)
uv run akasha --token "$AKASHA_TOKEN" token create <name> --class agent

# sync (human-only)
uv run akasha --token "$AKASHA_TOKEN" sync add /path/to/vault --name my-vault
```

## Flags worth knowing

- `--json` — machine-readable `cli/v1` envelope (`{"schema","ok","data"|"error"}`), additive-only across versions.
- `--dry-run` — mutating verbs print the would-be HTTP request and exit 0 without calling the server.
- `--base-url` — point at a non-default daemon (defaults to `http://127.0.0.1:7433`).
- `--token` — bearer token; can also be set via the `AKASHA_TOKEN` env var pattern shown in the quickstart (export it yourself, the CLI itself only reads `--token`).
- `set --task-state open|done` — only sent when explicitly passed; an omitted flag leaves an existing task's `task_state` unchanged (`docs/mvp-spec.md` §4.12, task T13.4).

Agent-class tokens do not mutate directly: every write becomes a review-queue proposal (`docs/mvp-spec.md` §4.11). Bootstrapping the first human token is not yet CLI-supported — see [`quickstart.md`](quickstart.md) step 2.
