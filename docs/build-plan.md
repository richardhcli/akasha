# akasha — Fine-Grained Agent Build Plan

**Derived from:** MVP Implementation Specification v1.0 (that document is authoritative; this one only sequences it into small, verifiable steps).
**Purpose:** Turn milestones M0–M10 into per-file tasks that a *less-capable agent* can execute one at a time, each with an explicit verification step and a machine-checkable Definition of Done (DoD).

---

## How to use this plan (read before doing anything)

1. **Do tasks strictly in ID order** within a phase, and never start a task whose `Depends on` tasks are not all `DONE`. When in doubt, stop and ask; do not improvise ordering.
2. **One task = one focused change.** Touch only the files listed under `Files`. If you feel you must touch a file not listed, that is a signal the task is misunderstood — stop and add a `# SPEC-QUESTION:` note in `docs/spec-questions.md` instead of guessing. Rule 5 is the sole precedence exception: when an API/TMS task necessarily persists state and its Files list accidentally omits `kernel/store.py`, add only the minimal store helper and record/correct the omission; never write SQLite from the higher layer.
3. **Never invent** schema, endpoints, ID formats, or grammar beyond the spec (spec rule 0.2). Implement the narrowest reading of any ambiguity.
4. **Never edit golden files, fixtures, or acceptance tests to make code pass** (spec rule 0.3). If a golden file looks wrong, that is a `# SPEC-QUESTION:`, not an edit.
5. **All persistent writes go through `kernel/store.py`** (spec rule 0.4); no other module writes SQLite.
6. **Pickle / eval / exec are forbidden** everywhere (spec rule 0.5, enforced by the ruff ban wired in T0.4).
7. The product name never appears in on-disk formats, anchors, or schema identifiers. The neutral on-disk prefix is `tm` (spec rule 0.6).
8. **A task is not DONE until its `Verify` command passes locally.** Run `make check` before closing any task; run `make battery` before closing any task in M5 or later (spec rule 0.7).
9. If a `Verify` command fails, the task stays `IN PROGRESS`. Do not mark it DONE, do not weaken the test, and do not move on.

### Per-task template

Every task below uses this shape:

- **Goal** — the single outcome.
- **Depends on** — task IDs that must be DONE first.
- **Files** — the only files you may create or edit.
- **Spec** — the authoritative section(s) to re-read before starting.
- **Steps** — the ordered actions.
- **Verify** — the exact command(s) to run.
- **DoD** — the machine-checkable pass condition.

### Status legend

Mark each task `TODO → IN PROGRESS → DONE` (or `BLOCKED: <reason>`). A phase is closed only when every task in it is DONE and the milestone DoD command from the spec passes on Windows CI.

### Dependency map (critical path in bold)

```
M0 ─┬─► M1 ─┬──────────────► **M4** ─► **M5** ─┬─► **M7** ─► M8 ─► **M10**
    │       │                                  │
    └─► M2 ─► M3 ──────────────► (feeds M4/M5)  └─► M9 (deps M5–M8)
                                                M6 (deps M5)  ─► M9
```
Parallelizable once deps are met: M6 and M8. Everything else follows the arrows.

---

## M0 — Scaffold (Depends on: nothing)

**Milestone DoD (spec):** `make check` green on windows-latest and ubuntu-latest with a placeholder test.

### T0.1 — Create repository skeleton
- **Goal** — Create the directory tree and empty package files of spec §2 so imports resolve.
- **Depends on** — none.
- **Files** — the full tree in spec §2 (`src/akasha/**` packages with empty `__init__.py`, `tests/{unit,property,integration,battery,golden}/`, `migrations/`, `docs/`, `plugin-obsidian/`).
- **Spec** — §2.
- **Steps** — (1) Create each directory. (2) Add an empty `__init__.py` in every Python package dir. (3) Add `.gitkeep` to empty non-package dirs. (4) Do **not** add logic yet.
- **Verify** — `python -c "import akasha, akasha.kernel, akasha.contract, akasha.sync, akasha.tms, akasha.api, akasha.cli"`
- **DoD** — the import command exits 0; `git status` shows only new files under the §2 layout.

### T0.2 — Author `pyproject.toml` (uv, Python ≥3.12)
- **Goal** — Declare the project, dependencies, and tool config so `uv` can build the env.
- **Depends on** — T0.1.
- **Files** — `pyproject.toml`.
- **Spec** — §2, §3.
- **Steps** — (1) Set `requires-python = ">=3.12"`. (2) Add runtime deps: `fastapi`, `uvicorn`, `pydantic`, `typer`, `watchdog`, `httpx`. (3) Add dev deps: `pytest`, `hypothesis`, `ruff`, `pyright`. (4) Configure `[tool.pyright]` with `strict` on `src/`. (5) Do not pin a package that the spec does not name.
- **Verify** — `uv sync && uv run python -c "import fastapi, pydantic, typer, watchdog"`
- **DoD** — `uv sync` resolves without error; the import line exits 0.

### T0.3 — Migration runner
- **Goal** — A forward-only migration applier that runs numbered SQL files from `migrations/` against the SQLite DB.
- **Depends on** — T0.2.
- **Files** — `src/akasha/kernel/store.py` (migration-runner section only), `migrations/` (no SQL yet beyond a placeholder), `tests/unit/kernel/test_migrations.py`.
- **Spec** — §3 (WAL, `foreign_keys=ON`, `synchronous=NORMAL`), §4.4 note (forward-only).
- **Steps** — (1) On connect, set `PRAGMA journal_mode=WAL; foreign_keys=ON; synchronous=NORMAL`. (2) Track applied migrations in a `schema_migrations` bookkeeping table. (3) Apply files in filename order, once each, in a transaction. (4) Refuse to run out-of-order or re-run applied files.
- **Verify** — `uv run pytest tests/unit/kernel/test_migrations.py`
- **DoD** — test proves: applies a placeholder migration once, is idempotent on re-run, and PRAGMAs are set on the connection.

### T0.4 — Ruff + pyright + custom bans wired
- **Goal** — Lint/type config including the custom ban on `pickle`, `eval`, `exec` (spec rules 0.5).
- **Depends on** — T0.2.
- **Files** — `pyproject.toml` (`[tool.ruff]`), `ruff.toml` if needed, a small `tests/unit/test_no_pickle_ban.py`.
- **Spec** — §3, rule 0.5.
- **Steps** — (1) Enable a ruff rule set that flags `import pickle`, `eval(`, `exec(` (use `flake8-bandit`/custom `per-file` select or a `fl0.5` grep-style test). (2) Add `tests/unit/test_no_pickle_ban.py` that greps `src/` for the forbidden tokens and fails if found. (3) Configure pyright strict on `src/`.
- **Verify** — `uv run ruff check src tests && uv run pyright src && uv run pytest tests/unit/test_no_pickle_ban.py`
- **DoD** — all three commands exit 0; a temporary `import pickle` in `src/` makes the ban test fail (spot-check, then revert).

### T0.5 — Config loading (`config.py`)
- **Goal** — Load paths, ports, budgets from a TOML file at the neutral dir per rule 0.6.
- **Depends on** — T0.2.
- **Files** — `src/akasha/config.py`, `tests/unit/test_config.py`.
- **Spec** — §3 (`%APPDATA%/tm-daemon/config.toml` on Windows; `~/.config/tm-daemon/` else; port 7433; bind 127.0.0.1).
- **Steps** — (1) Resolve the config dir per-OS using the neutral `tm-daemon` name. (2) Provide defaults (port 7433, bind 127.0.0.1). (3) Allow override via `--config PATH` value passed in. (4) Never write the brand name into the path.
- **Verify** — `uv run pytest tests/unit/test_config.py`
- **DoD** — test asserts default port 7433, bind `127.0.0.1`, and the Windows path contains `tm-daemon` (not the product name).

### T0.6 — Structured JSON logging
- **Goal** — JSON-lines logging to a rotating file plus stderr.
- **Depends on** — T0.2.
- **Files** — `src/akasha/daemon.py` (logging setup only) or a `logging` helper, `tests/unit/test_logging.py`.
- **Spec** — §3.
- **Steps** — (1) Emit one JSON object per line. (2) Configure a rotating file handler and a stderr handler. (3) Include timestamp, level, event fields.
- **Verify** — `uv run pytest tests/unit/test_logging.py`
- **DoD** — test captures a log record and asserts it parses as JSON with required keys.

### T0.7 — Makefile targets
- **Goal** — `make check`, `make battery`, `make run`, `make fmt` (spec §2).
- **Depends on** — T0.3, T0.4, T0.5, T0.6.
- **Files** — `Makefile`.
- **Spec** — §2, rule 0.7.
- **Steps** — (1) `fmt` = `ruff format`. (2) `check` = ruff + pyright + `pytest tests/unit tests/property`. (3) `battery` = `pytest tests/battery`. (4) `run` = launch daemon. (5) Add a placeholder `tests/unit/test_placeholder.py::test_ok`.
- **Verify** — `make check`
- **DoD** — `make check` exits 0 on both OSes (placeholder test passes).

### T0.8 — CI matrix (GitHub Actions)
- **Goal** — CI running `make check` on `[windows-latest, ubuntu-latest]`, Windows as the release gate.
- **Depends on** — T0.7.
- **Files** — `.github/workflows/ci.yml`.
- **Spec** — §3, §6.3.
- **Steps** — (1) Matrix over both OSes. (2) `uv sync` then `make check`. (3) Mark Windows required. (4) Reserve (commented) jobs for nightly battery/soak and OpenAPI-snapshot gate (wired later in M4/M5).
- **Verify** — push a branch; CI run for both OSes is green. Locally: `uv run python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml'))"`.
- **DoD** — workflow YAML parses; CI shows green `make check` on both matrix legs.

---

## M2 — IDs + canonicalization (Depends on: M0)

> Sequenced before M1 because the store (M1) mints IDs and hashes canonical bytes. M2 has no dependency on M1.

**Milestone DoD (spec):** `pytest tests/unit/test_ids.py tests/golden/test_serialization.py`; hypothesis round-trip `canonicalize(canonicalize(x)) == canonicalize(x)`.

### T2.1 — ID minting + checksum + validation (`ids.py`)
- **Goal** — Implement the 8-char base32 ID with weighted checksum exactly per spec.
- **Depends on** — T0.1.
- **Files** — `src/akasha/kernel/ids.py`, `tests/unit/test_ids.py`.
- **Spec** — §4.1 (verbatim `checksum` function; alphabet `A`; 7 core + 1 checksum; validation raises `E_ID_CHECKSUM`; mint retries collision, loop bound 10).
- **Steps** — (1) Define `A = "abcdefghijklmnopqrstuvwxyz234567"`. (2) Copy the `checksum(core)` function verbatim from §4.1. (3) `mint()` uses `secrets` for 7 core chars, appends checksum. (4) `validate(id)` checks length 8, alphabet membership, checksum match; on mismatch signal `E_ID_CHECKSUM` (never guess). (5) `contract_anchor(id) -> "^tm-"+id`. (6) DB-collision retry belongs to the store (T1.x), not here — expose a pure `mint()`.
- **Verify** — `uv run pytest tests/unit/test_ids.py`
- **DoD** — tests prove: minted IDs validate; a hand-crafted bad-checksum string raises `E_ID_CHECKSUM`; checksum matches §4.1 on ≥3 known vectors; anchor form is `^tm-<id8>`.

### T2.2 — Text + JSON canonicalization (`canonical.py`)
- **Goal** — The one and only text/JSON normalizer (spec rule: no other module normalizes text).
- **Depends on** — T0.1.
- **Files** — `src/akasha/kernel/canonical.py`, `tests/unit/test_canonical.py`.
- **Spec** — §4.3 (UTF-8, NFC, LF, no trailing whitespace, exactly one trailing newline, tabs only inside code fences; JSON `sort_keys, separators=(",",":"), ensure_ascii=False`; object hash = sha256 hex of canonical bytes).
- **Steps** — (1) `canonicalize_text(s)`: NFC-normalize, convert CRLF/CR→LF, strip trailing whitespace per line, expand tabs to spaces on managed lines but preserve tabs inside fenced code blocks, ensure exactly one trailing newline. (2) `canonical_json(obj)` per §4.3 byte spec. (3) `object_hash(bytes)` = sha256 hex. (4) Keep this module free of grammar knowledge except the minimal fence detection §4.3 requires.
- **Verify** — `uv run pytest tests/unit/test_canonical.py`
- **DoD** — unit cases cover CRLF, NFD→NFC, trailing whitespace, multiple/zero trailing newlines, tab-in-fence preservation; JSON output byte-equals the §4.3 example form.

### T2.3 — Canonicalization idempotence (property test)
- **Goal** — Prove `canonicalize(canonicalize(x)) == canonicalize(x)` for arbitrary text.
- **Depends on** — T2.2.
- **Files** — `tests/property/test_canonical_idempotent.py`.
- **Spec** — §4.3, §6.1.
- **Steps** — (1) Hypothesis strategy over unicode text incl. CRLF, NFD, emoji, tabs, fences. (2) Assert double-canonicalize equals single. (3) Assert output is valid UTF-8 NFC with single trailing newline.
- **Verify** — `uv run pytest tests/property/test_canonical_idempotent.py`
- **DoD** — property test passes with default hypothesis example count; no falsifying example.

### T2.4 — Golden serialization corpus (≥15 cases)
- **Goal** — Seed the byte-exact serialization fixtures required by the milestone DoD.
- **Depends on** — T2.2.
- **Files** — `tests/golden/serialization/<case>/{input.md,expected.md}` (≥15 cases), `tests/golden/test_serialization.py`.
- **Spec** — §4.3, M2 DoD (must include NFD input, CRLF input, trailing-whitespace, emoji, nested fences).
- **Steps** — (1) Create ≥15 case dirs covering at minimum: NFD, CRLF, trailing whitespace, emoji, nested fences, tabs-in-fence, mixed line endings, empty file, no-trailing-newline, multiple-trailing-newlines. (2) `expected.md` = `canonicalize_text(input.md)`. (3) Test loops cases and asserts byte-equality. (4) These are golden files — never edited to pass code (rule 0.3).
- **Verify** — `uv run pytest tests/golden/test_serialization.py`
- **DoD** — ≥15 cases present; every case's `canonicalize_text(input) == expected` byte-for-byte.

---

## M1 — Kernel store (Depends on: M0)

**Milestone DoD (spec):** `pytest tests/unit/kernel tests/property/test_store.py`; property suite includes no-dangling-refs, as-of, GC-safety; 10k-node benchmark `tests/integration/test_perf.py::test_neighborhood_p95` asserts p95 < 50 ms.

### T1.1 — DDL migration `001_init.sql` (verbatim)
- **Goal** — Create every table/index/vtable from §4.4 exactly as written.
- **Depends on** — T0.3.
- **Files** — `migrations/001_init.sql`, `tests/unit/kernel/test_schema.py`.
- **Spec** — §4.4 (DDL is verbatim — do not rename columns or alter types).
- **Steps** — (1) Paste the §4.4 DDL verbatim into `001_init.sql`. (2) Keep the partial indexes `ix_edges_dst/src ... WHERE retracted_at IS NULL`. (3) Include the `nodes_fts` fts5 vtable. (4) Do not add columns the spec does not list.
- **Verify** — `uv run pytest tests/unit/kernel/test_schema.py`
- **DoD** — after migration, `sqlite_master` contains all tables/indexes/vtable named in §4.4 with matching column names.

### T1.2 — pydantic models (`model.py`)
- **Goal** — Single-source-of-truth models for Node, Edge, Facet, and the Literal type aliases.
- **Depends on** — T0.1.
- **Files** — `src/akasha/kernel/model.py`, `tests/unit/kernel/test_model.py`.
- **Spec** — §4.2 (verbatim field lists; `NodeType`, `EdgeType`, `Maturity`, `ChangeClass` literals; justification-edge set; facet_binding rule).
- **Steps** — (1) Transcribe the §4.2 models exactly, including defaults (`facets=[]`, `mode="track"`, `status="live"`, etc.). (2) Encode the justification-edge set `{supports,contradicts,depends_on,derived_from,cites}` as a constant. (3) Add a validator: justification edges require `facet_binding` (facet_id or `"*"`); `None` allowed only for `composes`/`redirects_to`. (4) No DB access here.
- **Verify** — `uv run pytest tests/unit/kernel/test_model.py`
- **DoD** — models instantiate; the facet_binding validator rejects a `supports` edge with `facet_binding=None` and accepts a `composes` edge with `None`.

### T1.3 — Store: node create/read + commit DAG (`store.py`)
- **Goal** — `create_node`, `commit_node`, `get_node(as_of)`, `history` over the objects/commits DAG.
- **Depends on** — T1.1, T1.2, T2.1, T2.2.
- **Files** — `src/akasha/kernel/store.py`, `tests/unit/kernel/test_store_nodes.py`.
- **Spec** — §4.5, §4.4 (append-only objects; edits insert new object+commit and move `head_hash`), §4.1 (mint-collision retry loop bound 10).
- **Steps** — (1) `create_node`: mint id with DB-collision retry (bound 10 then error), insert canonical body into `objects`, insert `nodes` row + genesis `commit`. (2) `commit_node`: insert new object + commit with `parents`, `change_class`, `facets_touched`, `author`; move `head_hash`; all in one transaction. (3) `get_node(id, as_of=None)`: return head, or the commit live at an ISO timestamp. (4) `history(id)`: ordered commit list. (5) Every function transactional; docstring restates its invariant (§4.5).
- **Verify** — `uv run pytest tests/unit/kernel/test_store_nodes.py`
- **DoD** — round-trip create→get returns canonical body; commit moves head and preserves parent; `get_node(as_of=<past>)` returns the older object; objects table rows are never mutated.

### T1.4 — Store: edges create/retract + neighborhood/search
- **Goal** — `create_edge`, `retract_edge`, `neighborhood(id,hops=1)`, `search(q)` (FTS).
- **Depends on** — T1.3.
- **Files** — `src/akasha/kernel/store.py`, `tests/unit/kernel/test_store_edges.py`.
- **Spec** — §4.5, §4.2 (facet_binding validation), §4.4 (`nodes_fts`, partial edge indexes).
- **Steps** — (1) `create_edge`: validate facet_binding rule (T1.2) before insert; set `created_at`. (2) `retract_edge`: set `retracted_at` (never delete row). (3) Keep `nodes_fts` in sync with body on create/commit. (4) `neighborhood(id, hops=1)`: live edges only. (5) `search(q)`: FTS over bodies.
- **Verify** — `uv run pytest tests/unit/kernel/test_store_edges.py`
- **DoD** — creating an invalid justification edge is rejected; retract sets `retracted_at` and drops the edge from neighborhood; search returns a node by a body term.

### T1.5 — Maturity derivation (`maturity.py`)
- **Goal** — Derive S0–S4 exactly per §4.6, recomputed in the mutation transaction.
- **Depends on** — T1.4.
- **Files** — `src/akasha/kernel/maturity.py`, `tests/unit/kernel/test_maturity.py`.
- **Spec** — §4.6 (S1 inbound≥1; S2 adds type set + facets≥1 for non task/entity; S3 adds inbound justification from evidence/proof; S4 vetted flag; highest satisfied stage).
- **Steps** — (1) Pure function `derive(node, inbound_edges, ...) -> Maturity`. (2) Wire it so `store` recomputes maturity inside any transaction that can change the inputs. (3) Handle the task/entity exception for S2.
- **Verify** — `uv run pytest tests/unit/kernel/test_maturity.py`
- **DoD** — table-driven cases hit each of S0–S4 and the boundary just below each; a definition with 1 inbound + type + 1 facet computes S2, and S3 only with an evidence/proof justification edge.

### T1.6 — Deletion, tombstone, redirects, split/merge
- **Goal** — `delete_node` (S0 hard / S1+ tombstone+redirect), `split_node`, `merge_nodes` returning redirects.
- **Depends on** — T1.5.
- **Files** — `src/akasha/kernel/store.py`, `src/akasha/kernel/commits.py`, `tests/unit/kernel/test_store_lifecycle.py`.
- **Spec** — §4.5, §4.6 (S0 hard-delete; S1+ require redirect successors or explicit tombstone else `409 E_NEEDS_REDIRECT` at API layer), §4.4 `redirects` table.
- **Steps** — (1) `delete_node`: if S0, hard delete object+node; if S1+, require `redirect_to` successors or explicit tombstone; else raise `E_NEEDS_REDIRECT` (surfaced as 409 later). (2) `split_node(id, parts)` and `merge_nodes(ids)` insert `redirects` rows and reassign references. (3) `commits.py` holds change-class + `facets_touched` helpers.
- **Verify** — `uv run pytest tests/unit/kernel/test_store_lifecycle.py`
- **DoD** — S0 delete removes rows; S1+ delete without redirect raises `E_NEEDS_REDIRECT`; split/merge create redirect rows and leave zero dangling references.

### T1.7 — S0 garbage collection job
- **Goal** — GC that deletes objects unreachable from any S1+ node or base snapshot; never removes referenced objects.
- **Depends on** — T1.6.
- **Files** — `src/akasha/kernel/store.py` (GC section), `tests/unit/kernel/test_gc.py`.
- **Spec** — §4.4 (append-only except S0 GC), §4.5 (GC job invariant).
- **Steps** — (1) Compute the reachable set from S1+ heads/history and base snapshots. (2) Delete only unreachable `objects` rows. (3) Never touch referenced objects.
- **Verify** — `uv run pytest tests/unit/kernel/test_gc.py`
- **DoD** — GC removes an orphaned S0 object but leaves every object referenced by an S1+ node or base snapshot.

### T1.8 — Store property suite
- **Goal** — Hypothesis invariants: no dangling edges, head always reachable, as-of correctness, S0-GC safety.
- **Depends on** — T1.7.
- **Files** — `tests/property/test_store.py`.
- **Spec** — §4.5 (property tests enumerated), §6.1.
- **Steps** — (1) Generate random sequences of store operations. (2) After each, assert: no edge points to a missing node; every node head is reachable in its DAG; `get_node(as_of)` matches the commit live at that time; GC never removes a referenced object.
- **Verify** — `uv run pytest tests/property/test_store.py`
- **DoD** — all four invariants hold with no falsifying example.

### T1.9 — 10k-node neighborhood benchmark
- **Goal** — p95 of `neighborhood(id, hops=1)` under a 10k-node synthetic graph is < 50 ms.
- **Depends on** — T1.8.
- **Files** — `tests/integration/test_perf.py`.
- **Spec** — §4.11 note, M1 DoD.
- **Steps** — (1) Seed 10k nodes + realistic edges. (2) Sample neighborhood latency across many random nodes. (3) Assert p95 < 50 ms. (4) Ensure the partial edge indexes (T1.1) exist.
- **Verify** — `uv run pytest tests/integration/test_perf.py::test_neighborhood_p95`
- **DoD** — measured p95 < 50 ms on CI hardware.

---

## M3 — Contract parser / renderer (Depends on: M2)

**Milestone DoD (spec):** golden corpus ≥25 cases; hypothesis `render(parse(D)) == D` for in-contract docs D and `parse(render(G)) == G` for hub graphs G; committed fuzz corpus under `tests/golden/serialization/fuzz/`.

### T3.1 — Grammar tokens + contract version (`grammar.py`)
- **Goal** — Encode the §4.7 tokens/regexes and the contract version constant.
- **Depends on** — T2.1, T2.2.
- **Files** — `src/akasha/contract/grammar.py`, `tests/unit/contract/test_grammar.py`.
- **Spec** — §4.7 (EBNF: `anchor`, `managed_par`, `task_line`, `new_line`, `embed`, `ref`, `indent`; front-matter `tm: 1`; nesting depth = indent/2; fenced code ignored).
- **Steps** — (1) Define regexes for each token exactly per the EBNF. (2) Define `CONTRACT_VERSION = 1`. (3) Anchor must be at end-of-line to count (mid-line matches are plain text). (4) No parsing logic here — tokens/patterns only.
- **Verify** — `uv run pytest tests/unit/contract/test_grammar.py`
- **DoD** — token regexes match the positive examples and reject the negatives (mid-line anchor, fenced anchor) named in §4.7.

### T3.2 — Parser: vault text → BlockSet (`parser.py`)
- **Goal** — Parse a managed file into anchored blocks + task structure.
- **Depends on** — T3.1.
- **Files** — `src/akasha/contract/parser.py`, `tests/unit/contract/test_parser.py`.
- **Spec** — §4.7 (managed only if `tm: 1`; task_line ⇔ task node; indented task ⇒ `composes(parent→child)`; `^tm-new` request; embeds read-only; fenced code ignored entirely; anchor pattern not at EOL is plain text).
- **Steps** — (1) Skip files lacking front-matter `tm: 1` (return empty/unmanaged). (2) Emit a BlockSet keyed by anchor id: paragraphs, tasks (with `- [x]`⇔done), nesting depth from indent. (3) Record `^tm-new` requests. (4) Skip fenced code entirely. (5) Do not normalize text here — rely on canonical.py.
- **Verify** — `uv run pytest tests/unit/contract/test_parser.py`
- **DoD** — parses managed paragraphs/tasks/embeds/refs; ignores fenced anchors; derives `composes` parent→child from indentation; treats unmanaged files as empty.

### T3.3 — Renderer: hub nodes → canonical vault text (`render.py`)
- **Goal** — Deterministic canonical projection of hub state into vault markdown.
- **Depends on** — T3.2, T1.3 (read-only hub access).
- **Files** — `src/akasha/contract/render.py`, `tests/unit/contract/test_render.py`.
- **Spec** — §4.7, §4.3 (output must already be canonical), §1 (sync writes only canonical renders).
- **Steps** — (1) Render each managed node to `text SP anchor EOL` / task form. (2) Render nesting via 2-space indent. (3) Embeds render the target's current head body (read-only). (4) Output passes `canonicalize_text` unchanged.
- **Verify** — `uv run pytest tests/unit/contract/test_render.py`
- **DoD** — rendered output is byte-identical to its own canonicalization; anchors use `^tm-` form.

### T3.4 — Round-trip property tests
- **Goal** — `render(parse(D)) == D` and `parse(render(G)) == G`.
- **Depends on** — T3.3.
- **Files** — `tests/property/test_contract_roundtrip.py`.
- **Spec** — §4.7, M3 DoD, §6.1.
- **Steps** — (1) Strategy for in-contract documents D. (2) Strategy for hub graphs G. (3) Assert both round-trips hold. (4) Shrink failures into the fuzz corpus (T3.7).
- **Verify** — `uv run pytest tests/property/test_contract_roundtrip.py`
- **DoD** — both round-trip properties pass with no falsifying example.

### T3.5 — Linter: violation codes + certain-repair (`linter.py`)
- **Goal** — Detect §4.7 violations and apply only the certain auto-repairs.
- **Depends on** — T3.2.
- **Files** — `src/akasha/contract/linter.py`, `tests/unit/contract/test_linter.py`.
- **Spec** — §4.7 (codes `E_ID_CHECKSUM`, `E_DUP_ID`, `E_LOST_ANCHOR`, `E_DELETED_S1`, advisory `W_UNMANAGED_ANCHOR`; certain-repairs; everything else ⇒ review item).
- **Steps** — (1) Detect each code per §4.7. (2) Certain-repair only: `E_LOST_ANCHOR` where text is byte-identical to base except the anchor ⇒ re-insert anchor; `E_DUP_ID` where one copy is byte-identical to base ⇒ identical keeps id, other proposed `^tm-new`. (3) All repairs logged + undoable. (4) Everything uncertain ⇒ review item (no guessing).
- **Verify** — `uv run pytest tests/unit/contract/test_linter.py`
- **DoD** — each violation code is raised on its fixture; only the two certain-repair cases auto-fix; ambiguous cases produce a review item, never a guess.

### T3.6 — Pause & diff (formatter-storm guard)
- **Goal** — If violations affect >25% of a file's managed blocks in one cycle, make no writes, snapshot, open one review item with a diff.
- **Depends on** — T3.5.
- **Files** — `src/akasha/contract/linter.py` (threshold), `tests/unit/contract/test_pause_and_diff.py`.
- **Spec** — §4.7 (pause & diff), §4.8 (`pause_threshold`).
- **Steps** — (1) Compute the affected-block ratio. (2) If >25%, return a pause decision: no writes, snapshot the file, single review item carrying a diff. (3) Deterministic — no side effects beyond the snapshot/review.
- **Verify** — `uv run pytest tests/unit/contract/test_pause_and_diff.py`
- **DoD** — a fixture where >25% blocks change triggers pause (zero writes, one review item); a 24% case does not.

### T3.7 — Golden corpus (≥25) + committed fuzz corpus
- **Goal** — Satisfy the M3 golden requirement and commit the fuzz corpus.
- **Depends on** — T3.4, T3.5.
- **Files** — `tests/golden/serialization/<case>/{input.md,expected.md}` (grow to ≥25 total), `tests/golden/serialization/fuzz/**`, `tests/golden/test_serialization.py` (extend).
- **Spec** — §4.7, M3 DoD, §6.3 golden-file-change gate.
- **Steps** — (1) Add cases for tasks, nesting, embeds, refs, `^tm-new`, each violation code, pause&diff. (2) Commit shrunk hypothesis failures under `fuzz/`. (3) Reach ≥25 total cases. (4) Golden files are sacred (rule 0.3).
- **Verify** — `uv run pytest tests/golden/test_serialization.py`
- **DoD** — ≥25 golden cases pass byte-exact; fuzz corpus committed and green.

---

## M4 — Daemon + API + CLI core (Depends on: M1, M3)

**Milestone DoD (spec):** `pytest tests/integration/test_api.py tests/integration/test_cli.py`; `test_agent_writes_become_proposals`; OpenAPI snapshot-diff CI job green. (Sync endpoints excluded — they land in M5.)

### T4.1 — Auth: token classes, secrets, rate limits (`auth.py`)
- **Goal** — Human/agent token classes with hashed secrets and per-token rate limits.
- **Depends on** — T1.1 (tokens table), T0.5.
- **Files** — `src/akasha/api/auth.py`, `tests/unit/api/test_auth.py`.
- **Spec** — §4.11 (Bearer token; agent-class mutations become proposals unless endpoint marked ∅; per-token rate limit), §4.4 `tokens` table.
- **Steps** — (1) Verify `Authorization: Bearer <token>` against `tokens.secret_hash`. (2) Expose token `class` (`human`|`agent`) and `rate_per_min`. (3) Enforce rate limit per token. (4) Reject revoked tokens.
- **Verify** — `uv run pytest tests/unit/api/test_auth.py`
- **DoD** — valid token authenticates; revoked token rejected; exceeding `rate_per_min` returns a rate-limit error; class is exposed to routes.

### T4.2 — Audit log
- **Goal** — Append `(ts, token_id, action, detail)` to `audit_log` on every mutating action.
- **Depends on** — T4.1.
- **Files** — `src/akasha/api/auth.py` or middleware, `src/akasha/kernel/store.py` (minimal append helper required by rule 5), `tests/unit/api/test_audit.py`.
- **Spec** — §4.4 `audit_log`, §4.11.
- **Steps** — (1) Middleware/decorator records each mutation. (2) Never log secrets. (3) Append-only.
- **Verify** — `uv run pytest tests/unit/api/test_audit.py`
- **DoD** — a mutating request writes exactly one audit row with token id + action; reads write none.

### T4.3 — FastAPI factory + `/health` + schemas re-export (`app.py`, `schemas.py`)
- **Goal** — App factory bound to 127.0.0.1:7433 with an unauthenticated `/health`; `schemas.py` re-exports kernel models.
- **Depends on** — T4.1, T1.2.
- **Files** — `src/akasha/api/app.py`, `src/akasha/api/schemas.py`, `tests/integration/test_health.py`.
- **Spec** — §4.11 (`GET /health` no auth: liveness, version, contract version), §3 (bind 127.0.0.1), §8 (`schemas.py` re-exportable — Phase 4 hook).
- **Steps** — (1) `create_app()` factory. (2) `/health` returns version + `CONTRACT_VERSION`, no auth. (3) `schemas.py` re-exports kernel `model.py` types (no divergence). (4) Bind localhost only.
- **Verify** — `uv run pytest tests/integration/test_health.py`
- **DoD** — `/health` returns 200 with version + contract version and requires no auth; app binds 127.0.0.1.

### T4.4 — Node routes (`routes/nodes.py`)
- **Goal** — `GET/POST/PATCH/DELETE /nodes`, history, neighborhood, split/merge, vet.
- **Depends on** — T4.3, T1.6.
- **Files** — `src/akasha/api/routes/nodes.py`, `tests/integration/test_api.py` (node cases).
- **Spec** — §4.11 rows for `/nodes*` (incl. `?as_of=`, `DELETE` 409 `E_NEEDS_REDIRECT`, `/vet` human-only ∅, split/merge return redirect + reassignment queue).
- **Steps** — (1) Wire each endpoint to store functions. (2) `GET /nodes/{id}` includes maturity and supports `?as_of=ISO`. (3) `DELETE` returns 409 `E_NEEDS_REDIRECT` for S1+ without `redirect_to`. (4) `/vet` restricted to human tokens (∅, not proposalized). (5) Use the standard error envelope `{"error":{code,message,detail}}`.
- **Verify** — `uv run pytest tests/integration/test_api.py -k nodes`
- **DoD** — CRUD + as_of + history + neighborhood work; S1+ delete without redirect → 409 `E_NEEDS_REDIRECT`; vet from an agent token is rejected.

### T4.5 — Edge, search, token, sync-root routes
- **Goal** — `POST/DELETE /edges`, `GET /search`, `GET/POST/DELETE /tokens`, `GET/POST /sync/roots`.
- **Depends on** — T4.4, T1.4.
- **Files** — `src/akasha/api/routes/{edges.py,search.py,tokens.py,sync_roots.py}`, `src/akasha/kernel/store.py` (minimal token/sync-root helpers required by rule 5), `tests/integration/test_api.py`.
- **Spec** — §4.11 (`/edges` validates facet_binding rule; `/tokens` and `/sync/roots` human-only ∅; `/search` FTS).
- **Steps** — (1) `POST /edges` validates facet_binding (T1.2). (2) `/search` returns FTS hits. (3) `/tokens` and `/sync/roots` are human-only ∅. (4) `/sync/roots` registers/lists durable filesystem roots; watching arrives in M5.
- **Verify** — `uv run pytest tests/integration/test_api.py -k "edges or search or tokens or sync_roots"`
- **DoD** — edge creation enforces facet_binding; search returns hits; token/sync-root mutation from an agent token is rejected.

### T4.6 — Agent-token proposal rewriting
- **Goal** — Agent-class mutations to non-∅ endpoints become review items `cause_kind=proposal`.
- **Depends on** — T4.4, T4.5.
- **Files** — `src/akasha/api/routes/*` (shared dependency), `src/akasha/api/deps.py`, `src/akasha/kernel/store.py` (minimal review helper required by rule 5), `tests/integration/test_api.py::test_agent_writes_become_proposals`.
- **Spec** — §4.11 (agent mutations rewritten to proposals unless ∅), §8 (reserve `cause_kind=proposal`).
- **Steps** — (1) A shared dependency intercepts agent-class mutations on non-∅ endpoints. (2) Instead of mutating, enqueue a review item `cause_kind=proposal`. (3) Human tokens mutate directly. (4) ∅ endpoints reject agent tokens outright.
- **Verify** — `uv run pytest tests/integration/test_api.py::test_agent_writes_become_proposals`
- **DoD** — an agent `POST /nodes` creates a `cause_kind=proposal` review item and does **not** mutate; the same call as a human mutates directly.

### T4.7 — OpenAPI snapshot + CI gate
- **Goal** — Freeze `docs/api-snapshot/openapi.json`; CI fails if the served spec diverges without an intentional snapshot update.
- **Depends on** — T4.6.
- **Files** — `docs/api-snapshot/openapi.json`, `tests/integration/test_openapi_snapshot.py`, `.github/workflows/ci.yml` (enable the gate job).
- **Spec** — §4.11, §6.3 (OpenAPI-snapshot diff gate), PRD §7.12 rule 1.
- **Steps** — (1) Generate served OpenAPI and write the snapshot. (2) Test compares served spec to snapshot. (3) CI job fails on divergence unless snapshot changed in the same PR. (4) Treat the snapshot as a migration contract (rule 0.3 / §8).
- **Verify** — `uv run pytest tests/integration/test_openapi_snapshot.py`
- **DoD** — served spec equals snapshot; a deliberate route change fails the test until the snapshot is regenerated in the same change.

### T4.8 — CLI verbs (`cli/main.py`, typer)
- **Goal** — `new/get/set/rm/search/review/token` verbs, global flags, exit codes.
- **Depends on** — T4.5.
- **Files** — `src/akasha/cli/main.py`, `tests/integration/test_cli.py`.
- **Spec** — §4.12 (verbs, `--json` schema `cli/v1` additive-only, `--dry-run` returns would-be request, `--token`; exit codes 0/1/2/3/4).
- **Steps** — (1) Implement each verb as an API client call. (2) `--json` emits versioned `cli/v1` output. (3) `--dry-run` returns the would-be request without mutating. (4) Map exit codes: 0 ok, 1 error, 2 usage, 3 not found, 4 conflict/violation/needs-redirect.
- **Verify** — `uv run pytest tests/integration/test_cli.py`
- **DoD** — each verb round-trips against a live test daemon; `--dry-run` mutates nothing; exit codes match §4.12 (e.g. deleting a missing node → 3, needs-redirect → 4).

### T4.9 — Daemon lifecycle + single-instance lock + autostart docs
- **Goal** — `akasha daemon` process with single-instance lock; Windows autostart docs.
- **Depends on** — T4.3, T4.8.
- **Files** — `src/akasha/daemon.py`, `src/akasha/cli/main.py` (`daemon` verb), `docs/autostart-windows.md` (+ Task Scheduler XML, NSSM notes), `tests/integration/test_daemon_lock.py`.
- **Spec** — §4.12 (`akasha daemon`), M4 (single-instance lock; Task Scheduler XML + NSSM instructions in `docs/`).
- **Steps** — (1) Acquire a single-instance lock on startup; second instance exits cleanly with a clear message. (2) Clean shutdown releases the lock. (3) Write autostart docs with a Task Scheduler XML sample and NSSM steps.
- **Verify** — `uv run pytest tests/integration/test_daemon_lock.py`
- **DoD** — second daemon instance refuses to start while the first holds the lock; docs file exists with XML + NSSM sections.

### T4.10 — Durable sync-root registry + terminology refinement
- **Goal** — Replace ephemeral `/vaults` registration with durable `/sync/roots` state and precise hub/spoke/sync-root terminology.
- **Depends on** — T4.9.
- **Files** — `migrations/002_refine_m4_contract.sql`, `src/akasha/api/routes/{vaults.py,sync_roots.py}`, `src/akasha/api/app.py`, `src/akasha/kernel/store.py`, `tests/unit/kernel/test_schema.py`, `tests/integration/test_api.py`, `docs/api-snapshot/openapi.json`.
- **Spec** — §4.4 `sync_roots`/`sync_files`, §4.11 `/sync/roots`, vision §7.8–§7.9 (registered watched roots survive daemon restart).
- **Steps** — (1) Add the forward schema refinement and preserve existing rows. (2) Persist registration only through store helpers. (3) remove the in-memory registry and old `/vaults` route. (4) Regenerate OpenAPI. (5) Prove a registration is visible from a newly-created app/connection after restart.
- **Verify** — `uv run pytest tests/unit/kernel/test_schema.py tests/integration/test_api.py -k "schema or sync_roots" && uv run pytest tests/integration/test_openapi_snapshot.py`
- **DoD** — `/v1/sync/roots` is human-only, durable before any file sync, and the old `/v1/vaults` route is absent.

### T4.11 — Create-proposal identity refinement
- **Goal** — Make create-node proposals subjectless until approval; never reserve a node id for an unapproved proposal.
- **Depends on** — T4.10, T4.6.
- **Files** — `migrations/002_refine_m4_contract.sql`, `src/akasha/kernel/store.py`, `src/akasha/api/deps.py`, `src/akasha/api/routes/nodes.py`, `tests/unit/kernel/test_schema.py`, `tests/integration/test_api.py`, `docs/api-snapshot/openapi.json`.
- **Spec** — §4.4 nullable `review_queue.node_id`; §4.11 canonical proposal envelope and mint-on-approval rule.
- **Steps** — (1) Permit `node_id=NULL` only where no existing node exists. (2) Remove unassigned node-id minting. (3) Agent `POST /nodes` queues `{method,path,body}` with no node mutation or reservation. (4) Keep existing-node and edge proposals associated with their affected node (`dst` for edges). (5) T7.5 approval invokes `create_node`, which mints the real id.
- **Verify** — `uv run pytest tests/unit/kernel/test_schema.py tests/integration/test_api.py::test_agent_writes_become_proposals`
- **DoD** — create proposal has `node_id=NULL`, canonical recoverable payload, and no `nodes` row; all other proposal targets remain unchanged.

---

## M5 — Sync engine (Depends on: M4, T4.10)

**Milestone DoD (spec):** `make battery` — the scripted edit battery §6.2 passes 100% with 0 silent guesses; `test_crash_recovery_idempotent` converges on restart.

### T5.1 — Base store (per-file snapshots)
- **Goal** — Store/retrieve the last-agreed canonical bytes per file (`base_store.py`).
- **Depends on** — T1.1 (objects table), T2.2, T4.10.
- **Files** — `src/akasha/sync/base_store.py`, `tests/unit/sync/test_base_store.py`.
- **Spec** — §4.8 (`B = base_store.get(path)`), §4.4 (`objects` blob table; `sync_files.base_hash`).
- **Steps** — (1) `put(sync_root_id, path, bytes)` validates the durable sync root, stores canonical bytes as an object, and upserts `sync_files(sync_root_id, base_hash, ...)`. (2) `get(path)` returns last-agreed bytes or None. (3) Never store non-canonical bytes.
- **Verify** — `uv run pytest tests/unit/sync/test_base_store.py`
- **DoD** — put/get round-trips canonical bytes and retains its sync-root association; a fresh path returns None; an unknown sync-root id is rejected.

### T5.2 — Origin / echo-suppression (`origin.py`)
- **Goal** — Record daemon writes so watcher events matching them are dropped.
- **Depends on** — T5.1.
- **Files** — `src/akasha/sync/origin.py`, `tests/unit/sync/test_origin.py`.
- **Spec** — §4.8 (echo suppression: record `(path, hash)`; drop watcher event whose content hash matches a recorded write).
- **Steps** — (1) `record_write(path, hash)`. (2) `is_echo(path, hash)` true if it matches a recent recorded write, then consume it. (3) Bounded memory (recent writes only).
- **Verify** — `uv run pytest tests/unit/sync/test_origin.py`
- **DoD** — a recorded write's matching event is suppressed once; a different hash is not suppressed.

### T5.3 — Watcher: debounce + cloud-path detection (`watcher.py`)
- **Goal** — watchdog observer with 500 ms debounce and OneDrive/Dropbox detection (warn + conservative profile).
- **Depends on** — T5.2.
- **Files** — `src/akasha/sync/watcher.py`, `tests/unit/sync/test_watcher.py`.
- **Spec** — §4.8 (500 ms debounce), M5 (cloud-path detection under OneDrive/Dropbox markers ⇒ warn + conservative profile), §6.2 E18/E19.
- **Steps** — (1) Load all durable sync roots and watch their `root_path`s. (2) Debounce rapid bursts into a single cycle (500 ms). (3) Detect cloud markers in an Obsidian vault path; when present, log a warning and enable a conservative profile flag. (4) Route change events to reconcile.
- **Verify** — `uv run pytest tests/unit/sync/test_watcher.py`
- **DoD** — a burst of N events yields one cycle (E18); a simulated OneDrive path sets the warning + conservative flag (E19).

### T5.4 — Reconcile pipeline (`reconcile.py`)
- **Goal** — The per-file three-way merge of §4.8 exactly.
- **Depends on** — T5.1, T5.2, T5.3, T3.5, T1.3.
- **Files** — `src/akasha/sync/reconcile.py`, `tests/unit/sync/test_reconcile.py`.
- **Spec** — §4.8 (full pseudocode: quiet/hub-only shortcuts; `diff_blocks` op kinds modified|created|deleted|moved|checkbox_toggled|reparented; pause threshold; apply certain-repairs; conflict on both-sides edit; canonical write-back; `base_store.put`).
- **Steps** — (1) Implement the pipeline verbatim to the pseudocode order. (2) `diff_blocks(base, current)` keyed by anchor id producing the six op kinds. (3) Apply ops only via the store API with `origin='sync'`. (4) Both-sides edit ⇒ conflict queue (T5.5). (5) Canonical write-back + `base_store.put(sync_root_id, path, bytes)`. (6) **Zero silent guesses** — anything uncertain becomes a review item.
- **Verify** — `uv run pytest tests/unit/sync/test_reconcile.py`
- **DoD** — golden reconcile cases (`tests/golden/reconcile/<case>/`) produce the `expected.md` and `expected_ops.json`; quiet and hub-only shortcuts short-circuit; no op is applied outside the store API.

### T5.5 — Conflict branching
- **Goal** — On both-sides edit, hub keeps both versions as commit-DAG branches; open `cause_kind=conflict` review.
- **Depends on** — T5.4, T1.3.
- **Files** — `src/akasha/sync/reconcile.py`, `src/akasha/kernel/commits.py`, `tests/integration/test_conflict.py`.
- **Spec** — §4.8 (conflict semantics: both versions as branches; review item `cause_kind=conflict`), §6.2 E12.
- **Steps** — (1) Detect `hub_changed_since(base, node)` alongside a vault edit. (2) Record both as branches on the node's DAG. (3) Enqueue one `cause_kind=conflict` review item. (4) No data loss on either side.
- **Verify** — `uv run pytest tests/integration/test_conflict.py`
- **DoD** — E12 scenario yields two DAG branches + one conflict review item; both bodies remain retrievable.

### T5.6 — Startup reconcile / crash recovery
- **Goal** — Run `on_change` for every managed file at startup (idempotent = crash recovery).
- **Depends on** — T5.4.
- **Files** — `src/akasha/sync/reconcile.py` (startup entry), `src/akasha/daemon.py`, `tests/integration/test_crash_recovery.py`.
- **Spec** — §4.8 (startup reconcile idempotent), M5 DoD (`test_crash_recovery_idempotent`), §6.2 E11.
- **Steps** — (1) On daemon start, reconcile every managed file. (2) Ensure a second run makes no further writes (idempotent). (3) Simulate kill-mid-sync and restart.
- **Verify** — `uv run pytest tests/integration/test_crash_recovery.py::test_crash_recovery_idempotent`
- **DoD** — killing the daemon mid-sync then restarting converges to a stable canonical state with no lost blocks (E11).

### T5.7 — Sync API routes (`routes/sync.py`)
- **Goal** — `GET /sync/status`, `POST /sync/rescan` (deferred from M4).
- **Depends on** — T5.4, T4.3.
- **Files** — `src/akasha/api/routes/sync.py`, `tests/integration/test_api.py` (sync cases), `docs/api-snapshot/openapi.json` (regenerate in same PR).
- **Spec** — §4.11 (`/sync/status`, `/sync/rescan`: per-sync-root state, violations, pauses).
- **Steps** — (1) `/sync/status` returns per-sync-root state, violation list, pause info. (2) `/sync/rescan` triggers a full reconcile. (3) Regenerate the OpenAPI snapshot in the same change (T4.7 gate).
- **Verify** — `uv run pytest tests/integration/test_api.py -k sync && uv run pytest tests/integration/test_openapi_snapshot.py`
- **DoD** — status reports violations/pauses; rescan converges; snapshot gate green.

### T5.8 — Golden reconcile fixtures + scripted edit battery E01–E20
- **Goal** — The full §6.2 battery passing 100% with zero silent guesses.
- **Depends on** — T5.5, T5.6, T5.7.
- **Files** — `tests/golden/reconcile/<case>/{base.md,vault.md,hub.json,expected.md,expected_ops.json}`, `tests/battery/test_edit_battery.py`.
- **Spec** — §6.2 (E01–E20, each with its expected behavior), §4.8, §4.7.
- **Steps** — (1) Build a fixture for each of E01–E20. (2) Assert the expected op/violation/repair and zero silent guesses. (3) Wire into `make battery`. (4) Fixtures are golden (rule 0.3).
- **Verify** — `make battery`
- **DoD** — E01–E20 all pass; a counter asserts silent-guess count == 0 across the battery.

---

## M6 — Obsidian plugin (Depends on: M5)

**Milestone DoD (spec):** `plugin-obsidian/TESTPLAN.md` executed against a demo vault; plugin build green in CI.

### T6.1 — Plugin scaffold + build in CI
- **Goal** — TypeScript Obsidian plugin project that builds.
- **Depends on** — T0.8.
- **Files** — `plugin-obsidian/{manifest.json,package.json,tsconfig.json,src/main.ts,esbuild.config.mjs}`, `.github/workflows/ci.yml` (add plugin build job).
- **Spec** — M6 (TS thin client; plugin build in CI).
- **Steps** — (1) Standard Obsidian plugin scaffold. (2) `npm run build` produces `main.js`. (3) Add a CI job for the build.
- **Verify** — `cd plugin-obsidian && npm ci && npm run build`
- **DoD** — build produces `main.js`; CI plugin-build job green.

### T6.2 — Settings (URL + token)
- **Goal** — Settings tab storing daemon URL and API token.
- **Depends on** — T6.1.
- **Files** — `plugin-obsidian/src/settings.ts`, `plugin-obsidian/src/main.ts`.
- **Spec** — M6 (settings: URL+token).
- **Steps** — (1) Settings tab with URL + token fields. (2) Persist via Obsidian settings API. (3) Use them for API calls.
- **Verify** — Manual per `TESTPLAN.md` (set URL+token, confirm persistence) + `npm run build`.
- **DoD** — settings persist across reload; API client reads them.

### T6.3 — Status bar (sync state + violation count)
- **Goal** — Status-bar item polling `/sync/status`.
- **Depends on** — T6.2, T5.7.
- **Files** — `plugin-obsidian/src/statusbar.ts`, `plugin-obsidian/src/main.ts`.
- **Spec** — M6 (status bar: sync state, violation count).
- **Steps** — (1) Poll `/sync/status`. (2) Show sync state + violation count. (3) Handle daemon-down gracefully.
- **Verify** — Manual per `TESTPLAN.md` against a running daemon.
- **DoD** — status bar reflects live sync state and violation count; degrades cleanly when daemon is down.

### T6.4 — Command: create node from selection
- **Goal** — Wrap the selection and append `^tm-new` so the daemon mints an id.
- **Depends on** — T6.2.
- **Files** — `plugin-obsidian/src/commands.ts`, `plugin-obsidian/src/main.ts`.
- **Spec** — M6 (command "create node from selection"), §4.7 (`^tm-new` ⇒ mint + rewrite, origin-tagged).
- **Steps** — (1) Command wraps the selected text and appends `^tm-new` at EOL. (2) Save; the daemon mints and rewrites. (3) Do not mint client-side.
- **Verify** — Manual per `TESTPLAN.md` (select text, run command, observe daemon rewrite to `^tm-<id8>`, no echo).
- **DoD** — running the command yields a minted anchor via the daemon with no echo loop (matches battery E08 behavior).

### T6.5 — Clipboard cut/copy carrying anchors + TESTPLAN
- **Goal** — Preserve anchors on cut/copy so paste behaves per contract; write the manual test plan.
- **Depends on** — T6.4.
- **Files** — `plugin-obsidian/src/clipboard.ts`, `plugin-obsidian/TESTPLAN.md`.
- **Spec** — M6 (clipboard cut/copy carrying anchors), §4.7 (`E_DUP_ID` on copy-without-cut).
- **Steps** — (1) Ensure cut/copy retains anchors. (2) Document the full manual test script covering T6.2–T6.5 in `TESTPLAN.md`. (3) Cross-reference battery E04 (cut-paste) and E05 (copy-paste duplicate).
- **Verify** — Execute `TESTPLAN.md` end-to-end against the demo vault.
- **DoD** — cut-paste moves a block cleanly (E04); copy-paste raises `E_DUP_ID` path (E05); `TESTPLAN.md` steps all pass.

---

## M7 — TMS loop (Depends on: M4)

**Milestone DoD (spec):** `pytest tests/integration/test_tms.py` covering: major commit flags exactly the bound subscribers; `*`-binding flagged on any break; supertask trigger fires once and never auto-closes; split leaves zero dangling references.

### T7.1 — Invalidation walk (`invalidate.py`)
- **Goal** — Implement the §4.9 interface-break walk with the non-transitive damper.
- **Depends on** — T1.6, T1.5.
- **Files** — `src/akasha/tms/invalidate.py`, `tests/unit/tms/test_invalidate.py`.
- **Spec** — §4.9 (trigger on `change_class=="major"`; heuristic default; subscriber selection over justification|composes edges with facet_binding in touched or `*`; damper `already_unresolved_stale`; retraction always major touching all facets).
- **Steps** — (1) Select subscriber edges exactly per the §4.9 predicate. (2) Enqueue `cause='facet_break'` reviews for each `src` not already unresolved-stale (damper). (3) Handle `composes_touched_facet`. (4) Node retraction = major touching all facets.
- **Verify** — `uv run pytest tests/unit/tms/test_invalidate.py`
- **DoD** — a major commit flags exactly the bound subscribers; `*`-bound subscribers flag on any break; the damper prevents duplicate stale entries.

### T7.2 — Change-class heuristic + wiring into commit
- **Goal** — Default `major` iff a facet was removed/renamed or a touched facet's `version` bumped; retraction always major.
- **Depends on** — T7.1, T1.3.
- **Files** — `src/akasha/kernel/commits.py`, `src/akasha/tms/invalidate.py`, `tests/unit/tms/test_change_class.py`.
- **Spec** — §4.9 (heuristic default), §4.2 (facet `version`).
- **Steps** — (1) Compute default change_class from the facet delta. (2) Allow UI/CLI to override. (3) Trigger `invalidate` on major commits within the mutation transaction.
- **Verify** — `uv run pytest tests/unit/tms/test_change_class.py`
- **DoD** — facet removal/rename or version bump yields `major`; a pure body typo yields `patch`; override respected.

### T7.3 — Trigger registry + evaluator (`triggers.py`)
- **Goal** — Pure-function condition registry evaluated after commits and on a daily tick; sole action `enqueue_review`.
- **Depends on** — T7.1.
- **Files** — `src/akasha/tms/triggers.py`, `tests/unit/tms/test_triggers.py`.
- **Spec** — §4.10 (conditions `all_subtasks_closed`, `facet_interface_changed` [= §4.9], `evidence_retracted`, `recheck_after`; evaluated after every commit touching node/children + daily tick; only action enqueue_review; adding a condition needs a spec change).
- **Steps** — (1) Registry of `condition(node, ctx) -> bool`. (2) Implement the four named conditions only. (3) Evaluate after relevant commits and on a daily tick. (4) Sole side effect: `enqueue_review`. (5) No script runner (§8).
- **Verify** — `uv run pytest tests/unit/tms/test_triggers.py`
- **DoD** — each condition fires under its scenario and only enqueues review; `all_subtasks_closed` never auto-closes the supertask; the registry is closed to ad-hoc additions.

### T7.4 — Supertask trigger fires once, never auto-closes
- **Goal** — When all subtasks close, the supertask is flagged for review exactly once and is never auto-completed.
- **Depends on** — T7.3.
- **Files** — `src/akasha/tms/triggers.py`, `tests/integration/test_tms.py` (`test_supertask_flag`).
- **Spec** — §4.10, §9 story 8, §6.2 E06/E08.
- **Steps** — (1) `all_subtasks_closed` enqueues one review for the supertask. (2) Idempotent — no duplicate on re-evaluation. (3) Never sets the supertask `task_state=done`.
- **Verify** — `uv run pytest tests/integration/test_tms.py::test_supertask_flag`
- **DoD** — closing all subtasks flags the supertask once and leaves it `open`.

### T7.5 — Review queue: resolutions + daily cap (`review.py`)
- **Goal** — `enqueue_review`, `resolve_review`, and the ordered daily active-queue cap of 10.
- **Depends on** — T7.1, T1.6, T4.11.
- **Files** — `src/akasha/tms/review.py`, `tests/integration/test_tms.py`.
- **Spec** — §4.9 resolutions (`still_holds`, `revised` [new commit, itself classified], `retracted`, `dismissed` [violations only]); daily cap 10 ordered by (staleness age, inbound-edge count, user flag).
- **Steps** — (1) Implement the four resolutions; `revised` submits a new commit that is itself classified (may re-trigger invalidation). (2) `dismissed` only for violation items. (3) On approval of a create-node proposal (`cause_kind=proposal`, `node_id=NULL`), parse the canonical `{method,path,body}` envelope, call `create_node`, and return the newly minted id; never mint before approval. (4) Enforce the daily active cap of 10 with the specified ordering.
- **Verify** — `uv run pytest tests/integration/test_tms.py -k review`
- **DoD** — each resolution behaves per spec; `revised` re-classifies its commit; the active queue never exceeds 10 and is ordered correctly.

### T7.6 — Split/merge inbound-reassignment queue
- **Goal** — Split/merge produce a reassignment queue and leave zero dangling references.
- **Depends on** — T1.6, T7.5.
- **Files** — `src/akasha/tms/review.py`, `src/akasha/kernel/store.py`, `tests/property/test_split_merge.py`.
- **Spec** — §4.11 (split/merge return redirect + reassignment queue), §9 story 4 (property test).
- **Steps** — (1) On split/merge, enqueue inbound edges needing reassignment. (2) Follow redirects for resolution. (3) Property test: after any split/merge, no dangling references remain.
- **Verify** — `uv run pytest tests/property/test_split_merge.py`
- **DoD** — property test finds zero dangling references across random split/merge sequences.

### T7.7 — Facets-from-spans capture (`POST /edges` with `facet_span`)
- **Goal** — `POST /edges` accepts `facet_span` and creates the facet on the target from the highlighted span.
- **Depends on** — T4.5, T1.4.
- **Files** — `src/akasha/api/routes/edges.py`, `src/akasha/kernel/store.py`, `tests/integration/test_tms.py`.
- **Spec** — §4.2 (Facet.span, facets-from-spans), §4.11, M7 (capture flow in API/UI).
- **Steps** — (1) Accept `facet_span` on edge creation. (2) Mint a facet on the target node from that span and bind the edge to it. (3) Update the OpenAPI snapshot in the same change.
- **Verify** — `uv run pytest tests/integration/test_tms.py -k facet_span && uv run pytest tests/integration/test_openapi_snapshot.py`
- **DoD** — creating an edge with `facet_span` yields a new bound facet on the target and a non-`*` binding; snapshot gate green.

---

## M8 — Web UI (Depends on: M7)

**Milestone DoD (spec):** playwright smoke test: create → link with span → break facet → see badge → resolve.

### T8.1 — UI shell + static serving (htmx, no build step)
- **Goal** — Daemon-served UI shell with htmx + vanilla JS, no SPA framework, no build step beyond copying static files.
- **Depends on** — T4.3.
- **Files** — `src/akasha/ui/templates/base.html`, `src/akasha/ui/static/app.js`, `src/akasha/api/app.py` (mount UI).
- **Spec** — §4.13 (htmx + vanilla JS; no SPA; no build step).
- **Steps** — (1) Serve a base template + static assets from the daemon. (2) Wire htmx. (3) No bundler/build step.
- **Verify** — Start daemon; `GET /` returns the shell (assert in a lightweight integration test).
- **DoD** — the shell loads from the daemon with no build step; static files served directly.

### T8.2 — Node view
- **Goal** — Body, facets, 1-hop neighborhood, history, and a stale badge with its cause.
- **Depends on** — T8.1, T4.4.
- **Files** — `src/akasha/ui/templates/node.html`, `src/akasha/ui/static/app.js`.
- **Spec** — §4.13 (Node view), R9 (badge copy "vetted by you", never "true").
- **Steps** — (1) Render body + facets + 1-hop neighborhood + history. (2) Show a stale badge with its cause when the node has an open facet_break. (3) Badge copy uses "vetted by you" language, never "true".
- **Verify** — Playwright step: open a node, confirm sections + badge render.
- **DoD** — node view shows all five elements; badge text never says "true".

### T8.3 — Review view (one-click resolutions + daily-cap banner)
- **Goal** — Queue with one-click resolutions and a daily-cap banner.
- **Depends on** — T8.1, T7.5.
- **Files** — `src/akasha/ui/templates/review.html`, `src/akasha/ui/static/app.js`.
- **Spec** — §4.13 (Review view), §4.9 (daily cap 10).
- **Steps** — (1) List the active queue. (2) One-click `still_holds/revised/retracted/dismissed`. (3) Show a daily-cap banner when the cap is reached.
- **Verify** — Playwright step: resolve an item in one click; banner shows at cap.
- **DoD** — resolutions work one-click; cap banner appears when 10 active items exist.

### T8.4 — Search + Sync views
- **Goal** — Search view (FTS) and Sync view (per-sync-root status, violations, pause&diff inspector).
- **Depends on** — T8.1, T4.5, T5.7.
- **Files** — `src/akasha/ui/templates/{search.html,sync.html}`, `src/akasha/ui/static/app.js`.
- **Spec** — §4.13 (Search, Sync views incl. pause&diff inspector).
- **Steps** — (1) Search box over `/search`. (2) Sync view lists per-sync-root status + violations + a pause&diff inspector; Obsidian-specific labels may say “vault”.
- **Verify** — Playwright step: run a search; open the sync view and inspect a paused file.
- **DoD** — search returns hits; sync view renders violations and the pause&diff inspector.

### T8.5 — Playwright smoke test (full loop)
- **Goal** — Automate create → link with span → break facet → see badge → resolve.
- **Depends on** — T8.2, T8.3, T8.4, T7.7.
- **Files** — `tests/integration/test_ui_smoke.py` (or `plugin-obsidian`-independent playwright harness), CI job.
- **Spec** — M8 DoD.
- **Steps** — (1) Script the five-step loop end-to-end. (2) Run against a live test daemon. (3) Add to CI.
- **Verify** — `uv run pytest tests/integration/test_ui_smoke.py`
- **DoD** — the full create→link→break→badge→resolve loop passes headless in CI.

---

## M9 — Hardening (Depends on: M5–M8)

**Milestone DoD (spec):** 24-h soak (`tests/battery/soak.py`) — RSS < 150 MB, idle CPU ≈ 0%, zero unhandled exceptions.

### T9.1 — Windows battery items (CRLF, locking retry, AV noise)
- **Goal** — Handle Windows file-locking retries and AV-induced transient errors; confirm CRLF handling end-to-end.
- **Depends on** — T5.8.
- **Files** — `src/akasha/sync/watcher.py`, `src/akasha/sync/reconcile.py`, `tests/battery/test_windows.py`.
- **Spec** — M9 (CRLF, locking retry, AV noise), §6.2 E09.
- **Steps** — (1) Retry-with-backoff on Windows sharing-violation/locked-file errors. (2) Tolerate transient AV-held handles. (3) Confirm CRLF files canonicalize with no spurious diff (E09).
- **Verify** — `uv run pytest tests/battery/test_windows.py` (Windows CI leg).
- **DoD** — locked-file writes retry and succeed; CRLF arrival produces no spurious diff.

### T9.2 — Metrics: RSS/CPU sampling + counters (`metrics.py`)
- **Goal** — Implement §7 counters and `GET /v1/metrics`.
- **Depends on** — T4.3, T7.5.
- **Files** — `src/akasha/metrics.py`, `src/akasha/api/routes/health.py` (or metrics route), `tests/unit/test_metrics.py`.
- **Spec** — §7 (facet_coverage, review inflow/resolved/variance, violation_rate, auto_repairs{class}, crossing_rate, rss_bytes, idle_cpu_pct, sync_cycle_ms{p50,p95}), §4.11 (`GET /metrics`).
- **Steps** — (1) Implement each §7 counter. (2) Sample RSS and idle CPU. (3) Expose `GET /v1/metrics` (JSON). (4) Update the OpenAPI snapshot.
- **Verify** — `uv run pytest tests/unit/test_metrics.py && uv run pytest tests/integration/test_openapi_snapshot.py`
- **DoD** — every §7 metric appears in `/v1/metrics`; RSS/CPU sampled; snapshot gate green.

### T9.3 — S0 GC scheduling + log rotation
- **Goal** — Schedule the S0 GC job and enable rotating logs.
- **Depends on** — T1.7, T0.6.
- **Files** — `src/akasha/daemon.py`, `tests/integration/test_gc_schedule.py`.
- **Spec** — M9 (S0 GC scheduling, log rotation), §4.4/§4.5 (GC safety).
- **Steps** — (1) Run GC on a schedule/daily tick. (2) Confirm rotating file handler rotates. (3) GC keeps referenced objects (reuse T1.7 invariant).
- **Verify** — `uv run pytest tests/integration/test_gc_schedule.py`
- **DoD** — scheduled GC runs and removes only orphans; logs rotate at the configured size.

### T9.4 — `--dry-run` coverage + error-message pass
- **Goal** — Ensure every mutating CLI verb supports `--dry-run`; audit error messages for clarity.
- **Depends on** — T4.8.
- **Files** — `src/akasha/cli/main.py`, `tests/integration/test_cli_dry_run.py`.
- **Spec** — §4.12 (`--dry-run` returns would-be request), M9 (dry-run coverage, error-message pass).
- **Steps** — (1) Verify each mutating verb returns the would-be request under `--dry-run` and mutates nothing. (2) Standardize error messages against the API error envelope. (3) Add a coverage test enumerating the verbs.
- **Verify** — `uv run pytest tests/integration/test_cli_dry_run.py`
- **DoD** — every mutating verb has a passing `--dry-run` case with zero state change.

### T9.5 — 24-hour soak test
- **Goal** — Prove residency: RSS < 150 MB, idle CPU ≈ 0%, zero unhandled exceptions.
- **Depends on** — T9.1, T9.2, T9.3, T9.4.
- **Files** — `tests/battery/soak.py`, nightly CI job.
- **Spec** — M9 DoD, §9 story 9.
- **Steps** — (1) Drive realistic edit traffic over 24 h (or a scaled proxy in CI with a full run nightly on `main`). (2) Sample RSS/CPU into metrics. (3) Assert zero unhandled exceptions.
- **Verify** — `uv run python tests/battery/soak.py --hours 24` (nightly Windows).
- **DoD** — RSS < 150 MB throughout; idle CPU ≈ 0%; zero unhandled exceptions in logs.

---

## M10 — Dogfood instrumentation (Depends on: all)

**Milestone DoD (spec):** PRD §8 acceptance stories 1–9 each mapped to a passing test or checked manual script in `docs/acceptance.md`; the one-month dogfood gate begins.

### T10.1 — Metrics dashboard view
- **Goal** — UI view for facet coverage, inflow vs resolution + variance, violation rate, crossing rate.
- **Depends on** — T9.2, T8.1.
- **Files** — `src/akasha/ui/templates/dashboard.html`, `src/akasha/ui/static/app.js`.
- **Spec** — M10 (dashboard), §7 metrics, §9 story 6.
- **Steps** — (1) Read `/v1/metrics`. (2) Render facet coverage, review inflow vs resolved with variance, violation rate, crossing rate. (3) No new metric definitions — display only.
- **Verify** — Playwright/integration: dashboard renders each metric from a seeded state.
- **DoD** — dashboard shows all four metric groups sourced from `/v1/metrics`.

### T10.2 — Export command (`akasha export --md DIR`)
- **Goal** — Export the hub to markdown in a target directory.
- **Depends on** — T4.8, T3.3.
- **Files** — `src/akasha/cli/main.py` (`export` verb), `tests/integration/test_export.py`.
- **Spec** — M10 (`akasha export --md DIR`).
- **Steps** — (1) Render every managed node to canonical markdown under `DIR`. (2) Deterministic, canonical output. (3) `--json` summary of what was written.
- **Verify** — `uv run pytest tests/integration/test_export.py`
- **DoD** — export writes canonical markdown for all nodes; re-export is byte-stable.

### T10.3 — Acceptance mapping (`docs/acceptance.md`)
- **Goal** — Map PRD §8 stories 1–9 each to a passing test or a checked manual script.
- **Depends on** — all prior milestones DONE.
- **Files** — `docs/acceptance.md`.
- **Spec** — §9 acceptance table, M10 DoD.
- **Steps** — (1) For each story below, record the verifying test/script and confirm it is green. (2) Any gap is a `# SPEC-QUESTION:`, not a silent pass.

  | PRD story | Verified by |
  |---|---|
  | 1 capture ≤3s (syntax path) | M4 CLI/API timing test + manual script (T4.4/T4.8) |
  | 2 contradiction surface (non-LLM) | M7 near-duplicate FTS heuristic test |
  | 3 invalidation on major edit | `test_tms.py::test_facet_break_flags_subscribers` (T7.1) |
  | 4 split/merge zero dangling | property test `test_split_merge.py` (T7.6) |
  | 5 as-of time travel | `test_api.py::test_as_of` (T4.4) |
  | 6 review economy (cap, dashboard) | dashboard + metrics assertions (T10.1/T9.2) |
  | 7 contract sync losslessness | battery E01–E20 (T5.8) |
  | 8 tasks + supertask trigger + S0 lifecycle | `test_tms.py::test_supertask_flag` (T7.4), battery E06/E08 |
  | 9 daemon residency | soak + crash-recovery (T9.5/T5.6) |

- **Verify** — `make check && make battery && uv run python tests/battery/soak.py` all green; every row references a passing test/script.
- **DoD** — all nine rows green on Windows CI; `docs/acceptance.md` complete. **The one-month dogfood gate begins.**

---

## Expandability guardrails (build-now-use-later — do NOT implement future phases)

These are constraints on the tasks above, not tasks themselves (spec §8):

- Keep the **agent-token → review-queue proposal pathway** (T4.6) intact; reserve `cause_kind=proposal` rendering. It is the Phase 3 decomposer's entry point.
- Keep `api/schemas.py` **re-exportable** (T4.3) so a Phase 4 MCP facade can import only the HTTP API.
- The `tms/triggers.py` registry (T7.3) is the future host boundary — **do not add a script runner now**.
- All state stays **content-addressed with per-commit parents** (M1). **Never introduce a global sequence counter** (multi-device/CRDT-friendliness).
- Treat the **golden corpus, OpenAPI snapshot, and no-pickle/canonical-bytes rules as sacred** (rule 0.3) — they are the Rust-migration enablers.

**Explicit MVP non-goals — do not build even if easy:** LLM calls, embeddings, MCP server, mobile, multi-user, task scheduling/recurrence, prose management.

