# akasha — MVP Implementation Specification & Plan of Action

**Spec version:** 1.0 · **Derived from:** PRD v1.6 (authoritative for *why*; this document is authoritative for *what and how*)
**MVP scope:** PRD Phases 0–2 — kernel, deterministic capture, Obsidian bijective sync, TMS loop, CLI, minimal web UI, Windows-first.
**Audience:** implementing agents and contributors. Every work package has explicit acceptance tests and a machine-checkable definition of done.

---

## 0. How to use this document (rules for implementing agents)

1. Work milestones **in order** (M0 → M10). Do not start a milestone before its dependencies' DoD checks pass.
2. **Never** invent schema, endpoints, ID formats, or grammar beyond this spec. If something is ambiguous, implement the narrowest reading and add a `# SPEC-QUESTION:` comment plus an entry in `docs/spec-questions.md`.
3. **Never** edit golden files, fixtures, or acceptance tests to make an implementation pass. Golden files change only via a task that explicitly says so.
4. Every mutation of persistent state goes through the kernel module; no module writes SQLite directly except `kernel/store.py`.
5. All persisted bytes obey §4.3 (canonicalization). **Pickle is forbidden everywhere** (enforced by lint rule).
6. The product name never appears in on-disk formats, anchors, or schema identifiers (rebrand invariant). The neutral on-disk prefix is `tm` (e.g., anchors `^tm-...`), chosen because it is not brand-derived.
7. Run `make check` (lint + typecheck + unit + property) before considering any task done; run `make battery` before closing M5+ tasks.
8. NO BACKWARD COMPATIBILITY is needed-- always make optimal changes. ALWAYS MAKE CHANGES that conform to the interfaces of other mudules if needed to use with CURRENT codebase, but NEVER make changes to previous codebases-- I am the only user. 

---

## 1. System overview

```
                 ┌────────────────────────────────────────────┐
                 │              akasha daemon (Python)         │
                 │                                            │
 Obsidian vault  │  watcher ──► sync/reconcile ──► kernel     │
 (files) ◄──────►│  (watchdog)   (3-way diff)     (SQLite)    │
                 │                    ▲               ▲       │
                 │                    │               │       │
                 │              contract parser   tms loop    │
                 │              / renderer        (invalidate,│
                 │                                triggers,   │
                 │                                review)     │
                 │                    ▲               ▲       │
                 │       ┌────────────┴───────────────┴─────┐ │
                 │       │   FastAPI localhost API (:7433)  │ │
                 │       └───▲──────────▲──────────▲────────┘ │
                 └───────────┼──────────┼──────────┼──────────┘
                        CLI (typer)  web UI     Obsidian plugin
                                     (served)   (TS, thin client)
```

Data flow invariants: the **hub (SQLite) is the writer of record**; each file-backed spoke is a projection under contract; every surface (CLI, UI, plugin, future MCP) speaks only the localhost API; sync writes to managed files only canonical renders.

---

## 2. Repository layout

```
akasha/
├── pyproject.toml              # uv-managed; python >= 3.12
├── Makefile                    # check, battery, run, fmt targets
├── migrations/                 # 001_init.sql, 002_*.sql ... (forward-only)
├── src/akasha/
│   ├── kernel/
│   │   ├── ids.py              # §4.1 minting + checksum + validation
│   │   ├── canonical.py        # §4.3 byte canonicalization (text + JSON)
│   │   ├── model.py            # §4.4 pydantic models (single source of truth)
│   │   ├── store.py            # §4.5 SQLite access layer (only DB writer)
│   │   ├── commits.py          # commit DAG, change classes, facets_touched
│   │   └── maturity.py         # §4.6 stage derivation + deletion rules
│   ├── contract/
│   │   ├── grammar.py          # §4.7 tokens/regexes, contract version const
│   │   ├── parser.py           # managed-file contract text -> BlockSet
│   │   ├── render.py           # hub nodes -> canonical contract text
│   │   └── linter.py           # violations, certain-repair, pause&diff
│   ├── sync/
│   │   ├── base_store.py       # per-file base snapshots (blob table)
│   │   ├── reconcile.py        # §4.8 three-way merge pipeline
│   │   ├── watcher.py          # watchdog, debounce, cloud-path detection
│   │   └── origin.py           # echo suppression tags
│   ├── tms/
│   │   ├── invalidate.py       # §4.9 interface-break walk
│   │   ├── triggers.py         # §4.10 condition registry + evaluator
│   │   └── review.py           # queue, resolutions, daily cap
│   ├── api/
│   │   ├── app.py              # FastAPI factory; OpenAPI snapshot check
│   │   ├── auth.py             # token classes, rate limits
│   │   ├── schemas.py          # request/response models (re-export kernel)
│   │   └── routes/             # nodes.py edges.py search.py review.py
│   │                           # sync.py tokens.py health.py
│   ├── cli/main.py             # typer app: daemon + CRUD verbs
│   ├── ui/                     # templates/ static/ (htmx + vanilla JS)
│   ├── daemon.py               # process lifecycle, single-instance lock
│   ├── config.py               # paths, ports, budgets; TOML config file
│   └── metrics.py              # §7 counters; /v1/metrics
├── plugin-obsidian/            # TypeScript thin client (M6)
├── tests/
│   ├── unit/  property/  integration/  battery/
│   └── golden/
│       ├── serialization/<case>/{input.md,expected.md}
│       └── reconcile/<case>/{base.md,vault.md,hub.json,expected.md,expected_ops.json}
└── docs/
    ├── contract-v1.md          # human-readable contract (generated from §4.7)
    ├── api-snapshot/openapi.json  # frozen migration contract (PRD §7.12)
    └── spec-questions.md
```

---

## 3. Conventions & toolchain

Python 3.12+, managed by `uv`. Lint/format: `ruff` (rule set includes a custom ban on `pickle`, `eval`, `exec`). Types: `pyright --strict` on `src/`. Tests: `pytest`, `hypothesis` for property tests. DB: stdlib `sqlite3`, WAL mode, `PRAGMA foreign_keys=ON; PRAGMA synchronous=NORMAL`. Each HTTP request opens a **fresh WAL connection** (`PRAGMA busy_timeout=5000`) and closes it at request end — WAL gives concurrent readers + one writer. A single connection must **never** be shared across the ASGI request threadpool (falsified T8.5b / vision F14: corrupted reads under concurrent requests — `check_same_thread=False` disables only the same-thread assertion, not concurrent access). A long-lived connection is used only for pre-serving startup work (migrate/reconcile) and for test/embedded callers that inject one (`create_app(conn=...)`) and drive the app sequentially. Store at `%APPDATA%/tm-daemon/store.db` on Windows / `~/.config/tm-daemon/store.db` elsewhere. HTTP: FastAPI + uvicorn, port **7433** default, bind `127.0.0.1` only. Config at `%APPDATA%/tm-daemon/config.toml` (Windows) / `~/.config/tm-daemon/config.toml` elsewhere — note neutral dir name per rule 0.6. Logging: structured JSON lines to a rotating file + stderr. Commits: Conventional Commits. CI: GitHub Actions matrix `[windows-latest, ubuntu-latest]`; Windows is the release gate.

---

## 4. Core specifications

### 4.1 Identifiers

- Alphabet `A = "abcdefghijklmnopqrstuvwxyz234567"` (RFC 4648 base32, lowercase; index 0–31).
- An ID is **8 chars**: 7 random core chars (from `secrets`) + 1 checksum char.
- Checksum: `A[(Σ_{i=0..6} (i+1) * idx(c_i)) mod 32]` — weighted to catch transpositions.
- Validation: length 8, alphabet membership, checksum match. Invalid checksum ⇒ contract violation `E_ID_CHECKSUM`, never a guess.
- Minting: generate, `SELECT 1 FROM nodes WHERE id=?`, retry on collision (loop bound 10, then error).
- Contract anchor form: `^tm-<id8>`; the bare id is never shown without the `tm-` prefix in managed files.

```python
def checksum(core: str) -> str:
    return A[sum((i + 1) * A.index(c) for i, c in enumerate(core)) % 32]
```

### 4.2 Node & edge model (pydantic, single source of truth)

```python
NodeType   = Literal["entity","definition","claim","relation","proof","evidence","task"]
EdgeType   = Literal["composes","supports","contradicts","depends_on",
                     "derived_from","cites","redirects_to"]
Maturity   = Literal["S0","S1","S2","S3","S4"]
ChangeClass= Literal["patch","minor","major"]

class Facet(BaseModel):
    facet_id: str          # id8, minted like node ids
    name: str              # short label, unique per node
    span: str              # the highlighted span of the definition (facets-from-spans)
    version: int           # bumped on interface break of this facet

class Node(BaseModel):
    id: str                # id8
    node_type: NodeType
    body: str              # canonical text (§4.3)
    facets: list[Facet] = []
    task_state: Literal["open","done"] | None = None   # tasks only
    vetted: bool = False   # S4 flag
    status: Literal["live","retracted","tombstone"] = "live"

class Edge(BaseModel):
    id: str
    src: str; dst: str
    edge_type: EdgeType
    facet_binding: str | None   # REQUIRED (facet_id or "*") for justification
                                # edge types; None allowed only for composes/redirects_to
    provenance: Literal["human","agent_approved","imported"]
    mode: Literal["track","pin"] = "track"
    pinned_commit: str | None = None
```

Justification edge types = `{supports, contradicts, depends_on, derived_from, cites}`. `"*"` bindings are legal but counted against the facet-coverage metric (§7).

### 4.3 Canonicalization (byte-level, language-neutral — PRD §7.12 rule 2)

Text: UTF-8, Unicode **NFC**, line endings **LF**, no trailing whitespace on any line, exactly one trailing newline, tabs preserved inside code fences only, otherwise expanded to spaces on managed lines. JSON (for hashing objects): `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")`. Object hash: `sha256` hex of canonical bytes. These rules live in `kernel/canonical.py` and in `docs/contract-v1.md`; **no other module normalizes text.**

### 4.4 SQLite DDL (current schema after numbered migrations)

```sql
CREATE TABLE objects   (hash TEXT PRIMARY KEY, kind TEXT NOT NULL,
                        bytes BLOB NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE nodes     (id TEXT PRIMARY KEY, node_type TEXT NOT NULL,
                        head_hash TEXT NOT NULL REFERENCES objects(hash),
                        maturity TEXT NOT NULL DEFAULT 'S0',
                        status TEXT NOT NULL DEFAULT 'live',
                        vetted INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE commits   (hash TEXT PRIMARY KEY, node_id TEXT NOT NULL REFERENCES nodes(id),
                        parents TEXT NOT NULL,            -- JSON array of commit hashes
                        object_hash TEXT NOT NULL REFERENCES objects(hash),
                        change_class TEXT NOT NULL,       -- patch|minor|major
                        facets_touched TEXT NOT NULL,     -- JSON array of facet_ids
                        author TEXT NOT NULL,             -- token id
                        message TEXT NOT NULL DEFAULT '', ts TEXT NOT NULL);
CREATE TABLE edges     (id TEXT PRIMARY KEY, src TEXT NOT NULL, dst TEXT NOT NULL,
                        edge_type TEXT NOT NULL, facet_binding TEXT,
                        provenance TEXT NOT NULL, mode TEXT NOT NULL DEFAULT 'track',
                        pinned_commit TEXT, created_at TEXT NOT NULL, retracted_at TEXT);
CREATE INDEX ix_edges_dst ON edges(dst) WHERE retracted_at IS NULL;
CREATE INDEX ix_edges_src ON edges(src) WHERE retracted_at IS NULL;
CREATE TABLE redirects  (old_id TEXT PRIMARY KEY, successors TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE review_queue (id TEXT PRIMARY KEY, node_id TEXT,
                        cause_kind TEXT NOT NULL,  -- facet_break|subtasks_closed|evidence_retracted|recheck|conflict|violation|proposal
                        cause_ref TEXT, facet TEXT, created_at TEXT NOT NULL,
                        resolved_at TEXT, resolution TEXT);  -- still_holds|revised|retracted|dismissed
CREATE TABLE triggers   (id TEXT PRIMARY KEY, node_id TEXT NOT NULL,
                        condition TEXT NOT NULL, params TEXT NOT NULL DEFAULT '{}',
                        enabled INTEGER NOT NULL DEFAULT 1);
CREATE TABLE sync_roots (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
                        root_path TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
CREATE TABLE sync_files (path TEXT PRIMARY KEY, sync_root_id TEXT NOT NULL REFERENCES sync_roots(id),
                        base_hash TEXT REFERENCES objects(hash),
                        contract_version INTEGER NOT NULL, last_synced_at TEXT);
CREATE TABLE tokens     (id TEXT PRIMARY KEY, name TEXT NOT NULL,
                        class TEXT NOT NULL,               -- human|agent
                        secret_hash TEXT NOT NULL, rate_per_min INTEGER,
                        created_at TEXT NOT NULL, revoked_at TEXT);
CREATE TABLE audit_log  (ts TEXT NOT NULL, token_id TEXT, action TEXT NOT NULL, detail TEXT);
CREATE VIRTUAL TABLE nodes_fts USING fts5(id UNINDEXED, body);
```

`review_queue.node_id` is the affected existing node when one exists. It is `NULL` only for an agent proposal to create a node: the review row's own `id` is the proposal correlation key, and the node id is minted by `create_node` only after human approval. `cause_ref` for `cause_kind='proposal'` is canonical JSON with the complete would-be request: `{"method": ..., "path": ..., "body": ...}`.

`sync_roots` contains durable registrations of watched filesystem roots. In the MVP each root is an Obsidian vault, but “sync root” is the API/schema term; “spoke” names an integration type or client surface, not one registered directory. A registration must survive daemon restart even before it has any `sync_files` rows.

Append-only discipline: `objects` rows are never updated or deleted (except S0 GC, which may delete objects unreachable from any S1+ node or base snapshot). Node "edits" insert a new object + commit and move `head_hash`.

### 4.5 Store API (kernel/store.py — the only DB writer)

Exposed functions (all transactional): `create_node`, `commit_node(node_id, new_body|facets, change_class, facets_touched, author)`, `get_node(id, as_of=None)`, `history(id)`, `create_edge`, `retract_edge`, `split_node(id, parts) -> redirect`, `merge_nodes(ids) -> redirect`, `delete_node(id)` (S0 hard-delete; S1+ ⇒ tombstone + redirect required), `search(q)`, `neighborhood(id, hops=1)`, `enqueue_review`, `resolve_review`, GC job. Each function's docstring restates its invariant; property tests in `tests/property/test_store.py` assert: no dangling edges, head always reachable, as-of correctness, S0-GC never removes referenced objects.

### 4.6 Maturity derivation (kernel/maturity.py)

```
S1 iff live inbound edge count ≥ 1
S2 iff S1-eligible AND node_type set AND len(facets) ≥ 1     (types other than task/entity)
S3 iff S2-eligible AND ≥1 inbound justification edge from an evidence/proof node
S4 iff vetted flag set by human token
maturity = highest satisfied stage (S0 default); recomputed inside the same
transaction as any mutation that can change the inputs.
```

Deletion: S0 → hard delete; S1+ → require `redirect_to` successors or explicit tombstone; API returns `409 E_NEEDS_REDIRECT` otherwise.

### 4.7 Contract grammar v1 (Obsidian sublanguage)

File-level: front-matter key `tm: 1` marks a managed file (added by the daemon on first projection; files without it are never parsed for management, but `^tm-` anchors found in unmanaged files raise advisory lint `W_UNMANAGED_ANCHOR`).

Block grammar (line-oriented; EBNF):

```
anchor      := "^tm-" id8
managed_par := text SP anchor EOL                       ; paragraph node
task_line   := indent "- [" ("x"|" ") "] " text SP anchor EOL
new_line    := (text | task_form) SP "^tm-new" EOL      ; user requests minting
embed       := "![[" path "#^tm-" id8 "]]"              ; read-only transclusion
ref         := "[[" path "#^tm-" id8 "]]"               ; inline reference
indent      := (2 spaces)*                              ; nesting depth = indent/2
```

Semantics: a `task_line` maps to a task node (`- [x]` ⇔ `task_state=done`); an indented task under another task ⇒ `composes(parent→child)` edge; `^tm-new` ⇒ daemon mints an ID and rewrites the line (this rewrite is origin-tagged, not an echo); embeds render the target's current body (read-only in Obsidian by nature). Anything inside fenced code blocks is ignored entirely. Text matching the anchor pattern *not* at end-of-line is plain text.

Violations (linter codes): `E_ID_CHECKSUM` malformed anchor; `E_DUP_ID` same anchor twice in a sync root (copy without cut); `E_LOST_ANCHOR` managed block's text found (fuzzy ≥ 0.9 similarity to base) but anchor deleted; `E_DELETED_S1` managed block deleted whose node is S1+. **Certain auto-repairs** (silent, logged, undoable): `E_LOST_ANCHOR` where text is byte-identical to base except the anchor ⇒ re-insert anchor; `E_DUP_ID` where one copy is byte-identical to base ⇒ the identical copy keeps the ID, the other gets `^tm-new` minting proposed. Everything else ⇒ review item. **Pause & diff:** if violations affect > 25% of a file's managed blocks in one sync cycle (formatter storm), make no writes, snapshot the file, open one review item with a diff. A managed file is a lossless container: lines that are not contract constructs pass through write-back verbatim by position; the hub owns only anchored lines.

### 4.8 Sync engine (sync/reconcile.py) — per-file pipeline

```
on_change(path) after 500 ms debounce:
  V  = canonicalize(read(path))               # current managed-file bytes
  B  = base_store.get(path)                   # last agreed bytes (may be None)
  H  = render(hub_state_for(path))            # canonical projection
  if V == B and H == B: return                # quiet
  if V == B:            write_if_diff(path,H); base=H; return       # hub-only change
  blocksV = parse(V); blocksB = parse(B or "")
  ops, violations = diff_blocks(blocksB, blocksV)   # keyed by anchor id:
        # ops: modified | created(new) | deleted | moved | checkbox_toggled | reparented
  if pause_threshold(violations): pause_and_diff(path); return
  apply certain-repairs; enqueue remaining violations
  for op in ops:
      if hub_changed_since(base, op.node): conflict_queue(op.node)  # both-sides edit
      else: kernel.apply(op)                  # via store API, origin='sync'
  H2 = render(hub_state_for(path))
  write_if_diff(path, H2)                     # canonical write-back
  base_store.put(sync_root_id, path, H2)
```

Echo suppression: writes performed by the daemon record `(path, hash)` in `origin.py`; a watcher event whose content hash matches a recorded write is dropped. Startup: run `on_change` for every managed file (idempotent — this is also crash recovery). Conflict semantics: hub keeps both versions as branches on the node's commit DAG; review item `cause_kind=conflict`.

### 4.9 Invalidation walk (tms/invalidate.py)

Trigger: any commit with `change_class == "major"` (UI/CLI sets it; heuristic default = major iff any facet was removed/renamed or `facets_touched` includes a facet whose `version` was bumped; node retraction is always major touching all facets).

```
def invalidate(node_id, commit, touched: set[facet_id]):
    subs = edges where dst == node_id and retracted_at is null and mode == 'track'
           and edge_type in JUSTIFICATION | {'composes'}
           and (facet_binding in touched or facet_binding == '*'
                or (edge_type == 'composes' and composes_touched_facet(edge, touched)))
    for e in subs:
        if not already_unresolved_stale(e.src):        # non-transitive damper
            enqueue_review(e.src, cause='facet_break', cause_ref=commit, facet=e.facet_binding)
```

Resolutions (`review.py`): `still_holds` (clear, record), `revised` (client submits a new commit; that commit is itself classified), `retracted`, `dismissed` (violations only). Daily active-queue cap: 10 items, ordered by (staleness age, inbound-edge count, user flag).

### 4.10 Triggers v1 (tms/triggers.py)

Registry of pure functions `condition(node, ctx) -> bool`, evaluated (a) after every commit touching the node or its children, (b) on a daily tick. Conditions: `all_subtasks_closed`, `facet_interface_changed` (built-in — implemented *as* §4.9), `evidence_retracted`, `recheck_after` (params: ISO date, period). Sole action: `enqueue_review`. Adding a condition requires a spec change (schema-freeze discipline).

### 4.11 Localhost API (v1) — endpoint table

All authenticated application endpoints are under `/v1`, use JSON, and require header `Authorization: Bearer <token>`. The operational `GET /health` endpoint is intentionally root-level and unauthenticated. Errors: `{"error": {"code": "...", "message": "...", "detail": {}}}`. Agent-class tokens: mutating endpoints are rewritten into proposals (review items, `cause_kind=proposal`) unless the endpoint is marked ∅; rate-limited per token. Proposal `cause_ref` stores canonical `{method,path,body}`. Existing-node proposals use that node as `node_id`; edge proposals use the edge's `dst`. Create-node proposals use `node_id=NULL` and mint only when approved.

| Method & path | Purpose | Notes |
|---|---|---|
| GET  /health | liveness, version, contract version | no auth |
| GET  /nodes/{id} | node + maturity (+`?as_of=ISO`) | |
| POST /nodes | create node (type, body, facets?, task_state?) | |
| PATCH /nodes/{id} | commit edit (body/facets, change_class, facets_touched, message) | |
| DELETE /nodes/{id} | S0 delete; S1+ needs `redirect_to` body | 409 `E_NEEDS_REDIRECT` |
| GET  /nodes/{id}/history · /neighborhood?hops=1 | commit DAG · 1-hop graph | |
| POST /nodes/{id}/split · /merge | refactor ops; returns redirect + reassignment queue | merge: path id survives; body `{"ids":[other_ids...]}` |
| POST /nodes/{id}/vet | set S4 | human token only ∅ |
| POST /edges · DELETE /edges/{id} | create (validates facet_binding rule) / retract | |
| GET  /search?q= | FTS over bodies | |
| GET  /review?status=open · POST /review/{id}/resolve | queue · resolutions | resolve: human only ∅; `GET /review` returns the FULL open set (optional `?node=<id>` filter) — the §4.9 daily cap-10 is a client/display concern, never an endpoint limit (T8.0) |
| GET  /sync/status · POST /sync/rescan | per-sync-root state, violations, pauses | |
| GET  /sync/export | full canonical projection of every managed file | read-only — mutates nothing, not even review items; any token class; items ordered by (sync-root name, POSIX root-relative path), each `{sync_root, relative_path, text}` with `text` the §4.7 canonical render of the hub's current state; top-level `unfiled_node_count` = live nodes present in no managed projection (T10.2 fable ruling, 2026-07-18) |
| GET/POST /sync/roots | register/list durable filesystem sync roots | human only ∅ |
| GET/POST/DELETE /tokens | token management | human only ∅ |
| GET  /metrics | §7 counters (JSON) | |

The generated OpenAPI JSON is snapshotted at `docs/api-snapshot/openapi.json`; CI fails if the served spec diverges without the snapshot being deliberately updated in the same PR (PRD §7.12 rule 1).

### 4.12 CLI (cli/main.py, typer)

`akasha daemon [--config PATH]` · `akasha new TYPE BODY [--facet name=span ...] [--task]` · `akasha get ID [--as-of ISO]` · `akasha set ID [--body ...] [--class patch|minor|major] [--touch FACET]` · `akasha rm ID [--redirect-to ID...]` · `akasha search Q` · `akasha review [list|resolve ID RESOLUTION]` · `akasha token [create|revoke|list]` · `akasha export --md DIR`. Global flags: `--json` (output schema `cli/v1`, versioned, additive-only), `--dry-run` (mutations return the would-be request), `--token`, `--base-url` (defaults to `http://127.0.0.1:7433`; permits test/non-default daemon endpoints). Omitted `akasha set --class` defaults to `patch`, the least-invalidating class. The review verbs target the §4.11 endpoints even before T7.5 lands them and must fail without a traceback until then. Exit codes: 0 ok · 1 error · 2 usage · 3 not found · 4 conflict/violation/needs-redirect. `export` (M10, T10.2 fable ruling 2026-07-18) is a pure client of `GET /v1/sync/export`: it writes each returned file's canonical `text` byte-for-byte to `DIR/<sync_root>/<relative_path>`, and its `--json` summary lists the files written plus the endpoint's `unfiled_node_count`; re-export is byte-stable because the endpoint serves the §4.7 canonical render (T5.8). `daemon` remains the **only** verb that does not speak HTTP — it *is* the server; no other verb may read the store directly (§1 data-flow invariant, PRD §7.11 API-first parity, PRD §7.12 rule 1).

### 4.13 Web UI (MVP-minimal)

Daemon-served, htmx + vanilla JS, four views: **Node** (body, facets, 1-hop neighborhood, history, stale badge with cause), **Review** (queue with one-click resolutions — `still_holds`/`retracted`; `dismissed` shown for violations only; `revised` uses an inline body editor that submits a new commit, defaults `change_class=minor`/`facets_touched=[]` **disclosed in the UI**, T8.3 — plus a daily-cap banner), **Search**, **Sync** (per-sync-root status, violations, pause&diff inspector; display “Obsidian vault” in Obsidian-specific copy). Badge copy uses "vetted by you" language, never "true" (PRD R9). No SPA framework; no build step beyond copying static files. **Rendering:** static HTML shells (served verbatim from `ui/templates/`) + client-side vanilla JS fetching `/v1` JSON; no server-side templating engine (no jinja2).

---

## 5. Plan of action — milestones

Each milestone lists deliverables, key tasks, and **DoD** (all commands must pass on Windows CI).

**M0 — Scaffold (dep: none).** Repo layout §2, Makefile, uv env, ruff+pyright+pytest wired, CI matrix, migration runner, config loading, logging. *DoD:* `make check` green on both OSes with a placeholder test.

**M1 — Kernel store (dep: M0).** §4.4 DDL, §4.5 store API, §4.6 maturity, commit DAG, redirects, S0 GC. *DoD:* `pytest tests/unit/kernel tests/property/test_store.py`; property suite includes no-dangling-refs, as-of, GC-safety; 10k-node synthetic benchmark `pytest tests/integration/test_perf.py::test_neighborhood_p95` asserts p95 < 50 ms.

**M2 — IDs + canonicalization (dep: M0).** §4.1, §4.3, golden serialization corpus seeded (≥ 15 cases incl. NFD input, CRLF input, trailing-whitespace, emoji, nested fences). *DoD:* `pytest tests/unit/test_ids.py tests/golden/test_serialization.py`; hypothesis round-trip `canonicalize(canonicalize(x)) == canonicalize(x)`.

**M3 — Contract parser/renderer (dep: M2).** §4.7 grammar, parser, renderer, linter with violation codes + certain-repair. *DoD:* golden corpus ≥ 25 cases; hypothesis: for generated in-contract docs D, `render(parse(D)) == D` and for generated hub graphs G, `parse(render(G)) == G`; fuzz corpus committed under `tests/golden/serialization/fuzz/`.

**M4 — Daemon + API + CLI core (dep: M1, M3).** FastAPI app, auth/token classes with rate limits and audit log, endpoints of §4.11 except sync, CLI verbs of §4.12, OpenAPI snapshot check, single-instance lock, autostart docs (Task Scheduler XML + NSSM instructions in `docs/`). *DoD:* `pytest tests/integration/test_api.py tests/integration/test_cli.py`; agent-token mutation lands in review queue (test `test_agent_writes_become_proposals`); snapshot-diff CI job green.

**M5 — Sync engine (dep: M4).** Base store, watcher with debounce + cloud-path detection (warn + conservative profile when path is under OneDrive/Dropbox markers), reconcile pipeline §4.8, echo suppression, conflict branching, pause&diff. *DoD:* `make battery` — the scripted edit battery (§6.2) passes 100% with 0 silent guesses; kill-daemon-mid-sync test converges on restart (`test_crash_recovery_idempotent`).

**M6 — Obsidian plugin (dep: M5).** TS thin client: settings (URL+token), status bar (sync state, violation count), command "create node from selection" (wraps selection, appends `^tm-new`), clipboard cut/copy carrying anchors. *DoD:* manual test script `plugin-obsidian/TESTPLAN.md` executed against a demo vault; plugin build in CI.

**M7 — TMS loop (dep: M4).** §4.9 invalidation, §4.10 triggers, review queue + resolutions + daily cap, split/merge with inbound-reassignment queue, facets-from-spans capture flow in API/UI (`POST /edges` accepts `facet_span` and creates the facet on the target). *DoD:* `pytest tests/integration/test_tms.py` covering: major commit flags exactly the bound subscribers; `*`-binding flagged on any break; supertask trigger fires once and never auto-closes; split leaves zero dangling references (property test).

**M8 — Web UI (dep: M7).** §4.13 views. *DoD:* playwright smoke test: create → link with span → break facet → see badge → resolve.

**M9 — Hardening (dep: M5–M8).** Windows battery items (CRLF, locking retry, AV noise), RSS/CPU sampling into metrics, S0 GC scheduling, log rotation, `--dry-run` coverage, error-message pass. *DoD:* 24-h soak test script (`tests/battery/soak.py`) — RSS < 150 MB, idle CPU ≈ 0%, zero unhandled exceptions.

**M10 — Dogfood instrumentation (dep: all).** Metrics dashboard view (facet coverage, inflow vs resolution + variance, violation rate, crossing rate), export command `akasha export --md DIR` (a pure client of `GET /sync/export` — §4.11/§4.12, T10.2 fable ruling). *DoD:* PRD §8 acceptance stories 1–9 each mapped to a passing test or a checked manual script in `docs/acceptance.md`; **the one-month dogfood gate begins.**

Dependency-critical path: M0→M1→M4→M5→M7→M10; M2→M3 feeds M4/M5; M6, M8 parallelize after their deps.

---

## 6. Test & verification strategy

**6.1 Layers.** Unit (pure functions), property (hypothesis: round-trips, idempotence, invariants), golden (byte-exact fixtures; the migration acceptance suite per PRD §7.12 rule 3), integration (temp vault + live daemon on a random port), battery (end-to-end vault scenarios), soak.

**6.2 Scripted edit battery (tests/battery/) — all must pass with zero silent guesses:**
E01 edit managed text in place · E02 toggle checkbox · E03 move block within file · E04 cut-paste block to another file · E05 copy-paste duplicate (⇒ `E_DUP_ID`, certain-repair path) · E06 delete S0 block (⇒ hard delete) · E07 delete S1 block (⇒ review, no data loss) · E08 create via `^tm-new` (⇒ mint + rewrite, no echo) · E09 CRLF file arrives (⇒ canonicalized, no spurious diff) · E10 NFD filename + content (⇒ NFC, stable) · E11 edits while daemon down (⇒ startup reconcile) · E12 hub+vault concurrent edit of same node (⇒ conflict branch + review) · E13 formatter rewrites whole file (⇒ pause&diff, no writes) · E14 fake anchor inside code fence (⇒ ignored) · E15 malformed checksum (⇒ `E_ID_CHECKSUM` review) · E16 embed added and target edited in hub (⇒ embed shows head) · E17 subtask re-indent (⇒ reparent op) · E18 rapid modify bursts (⇒ debounce, single cycle) · E19 vault under simulated OneDrive path (⇒ warning + conservative profile) · E20 5,000-block file (⇒ cycle < 2 s, memory bounded).

**6.3 CI gates.** `make check` on every push; battery + soak on `main` nightly (Windows); OpenAPI-snapshot diff gate; golden-file-change gate (fails unless PR label `golden-update`).

---

## 7. Metrics (metrics.py → GET /v1/metrics)

`facet_coverage` (S2+ definitions with ≥1 non-`*` inbound binding ÷ S2+ definitions) · `review_inflow_7d`, `review_resolved_7d`, `inflow_variance_30d` · `violation_rate` (violations ÷ sync cycles) · `auto_repairs{class}` · `crossing_rate` (nodes created ÷ day) · `rss_bytes`, `idle_cpu_pct` (sampled) · `sync_cycle_ms{p50,p95}`. Dogfood gates read these directly (PRD §11).

---

## 8. Expandability hooks (build now, use later — do NOT implement the future phases)

- **Phase 3 decomposer:** plugs in as an API client that POSTs proposals; the proposal pathway (agent tokens → review queue) already exists. Reserve `cause_kind=proposal` rendering in UI.
- **Phase 4 MCP:** a facade process importing nothing but the HTTP API; keep `schemas.py` re-exportable.
- **Script notes:** `tms/triggers.py` registry is the future host boundary; do not add a script runner now.
- **Multi-device:** all state already content-addressed with per-commit parents (CRDT-friendly); never introduce global sequence counters.
- **Rust migration:** the golden corpus, OpenAPI snapshot, and no-pickle/canonical-bytes rules are the enablers — treat them as sacred (agents: rule 0.3).

Explicit non-goals for MVP (do not build even if easy): LLM calls of any kind, embeddings, MCP server, mobile anything, multi-user anything, scheduling/recurrence for tasks, prose management.

---

## 9. Acceptance — mapping to PRD §8 stories

| PRD story | Verified by |
|---|---|
| 1 capture ≤3s (syntax path) | M4 CLI/API timing test + manual script |
| 2 contradiction surface (non-LLM) | M7 near-duplicate FTS heuristic test |
| 3 invalidation on major edit | `test_tms.py::test_facet_break_flags_subscribers` |
| 4 split/merge zero dangling | property test in M7 |
| 5 as-of time travel | `test_api.py::test_as_of` |
| 6 review economy (cap, dashboard) | M10 dashboard + metrics assertions |
| 7 contract sync losslessness | M5 battery E01–E20 |
| 8 tasks + supertask trigger + S0 lifecycle | `test_tms.py::test_supertask_flag`, battery E06/E08 |
| 9 daemon residency | M9 soak + crash-recovery tests |

When all rows are green on Windows CI, the MVP is code-complete and the one-month dogfood gate (PRD §9 Phase 2) starts. Its outcome — not this document — decides Phase 3.
