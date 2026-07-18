# CLI

`akasha <verb>` is a pure HTTP client of the daemon's `/v1` API — it never touches SQLite directly (`docs/mvp-spec.md` §4.12, task T4.8). Run `uv run akasha daemon` first (see [`quickstart.md`](quickstart.md)).

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

# search
uv run akasha --token "$AKASHA_TOKEN" search "some query"

# review queue
uv run akasha --token "$AKASHA_TOKEN" review list
uv run akasha --token "$AKASHA_TOKEN" review resolve <review-id> still_holds

# tokens (human-only)
uv run akasha --token "$AKASHA_TOKEN" token create <name> --class agent
```

## Flags worth knowing

- `--json` — machine-readable `cli/v1` envelope (`{"schema","ok","data"|"error"}`), additive-only across versions.
- `--dry-run` — mutating verbs print the would-be HTTP request and exit 0 without calling the server.
- `--base-url` — point at a non-default daemon (defaults to `http://127.0.0.1:7433`).
- `--token` — bearer token; can also be set via the `AKASHA_TOKEN` env var pattern shown in the quickstart (export it yourself, the CLI itself only reads `--token`).

Agent-class tokens do not mutate directly: every write becomes a review-queue proposal (`docs/mvp-spec.md` §4.11). Bootstrapping the first human token is not yet CLI-supported — see [`quickstart.md`](quickstart.md) step 2.
