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

# refactor: split one node into several / merge nodes into one (build-plan T14.4)
uv run akasha --token "$AKASHA_TOKEN" split <id> --part 'definition=Tree (botany): a plant.' --part 'definition=Tree (CS): an acyclic graph.'
uv run akasha --token "$AKASHA_TOKEN" merge <survivor-id> <other-id> [<other-id> ...]

# first run, one command: database, your first token, the daemon, and a vault (build-plan T18.5)
uv run akasha setup /path/to/vault

# install the Obsidian plugin into a vault (build-plan T18.8; needs a built plugin-obsidian/)
uv run akasha plugin install /path/to/vault

# see a transclusion resolved, headlessly: prints the file with each embed replaced by its
# target's CURRENT text; does not modify files (build-plan T18.7)
uv run akasha render notes/B.md

# why isn't it syncing? one read-only screen (build-plan T18.6)
uv run akasha status

# the daemon in the background (build-plan T18.3): no second terminal to babysit
uv run akasha up      # start it detached, wait until healthy; a no-op if already running
uv run akasha down    # stop it; a no-op if it is not running

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
- `--base-url` (env: `AKASHA_BASE_URL`) — point at a non-default daemon (defaults to `http://127.0.0.1:7433`).
- `--token` (env: `AKASHA_TOKEN`) — bearer token. Resolved in this order: `--token`, then `AKASHA_TOKEN` (build-plan T18.2), then the **saved token file** (below). An empty variable counts as unset. `--help` names the variables but never prints their values. `daemon`, `init` and `tray` ignore the flag and variable.
- **The saved token** (build-plan T18.9, ruling M18-A) — `init` and `setup` save the human token they mint to `tm-token` beside `config.toml` (`~/.config/tm-daemon/tm-token`, or `%APPDATA%\tm-daemon\tm-token`), created readable by you only (mode `0600`; on Windows it relies on the per-user `%APPDATA%` permissions). After that, verbs need no flag or variable. Know what this means: **anything that runs `akasha` as you — a script, an AI agent in your terminal — acts as you**, with your human token. The file is only ever your human token (agent tokens made with `token create` are never written to it), an existing file is never overwritten, and it is used only for the default local endpoint — never sent to an address you named with `--base-url`. A rejected saved token (revoked, or the database was reset) gets a hint saying so. Delete the file to stop.
- `set --task-state open|done` — only sent when explicitly passed; an omitted flag leaves an existing task's `task_state` unchanged (`docs/mvp-spec.md` §4.12, task T13.4).
- `edge add SRC DST TYPE` (build-plan T14.2) — `TYPE` is one of `composes|supports|contradicts|depends_on|derived_from|cites|redirects_to` (`docs/mvp-spec.md` §4.2). The five justification types (`supports|contradicts|depends_on|derived_from|cites`) **require** `--facet-binding ID` or `--facet-binding '*'`; omitting it on one of those types is rejected by the daemon itself (a `400`, surfaced verbatim, never re-implemented client-side) — `composes`/`redirects_to` are the only two types that accept no binding. `--facet-span TEXT` mints a brand-new facet on the target node from that text and binds to it, overriding any `--facet-binding` also passed.
- `vet ID` (build-plan T14.3) — the one maturity stage the spec calls a *user act* (`docs/mvp-spec.md` §4.6, §4.11): sets the node's `vetted` flag, and it reads `S4` on the next `get`. **Human-only**, and unlike every other write, an agent-class token is *never* proposalized here — it gets the daemon's own `403` outright. Plain output reads `<id>: vetted by you (maturity: S4)`, never the word "true" (PRD R9: vetting is a claim about your own review, not a claim that something is objectively true); `--json` still returns the real API response, including its `vetted`/`maturity` fields, for scripted callers.
- `split ID --part TYPE=BODY ...` and `merge SURVIVOR OTHER...` (build-plan T14.4) — the two refactors PRD §8 story 4 promises. **No refactor leaves a dangling id:** the retired id is tombstoned with a redirect, and every live edge that pointed at it is moved to the new home in the same transaction. `split` makes one brand-new node per `--part` (`TYPE` is a node type, `BODY` its text, at least one part; the first `=` separates them) and moves the inbound edges to the **first** successor — then queues one `reassignment` review item per inbound edge, because only you know which successor an edge really meant. Plain output names the successors, the redirect, and how many review items were opened; work them with `akasha review list` and `akasha review resolve <review-id> still_holds` (the edge stays on the first successor) or one of the other resolutions. `merge` keeps the **path id** (`SURVIVOR`) and retires the rest; it has one unambiguous survivor, so it opens no review items. Both honor `--dry-run` and `--json` (the raw API response); with an agent-class token both become a review proposal and change nothing until you approve it.
- `up` / `down [--config PATH]` (build-plan T18.3) — process verbs, like `daemon` and `init`: they are not HTTP clients and ignore `--base-url`/`--token`. `up` starts the daemon detached (no terminal to keep open), waits until `GET /health` answers, and prints the URL and the log file (`daemon.log` in the config directory); if it is already answering it does nothing. It exits `4` if the single-instance lock is held but nothing answers (something else owns it) and `1` if the daemon did not become healthy in time. `down` stops it. "Running" means the lock is held, not that a `tm-daemon.pid` file exists, so a stale pid file is deleted and never signalled. Stopping abruptly is safe: startup reconcile is idempotent, so anything edited while it was down is picked up on the next start.
- **On-demand daemon start** (build-plan T18.4) — a verb aimed at the default local endpoint that finds no daemon listening starts one (the same detached start as `up`), prints `started daemon (log: <path>)` to stderr, and retries once. It never happens for an explicit `--base-url`/`AKASHA_BASE_URL` (those are not yours to spawn), for `--dry-run`, or when `AKASHA_NO_AUTOSTART` is set. The default endpoint is the address in your default config (`~/.config/tm-daemon/config.toml`, or `%APPDATA%\tm-daemon\config.toml`), which is `http://127.0.0.1:7433` when there is none. A `401` with no credential supplied by any route (no `--token`, no `AKASHA_TOKEN`) adds a one-line hint to run `akasha setup` and export the token; a rejected credential gets no hint.
- `setup [VAULT] [--config PATH] [--name NAME]` (build-plan T18.5) — the whole first run in one verb. On a fresh install it creates the database and your first **human token**, starts the daemon (`up`), and — given a `VAULT` folder — registers it as a sync root and reconciles it once. Every Markdown file under the vault is tracked by default (no per-file marker: a note gains no front matter, ever); a `.tmignore` file at the vault root opts paths out (gitignore-style: `#` comments, `*`, `?`, `**`, a trailing `/` for a folder, `!` to re-include; `.obsidian/`, `.git/`, `.trash/`, `node_modules/` and non-Markdown files are always skipped unless you `!`-include them). It prints, once, your token, the `export AKASHA_TOKEN=...` line and the web-UI link (`http://HOST:PORT/?token=...`) — **the link and the token are secrets**, and a token cannot be recovered afterwards. Re-running is safe: a token that already exists is never re-minted or re-printed (pass it with `--token`/`AKASHA_TOKEN`; without one `setup` exits `4` and says so), and registering the same vault again is an upsert. Registration starts watching the folder **before** the verb returns, so an edit made the instant it finishes is not missed. Nothing else is needed for transclusion: the same `^tm-` id in several files is one line kept identical in all of them ([`quickstart.md`](quickstart.md#transclusion-the-same-line-in-several-notes)). `--dry-run` prints the plan and changes nothing. Like `init`/`up`, `setup` is a process verb: it ignores `--base-url` and uses the address in the config. `init` is unchanged.
- `status` (build-plan T18.6) — read-only diagnosis, GETs only, never starts a daemon: whether the daemon answers (version), whether your credential is accepted, each sync root with its tracked-file count, open violations grouped by code, conflicts (a file is never paused; `pauses` stays for old reviews), and the open-review count. It names the three classic first-run failures with a one-line fix each — no credential (`akasha setup`, then `export AKASHA_TOKEN=...`), no vault registered (`akasha setup <folder>`), and a registered vault with nothing tracked yet (a note is only tracked once it holds a task or block to sync, e.g. `- [ ] something ^tm-new`; also check the path and `.tmignore`). Exit 0 when healthy; an unreachable daemon or a rejected credential exits 1 like every other verb. `--json` emits the `cli/v1` envelope.
- `render FILE` (build-plan T18.7) — the only headless file-to-file view. On disk an embed stays a link (`![[A.md#^tm-id]]`); `render` prints the file the way Obsidian would show it, with each embed replaced by the target node's **current** text from the hub: a standalone embed line becomes `> text (^tm-id, from A.md)`, an inline embed is replaced by the quoted text, and a fenced example is left alone. A tombstoned or missing target prints `[unresolved: ^tm-id (missing)]` instead of vanishing. It only reads: nothing is written, and the file's bytes are identical before and after. `--json` lists each embed with its resolved body and state.
- `plugin install VAULT [--from DIR] [--config PATH]` (build-plan T18.8) — copies the Obsidian plugin's `manifest.json` and **built** `main.js` into `VAULT/.obsidian/plugins/tm-hub/`, records the daemon address in its `data.json` (`daemonUrl`, only when not already set; **no token is written**), and lists `tm-hub` in `.obsidian/community-plugins.json` (existing entries and their order are kept). `--from` names a built `plugin-obsidian/` directory; without it a source checkout's own `plugin-obsidian/` is used (build it first with `npm ci && npm run build` — an unbuilt directory is a clear exit-3 error). Idempotent (a re-run changes nothing) and `--dry-run` prints the file operations without writing. Obsidian's own consent step cannot be automated, so the command prints what is left for you: turn off Restricted mode once, enable "TM Hub", paste your token in its settings — or pass `--with-token` to write your token into the plugin's settings for you. That puts a secret in a file inside the vault, so it is opt-in, and a warning is printed when the vault is under OneDrive/Dropbox or is a git repository.

Agent-class tokens do not mutate directly: every write becomes a review-queue proposal (`docs/mvp-spec.md` §4.11) — except the `require_human` endpoints (spec §4.11's ∅ scope column, e.g. `vet`, `review resolve`, `token create/revoke`, `sync add`), which reject an agent token outright with a `403` instead of proposalizing it. The first human token is minted by `akasha setup` (or `akasha init`) — see [`quickstart.md`](quickstart.md).
