# akasha — Fine-Grained Agent Build Plan

**Derived from:** MVP Implementation Specification v1.0 (that document is authoritative; this one only sequences it into small, verifiable steps).
**Purpose:** Turn milestones M0–M10 into per-file tasks that a *less-capable agent* can execute one at a time, each with an explicit verification step and a machine-checkable Definition of Done (DoD). **M11** is a post-MVP addendum (M0–M10 all reached DONE 2026-07-21) — a real-vault dogfood smoke test requested directly by the user rather than derived from `mvp-spec.md`'s milestone list; it invents no new schema/endpoint/grammar, it only exercises the existing API/CLI surface against real personal content instead of synthetic battery fixtures.

---

## How to use this plan (read before doing anything)

1. **Do tasks strictly in ID order** within a phase, and never start a task whose `Depends on` tasks are not all `DONE`. When in doubt, stop and ask; do not improvise ordering.
2. **One task = one focused change.** Touch only the files listed under `Files`. If you feel you must touch a file not listed, that is a signal the task is misunderstood — stop and add a `# SPEC-QUESTION:` note in `docs/spec-questions.md` instead of guessing. Rule 5 is the sole precedence exception: when an API/TMS task necessarily persists state and its Files list accidentally omits `kernel/store.py`, add only the minimal store helper and record/correct the omission; never write SQLite from the higher layer. **Files-list completion (ratified T8.0/T8.1):** a file may be added when it is *strictly entailed by the task's own Goal/DoD/Verify text* (e.g. a vendored asset the DoD says to serve, or the integration test the Verify demands) — log the completion in `docs/spec-questions.md` and correct the Files line. This is narrow: anything requiring judgment about *what* to build stays a stop-and-log spec-question, not a completion.
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
M0 ─┬─► M1 ─┬──────────────► **M4** ─► **M5** ─┬─► **M7** ─► M8 ─► **M10** ─► M11
    │       │                                  │
    └─► M2 ─► M3 ──────────────► (feeds M4/M5)  └─► M9 (deps M5–M8)
                                                M6 (deps M5)  ─► M9
```
Parallelizable once deps are met: M6 and M8. Everything else follows the arrows. M11 (post-MVP addendum) depends on M10 only.

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

### T8.0 — Review API endpoints (`GET /v1/review`, `POST /v1/review/{id}/resolve`)
- **Goal** — Expose the spec §4.11 review endpoints over HTTP, wiring the existing `src/akasha/tms/review.py` logic. **Prerequisite for T8.2/T8.3** (both need review data over HTTP). Discovered missing during M8 orientation: §4.11 defines these endpoints and M4's CLI + M8's UI depend on them, but no prior task's `Files` list ever included the route — T7.5 built only `tms/review.py`. See SPEC-QUESTION T8.0.
- **Depends on** — T7.5 (review logic), T4.3 (app factory).
- **Files** — `src/akasha/api/routes/review.py` (new), `src/akasha/api/app.py` (wire router), `docs/api-snapshot/openapi.json` (regenerate via the sanctioned command, never hand-edit), `tests/integration/test_api.py`.
- **Spec** — §4.11 (`GET /review?status=open` queue; `POST /review/{id}/resolve` resolutions, human-only ∅).
- **Steps** — (1) `GET /review?status=open` returns the OPEN review set via `store.find_open_reviews` (**UNCAPPED** — the daily-cap-10 is a T8.3 display concern, not an endpoint limit), with an optional `node` filter so the Node view can query a node's open `facet_break` reviews for its stale badge. (2) `POST /review/{id}/resolve` (human-only via `deps.require_human`) dispatches to `tms/review.py` `resolve_review` for the four standard resolutions (`still_holds|revised|retracted|dismissed`). (3) Wire the router into `app.py`. (4) Regenerate the OpenAPI snapshot via the sanctioned command.
- **Verify** — `uv run pytest tests/integration/test_api.py -k review && uv run pytest tests/integration/test_openapi_snapshot.py`
- **DoD** — a browser/CLI can list open reviews (filterable by node) and resolve one of the four resolutions; resolve is human-only; OpenAPI snapshot regenerated with a reviewed diff of exactly the two new paths.

### T8.1 — UI shell + static serving (htmx, no build step)
- **Goal** — Daemon-served UI shell with htmx + vanilla JS, no SPA framework, no build step beyond copying static files.
- **Depends on** — T4.3.
- **Files** — `src/akasha/ui/templates/base.html`, `src/akasha/ui/static/app.js`, `src/akasha/ui/static/htmx.min.js` (vendored), `src/akasha/api/app.py` (mount UI), `tests/integration/test_ui_shell.py`.
- **Spec** — §4.13 (htmx + vanilla JS; no SPA; no build step).
- **Steps** — (1) Serve a base template + static assets from the daemon. (2) Wire htmx. (3) No bundler/build step.
- **Verify** — Start daemon; `GET /` returns the shell (assert in a lightweight integration test).
- **DoD** — the shell loads from the daemon with no build step; static files served directly.

### T8.2 — Node view
- **Goal** — Body, facets, 1-hop neighborhood, history, and a stale badge with its cause.
- **Depends on** — T8.0, T8.1, T4.4.
- **Files** — `src/akasha/ui/templates/node.html`, `src/akasha/ui/static/app.js`.
- **Spec** — §4.13 (Node view), R9 (badge copy "vetted by you", never "true").
- **Steps** — (1) Render body + facets + 1-hop neighborhood + history. (2) Show a stale badge with its cause when the node has an open facet_break. (3) Badge copy uses "vetted by you" language, never "true".
- **Verify** — Playwright step: open a node, confirm sections + badge render.
- **DoD** — node view shows all five elements; badge text never says "true".

### T8.3 — Review view (one-click resolutions + daily-cap banner)
- **Goal** — Queue with one-click resolutions and a daily-cap banner.
- **Depends on** — T8.0, T8.1, T7.5.
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

### T8.5b — Daemon per-request DB connections (concurrency fix)
- **Goal** — Fix the data-integrity concurrency defect the T8.5 smoke test exposed: the spec-§3 single shared `sqlite3.Connection` corrupts reads under the Web UI's concurrent `fetch`es. **Prerequisite for T8.5.** Discovered during T8.5; user-directed "ensure concurrency is possible."
- **Depends on** — T8.1 (the app factory / deps this touches).
- **Files** — `src/akasha/api/deps.py`, `src/akasha/api/app.py`, `src/akasha/kernel/store.py`, `tests/integration/test_concurrency.py`.
- **Spec** — amends §3 (see SPEC-QUESTION T8.5b).
- **Steps** — (1) `get_conn` opens a fresh WAL connection PER REQUEST (concurrent readers + one writer), closed at request end; `app.state.db_path` selects this vs. the injected-connection path for tests. (2) `store.connect` gains `PRAGMA busy_timeout`. (3) Keep `app.state.conn` for the pre-serving startup reconcile only. (4) Add a fast concurrent-request regression test.
- **Verify** — `uv run pytest tests/integration/test_concurrency.py`
- **DoD** — concurrent node-view fetches never corrupt (all 200); existing sequential (injected-connection) tests unchanged.

### T8.5 — Playwright smoke test (full loop)
- **Goal** — Automate create → link with span → break facet → see badge → resolve.
- **Depends on** — T8.2, T8.3, T8.4, T8.5b, T7.7.
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

### T9.2c — Wire live producers for `violation_rate`/`auto_repairs{class}`/`sync_cycle_ms` (pre-dogfood triage)
- **Goal** — Make the three §7 metrics that currently read `0.0`/`{}` in every real daemon actually reflect production sync activity, by calling the recorder API `metrics.py` already exposes (`record_sync_cycle_ms`, `record_auto_repair`) from the one place that has the data: `sync/reconcile.py`'s `Reconciler.on_change`.
- **Depends on** — T9.2 (DONE — built the recorder API and `compute_metrics()`'s aggregation over it, but never wired a producer).
- **Files** — `src/akasha/sync/reconcile.py` (the only file needing a change — `metrics.py`'s recorder API is already complete), `docs/acceptance.md` (row 6 — remove the disclosure caveat once live, or correct it if a leg stays unwired).
- **Spec** — §7 (`violation_rate`, `auto_repairs{class}`, `sync_cycle_ms{p50,p95}`), §4.8 (`Reconciler.on_change` pipeline).
- **Scope narrowing (do NOT widen)** — pure wiring only. Do not touch `metrics.py` (the recorder + aggregation are already correct and tested — `record_sync_cycle_ms`/`record_auto_repair` are the exact, already-built call targets); do not add a new DB table/column (the recorder is deliberately in-process/in-memory, same precedent as `auth.py`'s rate limiter — resets on restart, documented, not a truth invariant); do not touch the certain-repair logic itself (`contract/linter.py`), only observe it.
- **Steps** — (1) Wrap `Reconciler.on_change`'s body in a `time.monotonic()` timer at entry and record the elapsed milliseconds via `metrics.record_sync_cycle_ms(...)` on **every** exit path (the quiet no-op return, the hub-only-change return, the pause&diff early return, and normal completion) — `violation_rate = violations / sync_cycles`, so a cycle that doesn't count would silently understate the denominator. Use `try/finally` so an exception mid-cycle still records the attempt. (2) In the branch where certain-repairs are actually applied silently (not the conservative/pause&diff branch, where the same repair codes route to review instead — do not double-count), call `metrics.record_auto_repair(repair.code)` once per applied repair. (3) Add a test driving one real `on_change` cycle each for: a quiet cycle (timing recorded, zero repairs), a cycle with a real certain-repair applied (E_LOST_ANCHOR or E_DUP_ID path, per §4.7 — repair code recorded), and confirm the conservative/pause&diff path does NOT record a repair for the same violation. (4) Re-run `docs/acceptance.md` row 6's cited commands; if all three metrics now read live non-zero values under the test's synthetic traffic, remove the disclosure caveat this task's registration added (or, if it's still accurate for some remaining leg, correct it rather than deleting it — never silently misrepresent).
- **Verify** — `uv run pytest tests/unit/test_metrics.py tests/integration/test_openapi_snapshot.py` plus the new reconcile-level test (file TBD by the worker, likely `tests/integration/test_reconcile.py` or `tests/unit/sync/test_reconcile.py` depending on whether a live daemon is needed — worker's judgment, but must exercise the REAL `Reconciler.on_change` path, not call `record_sync_cycle_ms`/`record_auto_repair` directly as a substitute).
- **DoD** — `violation_rate`, `auto_repairs`, and `sync_cycle_ms` are populated by real `on_change` cycles, not just directly-seeded recorder calls; existing T9.2/T9.5 tests unregressed; `make check` green; `docs/spec-questions.md`'s T9.2-producer entry moves to `docs/archived-questions.md` on landing.

### T9.3 — S0 GC scheduling + log rotation
- **Goal** — Schedule the S0 GC job and enable rotating logs.
- **Depends on** — T1.7, T0.6.
- **Files** — `src/akasha/daemon.py`, `tests/integration/test_gc_schedule.py`.
- **Spec** — M9 (S0 GC scheduling, log rotation), §4.4/§4.5 (GC safety).
- **Steps** — (1) Run GC on a schedule/daily tick. (2) Confirm rotating file handler rotates. (3) GC keeps referenced objects (reuse T1.7 invariant).
- **Verify** — `uv run pytest tests/integration/test_gc_schedule.py`
- **DoD** — scheduled GC runs and removes only orphans; logs rotate at the configured size.

### T9.3b — S0 node-retention-by-age GC (vision A7, pre-dogfood triage)
- **Goal** — Schedule the age-based S0 *node* deletion job vision.md §14 assumption A7 requires ("S0 default GC retention 30 days (configurable)") — distinct from T9.3's object-level `gc_objects` orphan reclamation, which only cleans up objects already orphaned by some other deletion. The archived T1.7 resolution named this job's home explicitly and assigned it to T9.3, but T9.3's literal Steps/DoD never built it; this task closes that gap.
- **Depends on** — T9.3 (DONE), T1.6 (DONE — `delete_node`'s S0 hard-delete branch is the mechanism this task drives).
- **Files** — `src/akasha/config.py` (new `s0_gc_retention_days: int = 30` field, TOML-loaded), `src/akasha/kernel/store.py` (one new read-only helper, e.g. `list_expired_s0_node_ids(conn, older_than_iso)` — rule-0.4-sanctioned completion, same precedent as T9.2's read-only metrics helpers), `src/akasha/daemon.py` (`GcScheduler`'s tick), `tests/integration/test_gc_schedule.py`.
- **Spec** — vision.md §14 A7 ("S0 default GC retention 30 days (configurable); GC blocked at S1 automatically"), §7.2 ("S0 nodes are... periodically garbage-collected"), the archived T1.7 resolution (`docs/archived-questions.md`) naming T9.3/this task as the intended lifecycle's first step.
- **Scope narrowing (do NOT widen)** — no migration: `nodes.created_at` and `nodes.maturity` (both existing §4.4 columns) are sufficient to identify expired S0 nodes; the only new state is a config value, not a DB column. Do not touch S1+ nodes under any circumstance (`list_expired_s0_node_ids` must filter `maturity='S0' AND status='live'` — an S1+ node is never eligible for age-based deletion, per §4.6/A7's own "GC blocked at S1 automatically"). Do not change `gc_objects`' own reachability logic (T1.7, frozen/final).
- **Steps** — (1) Add the config field, defaulting to 30 (vision A7's stated default), configurable via the existing TOML config file. (2) Add the read-only `store.py` helper querying `nodes WHERE maturity='S0' AND status='live' AND created_at < ?`. (3) In `GcScheduler`'s scheduled tick, call the helper, then `store.delete_node(conn, node_id)` (the existing S0 hard-delete branch — no new deletion path) for each expired id, **before** the existing `store.gc_objects(conn)` call in the same tick (ordering matches T1.7's stated two-step lifecycle: node deletion first, so the objects it orphans are reclaimed the same tick, not next). (4) Test: an S0 node older than the threshold is gone after a tick; one younger survives; an S1+ node of any age survives regardless of threshold; confirm `gc_objects` in the same tick reclaims the now-orphaned objects (no two-tick lag).
- **Verify** — `uv run pytest tests/integration/test_gc_schedule.py`
- **DoD** — a scheduled tick deletes only S0 nodes older than the configured retention threshold, never touches S1+ nodes, and the same tick's `gc_objects` reclaims their now-orphaned objects; existing T9.3 object-GC/log-rotation behavior unregressed; `make check` green; `docs/spec-questions.md`'s T9.3 entry moves to `docs/archived-questions.md` on landing.

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
- **Goal** — Export the hub to markdown in a target directory, via a new read-only endpoint; the CLI stays a pure HTTP client.
- **Depends on** — T4.8, T3.3.
- **Files** — `src/akasha/api/routes/sync.py` (`GET /sync/export` on the existing router — no `app.py` change), `src/akasha/kernel/store.py` (read-only `unfiled_node_count` helper, rule-0.4 completion per T9.2 precedent), `src/akasha/sync/reconcile.py` (read-only projection mode: suppress `hub_state_for`'s enqueue-on-unprojectable-body side effect for export reads), `src/akasha/cli/main.py` (`export` verb), `docs/api-snapshot/openapi.json` (regenerated in the same change, §6.3 gate), `tests/integration/test_export.py`, `tests/integration/test_api.py` (endpoint coverage).
- **Spec** — M10 (`akasha export --md DIR`), §4.11 `GET /sync/export`, §4.12 `export` verb (both added by the 2026-07-18 T10.2 fable rulings — see `docs/spec-questions.md` for the full transport + scope resolutions).
- **Steps** — (1) Endpoint: for every `store.list_sync_files` row with a base snapshot (same skip rule as `ProjectionIndex.build`), serve `render(hub_state_for(parse(base)))` as `{sync_root, relative_path, text}`, ordered by (sync-root name, POSIX root-relative path), plus top-level `unfiled_node_count` (live nodes in no managed projection); strictly read-only — a GET mutates nothing, not even review items; any token class. (2) CLI: pure client of the endpoint — write each `text` byte-for-byte to `DIR/<sync_root>/<relative_path>`. (3) `--json` summary: files written + `unfiled_node_count`. (4) Regenerate the OpenAPI snapshot in the same change.
- **Verify** — `uv run pytest tests/integration/test_export.py tests/integration/test_openapi_snapshot.py`
- **DoD** — export writes canonical markdown for every managed projection; re-export is byte-stable; `GET /sync/export` mutates nothing (review queue identical before/after the call, asserted in test); snapshot gate green.

### T10.2b — Contradiction surfacing at capture (story 2, non-LLM)
- **Goal** — On human-token claim creation, `POST /v1/nodes` returns exact/near-duplicate candidate claims (existing claim's text, date, and attached evidence) via a non-LLM FTS5 heuristic over the **existing** `nodes_fts` index; display-only and strictly read-only.
- **Depends on** — T1.4, T4.4 (both DONE). *Inserted 2026-07-19 by the story-2 gap fable ruling: spec §9 row 2 named an "M7 near-duplicate FTS heuristic test" that no M7 task (T7.1–T7.7) ever produced, and no contradiction-surfacing code exists anywhere in `src/akasha/` — yet PRD §8.2, PRD §9's Phase-2 exit ("contradiction surfacing for exact/near-duplicate claims (non-LLM heuristics first)"), PRD §11's first-contradiction-within-week-one Value metric, and PRD §2's north-star paragraph all require it. See `docs/spec-questions.md` T10.3 entries.*
- **Files** — `src/akasha/api/routes/nodes.py` (201 response field), `src/akasha/kernel/store.py` (read-only `find_contradiction_candidates` helper — rule-0.4 completion, T9.2 precedent), `docs/api-snapshot/openapi.json` (regenerated in the same change via the sanctioned command, §6.3 gate), `tests/integration/test_contradiction_surfacing.py` (new).
- **Spec** — §4.11 `POST /nodes` row + the contradiction-surfacing paragraph (both added by the same ruling), §9 row 2, PRD §8.2, PRD §5 F-list (no LLM anywhere in the truth path).
- **Steps** — (1) `store.py`: read-only helper — tokenize the new claim's canonicalized body to alphanumeric terms, FTS5-quote each, OR-join (empty term set ⇒ `[]`); `nodes_fts MATCH` ranked by bm25; filter `type='claim' AND status='live'`, exclude the new node itself; byte-equal canonical body ranks first; cap 5. Evidence per candidate via the existing `find_live_edges(src=candidate, edge_type='cites')` filtered to Evidence-type dst nodes. No write verbs, no transaction, no new table/index (schema stays frozen per rule 2). (2) `routes/nodes.py`: compute only on the human 201 path; field present on every 201 (`[]` for non-claim types); the agent 202 proposal path (T4.6) is untouched. (3) Regenerate the OpenAPI snapshot. (4) Tests: exact-duplicate ranks first and carries evidence text + `created_at`; near-duplicate (shared terms) surfaces; non-claim create ⇒ `[]`; agent create ⇒ 202 unchanged, no candidates; **read-only proof** — `review_queue` contents and node/commit state byte-identical before/after the surfacing computation (mirror T10.2's read-only-gate test discipline); an FTS5-syntax-hostile body (quotes, operators, only punctuation) returns 201 with a sane candidate list, never a 500.
- **Verify** — `uv run pytest tests/integration/test_contradiction_surfacing.py tests/integration/test_openapi_snapshot.py`
- **DoD** — exact + near-duplicate candidates returned with text/date/evidence on human claim creation; zero writes from the surfacing path (asserted in test); no new schema, index, or `cause_kind`; snapshot gate green; `make check` green.

### T10.2c — Wire §4.10 trigger evaluation into the commit path (story 8)
- **Goal** — Make the supertask trigger actually fire in a running daemon: closing the last open subtask (via `PATCH /nodes` or an Obsidian checkbox toggle) enqueues the parent's `subtasks_closed` review item. Today it does not — the evaluator exists but has no production call site.
- **Depends on** — T7.2, T7.3, T7.4 (all DONE). *Registered 2026-07-19 by a fable closeout audit of T10.3's acceptance mapping: grep-verified (independently reproduced) that nothing under `src/akasha/` imports `tms.triggers` at all — the sole in-`src/` reference is `run_daily_tick`'s own internal call to `evaluate` at `triggers.py:237`, and `run_daily_tick` has no caller either; every external caller is a test (`tests/unit/tms/test_triggers.py`, `tests/integration/test_tms.py`). Spec §4.10 requires evaluation "(a) after every commit touching the node or its children, (b) on a daily tick"; T7.3 explicitly deferred the wiring to "whichever later task wires this into the daemon's daily-tick driver" and no task ever did. T7.4 verified the evaluator's semantics under direct invocation, not a wired path — so vision §8 story 8 ("closing the last open subtask fires the trigger and the supertask appears in the review queue") is false in production, in the launch domain, and §7.10 calls this rule "the canonical example" of the reactive layer.*
- **Files** — `src/akasha/kernel/store.py` (one call site in `commit_node`, mirroring how T7.2 wired `invalidate`), `tests/integration/test_tms.py` (add a test driving the REAL path end-to-end, not the evaluator directly).
- **Spec** — §4.10a (`evaluate`), §4.10 condition `all_subtasks_closed`, PRD §8 story 8, PRD §7.10.
- **Scope narrowing (do NOT widen)** — wire **only** the `all_subtasks_closed` condition on the commit path. `facet_interface_changed` is already live (§4.10 states it is implemented *as* §4.9's invalidation, wired since T7.2 — do not double-fire it). `evidence_retracted` is materially covered by T7.2b's delete→invalidate path via `cites` subscribers. `recheck_after` has **no persisted schedule** (open T7.3 spec-question — no DDL column exists), so §4.10's daily-tick leg (b) is **explicitly descoped** to a post-MVP ruling rather than half-built; do not invent a schedule table (rule 2). Adding a migration is out of scope for this task.
- **Steps** — (1) In `store.commit_node`, after the existing `invalidate` call and INSIDE the same `with conn:` transaction, evaluate the `all_subtasks_closed` condition for the committed node's parent supertask(s). (2) **Transaction gotcha (T7.2 precedent, load-bearing):** sqlite3's `with conn:` commits on every block exit, so a nested `with conn:` inside `enqueue_review` would prematurely commit the in-flight commit. Use `enqueue_review_within_transaction` (the composable variant T7.2 extracted for exactly this) — do not call the standalone `enqueue_review`. (3) **Import gotcha:** `tms/triggers.py` imports from `kernel/store.py`, so wire it with a function-body deferred import, the same pattern `commit_node` already uses for `invalidate`. (4) Preserve idempotence: T7.3 made `all_subtasks_closed` idempotent via a `find_open_reviews` gate and it never writes `task_state` — do not change either property; re-committing must not enqueue a duplicate. (5) Test the REAL path: create a supertask + subtasks linked by `composes`, close them through the actual API/store commit path (not by calling `evaluate` directly), assert the parent is flagged exactly once when the last one closes and not before, and assert the supertask's `task_state` is never auto-closed.
- **Verify** — `uv run pytest tests/integration/test_tms.py`
- **DoD** — closing the last open subtask through the real commit path enqueues exactly one `subtasks_closed` review for the parent (asserted end-to-end, not via direct evaluator invocation); not flagged while any subtask is open; re-commit enqueues no duplicate; supertask `task_state` never auto-closed; existing T7.1–T7.5 tests unregressed; `make check` green. On landing, `docs/acceptance.md` row 8 returns from PARTIAL to GREEN (re-run and update its recorded counts).

### T10.3 — Acceptance mapping (`docs/acceptance.md`)
- **Goal** — Map PRD §8 stories 1–9 each to a passing test or a checked manual script.
- **Depends on** — all prior milestones DONE; T10.2b (row 2's verifier).
- **Files** — `docs/acceptance.md`.
- **Spec** — §9 acceptance table, M10 DoD.
- **Steps** — (1) For each story below, record the verifying test/script and confirm it is green. (2) Any gap is a `# SPEC-QUESTION:`, not a silent pass.

  | PRD story | Verified by |
  |---|---|
  | 1 capture ≤3s (syntax path) | manual capture-timing script over the T4.8 CLI path — the DoD's checked-manual-script leg; no automated timing test exists (grep-verified 2026-07-19), so the row stays *pending manual execution* until a human runs it (see `docs/spec-questions.md` T10.3 citation-drift entry) |
  | 2 contradiction surface (non-LLM) | `tests/integration/test_contradiction_surfacing.py` (T10.2b) |
  | 3 invalidation on major edit | `test_tms.py::test_review_revised_reclassifies_and_cascades` + `test_tms.py::test_s1_node_retraction_flags_dependents` (T7.5/T7.2b); `*`-binding-on-any-break: `tests/unit/tms/test_invalidate.py` (T7.1) — coverage distributed per the M7 milestone note |
  | 4 split/merge zero dangling | property test `test_split_merge.py` (T7.6) |
  | 5 as-of time travel | `test_api.py::test_nodes_get_as_of_returns_earlier_body` (T4.4) |
  | 6 review economy (cap, dashboard) | dashboard + metrics assertions (T10.1/T9.2) |
  | 7 contract sync losslessness | battery E01–E20 (T5.8) |
  | 8 tasks + supertask trigger + S0 lifecycle | `test_tms.py::test_supertask_flag` (T7.4), battery E06/E08 |
  | 9 daemon residency | soak + crash-recovery (T9.5/T5.6) |

- **Verify** — `make check && make battery && uv run python tests/battery/soak.py` all green; every row references a passing test/script.
- **DoD** — all nine rows green on Windows CI; `docs/acceptance.md` complete. **The one-month dogfood gate begins.**

---

## M11 — Dogfood smoke test (Depends on: M10)

**Milestone DoD (user-directed, not spec-derived):** a real subset of the
user's own Obsidian vault (`data/(10) Concepts/` — YAML frontmatter,
wikilinks, native Obsidian `^block-id` references, meta-bind plugin
embeds, none of it synthetic) is registered as a live sync root against a
real running daemon, at least one real span becomes a genuinely minted node
through the actual reconcile pipeline, and the result is exercised through
the real CLI/API — before committing to vision.md §Phase-2's full
one-month dogfood gate on the founder's real vault. This is the first time
the sync/contract pipeline runs against real, messy personal content
instead of `tests/battery`'s scripted E01–E20 fixtures.

**Human-in-the-loop boundary (load-bearing, do not blur):** T11.1 is pure
mechanical plumbing — safe for an autonomous worker. T11.2 requires a
human to decide *which real personal-note spans become tracked
claims/entities*; that decision is explicitly reserved for the human
throughout `mvp-spec.md`/`vision.md` (no LLM/agent autonomously curates
what counts as "true" or worth tracking — vision.md PRD §5 F-list, R9). Do
not reassign T11.2 to an autonomous worker even if it looks mechanically
similar to T11.1; `docs/agents/task-status.md` marks it `BLOCKED:
human-only` for exactly this reason, and `fleet-orchestrator`'s scan only
picks up literal `TODO` rows (confirmed against
`.claude/agents/fleet-orchestrator.md`'s eligibility rules) — this keeps it
out of the overnight loop by construction, not by prompting discipline.
**T11.3 and T11.4** (added 2026-07-25, see `docs/agents/overnight-goals.md`)
are both safe for autonomous dispatch by the same test: T11.3 is a pure
discovery-wiring bug fix, and T11.4 is deliberately content-blind (a fixed
template, not real note meaning) — neither makes the T11.2 curation
decision.

### T11.1 — Stage a scratch dogfood vault + register it as a live sync root
- **Goal** — Mechanically stand up a small, disposable, never-git-tracked copy of real notes from `data/(10) Concepts/` as a genuine sync root on a real running daemon instance. Pure plumbing — no judgment about note content.
- **Depends on** — (milestone gate: M10 DONE).
- **Files** — `docs/dogfood/README.md` (new — the runbook itself: exact commands, no personal note content). Everything this task touches on disk beyond that (the scratch vault dir, the scratch `config.toml`/DB) lives **outside the repo entirely** (e.g. `$HOME/.local/share/akasha-dogfood/vault-1/`) — never under this repo's working tree, so it can never be `git add`ed regardless of `.gitignore`.
- **Spec** — §4.11 `GET/POST /sync/roots` (existing endpoint, T4.10 — do not add a CLI verb for it: the literal §4.12 CLI verb list has no sync-root verb, confirmed by grep against `src/akasha/cli/main.py`; register via direct HTTP, e.g. `curl`/`httpx`, exactly as an Obsidian-plugin-less human would today), §4.12 `akasha daemon [--config PATH]` / `akasha token create`.
- **Steps** — (1) Confirm `/data/` is present in the repo's `.gitignore` (already added — this step just verifies, does not re-add). (2) Create a scratch directory outside the repo, e.g. `$HOME/.local/share/akasha-dogfood/vault-1/`. (3) Copy exactly 5 real files verbatim, unmodified, from `data/(10) Concepts/(1) Universal/` into that scratch dir (small, self-contained concept notes — avoid anything under `Personal Workflow/`). (4) Write a scratch `config.toml` next to it with its own `db_path` inside the same scratch tree — never the default `~/.config/tm-daemon/` location, so this can never collide with or pollute a future real production DB. (5) Start `akasha daemon --config <scratch config.toml>`. (6) `akasha token create dogfood-smoke --class human` against that daemon; capture the token. (7) `POST /v1/sync/roots {"name": "dogfood-smoke", "root_path": "<scratch vault dir>"}` via direct HTTP against the running daemon (human-class token; the endpoint is `∅` human-only per §4.11). (8) Write `docs/dogfood/README.md` documenting steps 2–7 as copy-pasteable commands, generalized (no literal personal file names or content — path patterns and command shapes only).
- **Verify** — `GET /v1/sync/roots` against the scratch daemon includes the new root by name; `GET /v1/sync/status` shows 0 violations for it (expected — the 5 files have no `^tm-` anchors yet, so 0 managed blocks, not a bug); `git status --porcelain` run from the repo root shows nothing under the scratch path (it's outside the repo) and confirms `data/` stays untracked.
- **DoD** — a real personal-note directory (5 files, copied verbatim, never git-tracked) is a live, watched sync root on a real running daemon instance backed by a throwaway DB; `docs/dogfood/README.md` exists and its commands are copy-pasteable.

### T11.2 — MANUAL: mark real spans, confirm ingestion, and use it (human-only, story: "then use it")
- **Goal** — A human adds `^tm-new` anchors to a handful of real spans across the T11.1 scratch vault, confirms the daemon's real reconcile pipeline mints real node IDs and rewrites the files in place (no echo — spec §4.7/§4.8), then exercises `akasha search`, `akasha get`, `akasha review list` against the real resulting content, and records what real-world linter/violation behavior looked like against messy real content (existing wikilinks, YAML frontmatter, native non-`^tm-` Obsidian block IDs, meta-bind embeds) versus the synthetic `tests/battery` E01–E20 fixtures.
- **Depends on** — T11.1.
- **Files** — `docs/dogfood/smoke-test-log.md` (new — the human-authored observation record; never the source vault content itself, only summary counts/observations).
- **Spec** — §4.7/§4.8 (contract parse/render, anchor minting, reconcile), §4.11 `GET /search`, `GET /review`, §4.12 `akasha search`/`akasha review list`/`akasha get`.
- **Steps (manual runbook — explicitly not automated, same DoD category as M6's `plugin-obsidian/TESTPLAN.md`)** — (1) In 2–3 of the T11.1 scratch vault's files, hand-pick a real span the human actually wants tracked and add a `^tm-new` anchor per §4.7 grammar. (2) Save; let the daemon's watcher pick it up (or `POST /v1/sync/rescan`). (3) Confirm the file was rewritten in place with a real minted `^tm-<id>` anchor (no echo — the anchor changes, the body text doesn't). (4) `akasha search <a real term from the captured text>` — confirm the new node comes back. (5) `akasha get <minted id>` — confirm body/facets match. (6) `akasha review list` — confirm no unexpected violations from the *unrelated* real content in the same files (existing wikilinks, frontmatter, native Obsidian `^block-id`s must be ignored by the linter, not misparsed as akasha anchors). (7) Record in `docs/dogfood/smoke-test-log.md`: node count minted, any violation/linter codes actually hit, and a one-line verdict on real-content parser robustness.
- **Verify** — N/A (manual leg — same framing as M0/M6/M8/M9/M10's human/CI-pending legs already in this plan). DoD is the completed, dated log entry.
- **DoD** — at least 1 real node exists in the scratch DB, minted through the genuine sync pipeline from the user's own vault content, confirmed retrievable via `akasha search`/`akasha get`; observations logged in `docs/dogfood/smoke-test-log.md`.

### T11.3 — Wire filesystem discovery for newly registered sync roots

- **Goal** — Close the gap T11.1 surfaced and logged in `docs/spec-questions.md`: `POST /v1/sync/roots` is a pure DB upsert with no filesystem walk, and both `reconcile.reconcile_all` (daemon startup) and `POST /v1/sync/rescan` only iterate already-known `store.list_sync_files` rows — never a root's actual directory. A brand-new root with real, pre-existing `.md` files on disk therefore shows `files_reconciled: 0` / `"files": []` forever, because nothing ever calls `Reconciler.on_change` on a path the store has never seen before. Add discovery so both entry points also pick up files that exist on disk but have no `sync_files` row yet.
- **Depends on** — T11.1.
- **Files** — `src/akasha/sync/reconcile.py`, `src/akasha/api/routes/sync.py`, `tests/integration/test_crash_recovery.py` (extend with the `reconcile_all` discovery test), `tests/integration/test_api.py` (extend with the `sync_rescan` discovery test). Both test files are in scope — do not add a third.
- **Spec** — §4.8 "Startup: run `on_change` for every managed file (idempotent — this is also crash recovery)" and §4.11 `POST /sync/rescan`; neither text limits "every managed file" to rows already in `sync_files`, so walking each registered root's directory for files `on_change` has never seen is the narrowest reading that makes startup/rescan match what the spec prose actually says, not a new endpoint or schema (rule 2).
- **Steps** — (1) In `reconcile.py`, add a helper that, for each row from `store.list_sync_roots(conn)`, walks `Path(root["root_path"]).rglob("*.md")` — the same idiom `Reconciler` already uses internally (see its "other `*.md` file under the same sync root" conflict-candidate scan) — and yields any absolute path not already present in `{f["path"] for f in store.list_sync_files(conn)}`. (2) In `reconcile_all`, call this helper and run `reconciler.on_change(path)` on each newly discovered path exactly like the existing known-file loop (same try/except `FileNotFoundError` handling, same `files_reconciled`/`files_missing` counters — a file that vanishes between the walk and the read is not a crash). (3) Apply the same discovery step to `routes/sync.py`'s `sync_rescan` (it currently duplicates `reconcile_all`'s loop rather than calling it — either add the same walk-and-append step there, or refactor `sync_rescan` to call `reconcile.reconcile_all` directly if that stays a single, focused change; if it isn't, duplicate the walk step instead of refactoring). (4) Do not touch `sync/watcher.py` — `Watcher` is a live-event listener, not a startup/rescan discovery mechanism, and wiring it into `daemon.serve()` is a separate concern this task does not need to touch to close the T11.1 gap. (5) Add a test reproducing T11.1's exact empirical repro: register a sync root pointing at a tmp dir containing pre-existing `.md` files never passed through `on_change`, call `reconcile_all` (or hit `POST /sync/rescan`), and assert `files_reconciled` now counts them and `store.list_sync_files` has rows for them — plus one test confirming a second call is idempotent (no duplicate `sync_files` rows, no duplicate node mints for unchanged content).
- **Verify** — `uv run pytest tests/integration/test_crash_recovery.py tests/integration/test_api.py` all passing, plus `make check` and `make battery` per rule 0.7.
- **DoD** — registering a sync root against a directory with pre-existing `.md` files, then calling `reconcile_all` or `POST /v1/sync/rescan`, discovers and reconciles those files on the very first call (not just after a live watcher event fires on each one individually); the T11.1-era `docs/spec-questions.md` entry for this gap is updated with **Resolution:** pointing at this task and the landing commit.

### T11.4 — Scaled dogfood ingestion smoke test (1 → 10 → 100 real notes)

- **Goal** — Extend T11.1's mechanical-plumbing precedent to increasing scale, to answer "does the sync/reconcile pipeline hold up against something closer to the founder's real vault size" — **without** making any content-curation decision. This task is explicitly **content-blind**: it appends one fixed, deterministic managed block (a single mechanically-generated `claim`-type anchor, same literal wording template for every file, no reading or interpretation of the note's real content) to each copied file, purely so discovery → anchor-mint → write-back → node-creation → review-queue behavior actually exercises at scale instead of the T11.1 result (0 anchors, 0 nodes at any scale). **This is not T11.2.** Deciding which real spans the user actually wants tracked as claims/entities stays exclusively human (`docs/vision.md` PRD §5 F-list, R9) — this task creates zero nodes derived from real note *meaning*, only from a fixed, content-independent template, and must say so plainly in its report so a green result is never mistaken for "the vault is now usable," which remains T11.2's human call.
- **Depends on** — T11.3 (without it, discovery never fires and every scale reports 0 files reconciled, making the test vacuous — see T11.1's own empirical finding).
- **Files** — `docs/dogfood/scaled-smoke-report.md` (new — the report; counts/timings/observations only, same no-real-content-leak discipline as `docs/dogfood/README.md`). Everything else this task touches (three scratch vault dirs, scratch config/DB) lives outside the repo, same as T11.1.
- **Spec** — §4.7/§4.8 (anchor grammar, reconcile), §4.11 `GET /sync/status`, `GET /dashboard`/§4.9 metrics (`rss_bytes`, `sync_cycle_ms`).
- **Steps** — (1) Create three scratch vaults under `$HOME/.local/share/akasha-dogfood/` (e.g. `vault-scale-1`, `vault-scale-10`, `vault-scale-100`), each a **fresh copy** (not cumulative) of the first N files (by sorted path, mechanical selection — no content judgment) under `data/(10) Concepts/`, N ∈ {1, 10, 100} (confirmed 2026-07-25: `find "data/(10) Concepts" -name '*.md' | wc -l` = 432, so all three scales are satisfiable from real files — no need to pad or substitute). (2) For each copied file, mechanically append one fixed managed block using the exact `^tm-new` anchor grammar (§4.7) around a fixed placeholder claim body — identical template string for every file, substituting nothing from the real note text. (3) For each scale, on its own scratch daemon/DB (never reused across scales, never the default config dir): register the vault as a sync root, call `POST /v1/sync/rescan` (now functional per T11.3), and record: files reconciled, nodes minted, wall-clock time for the rescan call, daemon RSS before/after (`GET /dashboard` or `ps`). **Catalogue every review-queue violation/linter code that fires, and on how many files, at each scale — do not assume or report "none".** The appended blocks are well-formed by construction, but the reconciler parses the *whole* file (real YAML frontmatter, wikilinks, native Obsidian `^block-id`s, meta-bind embeds it was never tested against), and whether that messy real content trips the linter is the one genuinely new signal this content-blind test can produce — a bare "0 violations" is only meaningful if the report shows it was actually counted, not assumed. (4) Confirm zero nodes were derived from real note content at any scale (spot check: the minted node bodies are all the one fixed template string, not vault text). (5) Write `docs/dogfood/scaled-smoke-report.md` with a table of the three scales' results (including the violation catalogue from step 3) and one explicit closing line: "This validates sync/reconcile mechanics and daemon health at scale; it makes no claim about content usability — that is T11.2, still open, still human-only."
- **Verify** — N/A (manual/live-daemon leg, same framing as T11.2 and M9/M10's human/CI-pending legs). A `fleet-verifier` picking this up independently re-checks the report's row counts (files reconciled, nodes minted) against `store.list_sync_files`/a live node query in each scratch DB — it does not need to re-run all three scales from scratch to confirm the numbers are real. DoD is the completed, dated report with all three scales' rows filled in from real runs, not estimated.
- **DoD** — `docs/dogfood/scaled-smoke-report.md` exists with real (not projected) results at N=1, 10, and 100 files, a real (possibly empty, but actually-checked) violation catalogue per scale, no crashes or unbounded RSS growth across the three runs, and the report's closing line explicitly disclaims any content-usability conclusion, leaving that call to T11.2.

---

## Expandability guardrails (build-now-use-later — do NOT implement future phases)

These are constraints on the tasks above, not tasks themselves (spec §8):

- Keep the **agent-token → review-queue proposal pathway** (T4.6) intact; reserve `cause_kind=proposal` rendering. It is the Phase 3 decomposer's entry point.
- Keep `api/schemas.py` **re-exportable** (T4.3) so a Phase 4 MCP facade can import only the HTTP API.
- The `tms/triggers.py` registry (T7.3) is the future host boundary — **do not add a script runner now**.
- All state stays **content-addressed with per-commit parents** (M1). **Never introduce a global sequence counter** (multi-device/CRDT-friendliness).
- Treat the **golden corpus, OpenAPI snapshot, and no-pickle/canonical-bytes rules as sacred** (rule 0.3) — they are the Rust-migration enablers.

**Explicit MVP non-goals — do not build even if easy:** LLM calls, embeddings, MCP server, mobile, multi-user, task scheduling/recurrence, prose management.

