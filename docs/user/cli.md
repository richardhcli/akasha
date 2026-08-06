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

# build the definition DAG (build-plan T14.2) -- a facet-bound justification
# edge; quote '*' so your shell doesn't glob it
uv run akasha --token "$AKASHA_TOKEN" edge add <src-id> <dst-id> depends_on --facet-binding '*'
uv run akasha --token "$AKASHA_TOKEN" edge add <src-id> <dst-id> depends_on --facet-binding <facet-id>

# or mint a brand-new facet on the target from a highlighted span and bind
# to it in one step (facets-from-spans capture, task T7.7)
uv run akasha --token "$AKASHA_TOKEN" edge add <src-id> <dst-id> depends_on --facet-span "the highlighted text"

# composes/redirects_to are the only edge types that allow no facet binding
uv run akasha --token "$AKASHA_TOKEN" edge add <parent-id> <child-id> composes

# retract an edge (soft -- both endpoint nodes stay live)
uv run akasha --token "$AKASHA_TOKEN" edge rm <edge-id>

# vet a node -- the S4 human act (build-plan T14.3, human-only, never
# proposalized for an agent token)
uv run akasha --token "$AKASHA_TOKEN" vet <id>

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
- `edge add SRC DST TYPE` (build-plan T14.2) — `TYPE` is one of `composes|supports|contradicts|depends_on|derived_from|cites|redirects_to` (`docs/mvp-spec.md` §4.2). The five justification types (`supports|contradicts|depends_on|derived_from|cites`) **require** `--facet-binding ID` or `--facet-binding '*'`; omitting it on one of those types is rejected by the daemon itself (a `400`, surfaced verbatim, never re-implemented client-side) — `composes`/`redirects_to` are the only two types that accept no binding. `--facet-span TEXT` mints a brand-new facet on the target node from that text and binds to it, overriding any `--facet-binding` also passed.
- `vet ID` (build-plan T14.3) — the one maturity stage the spec calls a *user act* (`docs/mvp-spec.md` §4.6, §4.11): sets the node's `vetted` flag, and it reads `S4` on the next `get`. **Human-only**, and unlike every other write, an agent-class token is *never* proposalized here — it gets the daemon's own `403` outright. Plain output reads `<id>: vetted by you (maturity: S4)`, never the word "true" (PRD R9: vetting is a claim about your own review, not a claim that something is objectively true); `--json` still returns the real API response, including its `vetted`/`maturity` fields, for scripted callers.

Agent-class tokens do not mutate directly: every write becomes a review-queue proposal (`docs/mvp-spec.md` §4.11) — except the `require_human` endpoints (spec §4.11's ∅ scope column, e.g. `vet`, `review resolve`, `token create/revoke`, `sync add`), which reject an agent token outright with a `403` instead of proposalizing it. Bootstrapping the first human token is not yet CLI-supported — see [`quickstart.md`](quickstart.md) step 2.
