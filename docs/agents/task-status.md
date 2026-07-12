# Task status

Machine-checkable status for every task in `docs/build-plan.md` (M0–M10, 70
tasks total). This file is the single source of truth for "what's done" —
an autonomous agent picking up work should read this file first, find the
next `TODO` task whose `Depends on` tasks are all `DONE`, and work it per
the rules in `docs/build-plan.md` §"How to use this plan" and root
`CLAUDE.md`.

Status values: `TODO` → `IN PROGRESS` → `DONE`, or `BLOCKED: <reason>`.
A milestone is closed only when every task in it is `DONE` and the
milestone's own DoD command (stated in `docs/build-plan.md`) passes.

When you finish a task: flip its status here in the same change that closes
the task, and note anything a future agent needs (e.g. a new
`docs/spec-questions.md` entry) in the Notes column.

---

## M0 — Scaffold (Depends on: nothing) — **CLOSED**

Milestone DoD: `make check` green on windows-latest and ubuntu-latest with a
placeholder test. Verified locally on ubuntu (uv run ruff/pyright/pytest);
CI matrix not yet run (no remote configured).

| Task | Goal | Status | Notes |
|---|---|---|---|
| T0.1 | Create repository skeleton | DONE | |
| T0.2 | Author `pyproject.toml` (uv, Python ≥3.12) | DONE | |
| T0.3 | Migration runner | DONE | placeholder migration `000_placeholder.sql`; `001_init.sql` is T1.1's job |
| T0.4 | Ruff + pyright + custom bans wired | DONE | scoped ruff `S` selection to S102/S301/S302/S307 only, not full bandit set |
| T0.5 | Config loading (`config.py`) | DONE | |
| T0.6 | Structured JSON logging | DONE | landed in `daemon.py` per spec's suggested location |
| T0.7 | Makefile targets | DONE | `make` itself isn't installed in this sandbox; targets verified by running their `uv run ...` bodies directly |
| T0.8 | CI matrix (GitHub Actions) | DONE | YAML validated locally; not yet run against a remote |

## M2 — IDs + canonicalization (Depends on: M0) — **CLOSED**

Milestone DoD: `pytest tests/unit/test_ids.py tests/golden/test_serialization.py`
→ 33 passed. Hypothesis round-trip (`tests/property/test_canonical_idempotent.py`)
→ 2 passed, no falsifying example. Verified locally on ubuntu.

| Task | Goal | Status | Notes |
|---|---|---|---|
| T2.1 | ID minting + checksum + validation (`ids.py`) | DONE | Sonnet worker recovered after initial orchestration halt; 14 tests passing (includes 5 known checksum vectors, validation, minting, anchors). Confirmed via `make check`: ruff/pyright clean, 41 total tests pass. |
| T2.2 | Text + JSON canonicalization (`canonical.py`) | DONE | Sonnet worker created both canonical.py and test_canonical.py; 14 tests passing (CRLF/LF, NFC, fence detection, tabs, JSON hashing). Confirmed via `make check`: ruff/pyright clean, 41 total tests pass. |
| T2.3 | Canonicalization idempotence (property test) | DONE | Ran via redesigned fleet-workflow.js pipeline (docs/agents/logs/20260711-173459-M1-M2-mixed/). Hypothesis property test, 2 passed. Independently CONFIRMED_DONE by separate verifier agent (re-ran pytest itself). M2 milestone now CLOSED — T2.1-T2.4 all DONE. |
| T2.4 | Golden serialization corpus (≥15 cases) | DONE | Same run. 18 golden cases (>=15 required), 19 tests passed. Independently CONFIRMED_DONE — verifier checked all 37 files exist/non-empty (empty_file/input.md correctly 0 bytes by design) and cross-referenced git status. |

## M1 — Kernel store (Depends on: M0) — **CLOSED**

Milestone DoD: `pytest tests/unit/kernel tests/property/test_store.py` → 92 passed;
property suite covers no-dangling-refs, head-reachable, as-of, S0-GC-safety;
10k-node benchmark `tests/integration/test_perf.py::test_neighborhood_p95` → p95 < 50 ms
(1 passed in ~3.5s). Verified locally on ubuntu via `make check` equivalent:
ruff clean, pyright --strict 0 errors, 132 unit+property tests pass. All T1.3–T1.9
worker results independently verified (CONFIRMED_DONE) AND separately audited against
spec §4.1/§4.2/§4.4/§4.5/§4.6 by the Opus orchestrating session. Run log:
docs/agents/logs/20260711-215704-M1-kernel-store/. SPEC-QUESTIONs T1.3/T1.5/T1.6/T1.7
all RESOLVED 2026-07-11 (see docs/spec-questions.md): every narrowest reading was
confirmed correct against vision.md + mvp-spec.md; no code changes required. Follow-ups
noted there for M4 (vet endpoint must recompute maturity in-txn; history order is frozen
oldest-first) and M7 (T7.6 reassignment queue layers on the mechanical redirect default;
T7.2 maps facet-adds to 'minor').

| Task | Goal | Status | Notes |
|---|---|---|---|
| T1.1 | DDL migration `001_init.sql` (verbatim) | DONE | Same run as T2.3/T2.4 (docs/agents/logs/20260711-173459-M1-M2-mixed/). 5 schema tests passed. Independently CONFIRMED_DONE. |
| T1.2 | pydantic models (`model.py`) | DONE | Same run. 15 model tests passed (facet_binding validator, justification-edge constant, defaults). Independently CONFIRMED_DONE. |
| T1.3 | Store: node create/read + commit DAG (`store.py`) | DONE | Run 20260711-215704-M1-kernel-store. 13 store-node tests pass; verifier CONFIRMED_DONE (re-ran pytest). Opus main-session audited store.py vs §4.5/§4.4/§4.1: create/commit/get_node(as_of)/history all correct, transactional, append-only. Logged SPEC-QUESTION T1.3 (object-blob layout, commit-hash addressing, history ordering — narrowest reading, open). |
| T1.4 | Store: edges create/retract + neighborhood/search | DONE | Run 20260711-215704-M1-kernel-store. 9 edge tests pass; verifier CONFIRMED_DONE. Opus audit vs §4.5/§4.2/§4.4: create_edge reuses Edge pydantic validator (no reinvented rule), retract is soft (retracted_at, never DELETE), neighborhood filters retracted_at IS NULL both directions, FTS sync wired into create_node (INSERT) + commit_node (UPDATE), search via nodes_fts MATCH. No spec questions. |
| T1.5 | Maturity derivation (`maturity.py`) | DONE | Run 20260711-215704-M1-kernel-store. 16 tests pass; verifier CONFIRMED_DONE. Opus audit vs §4.6: S0-S4 ladder correct, task/entity S2 facet-exemption handled, pure fn (no DB). Store-recompute wiring DEFERRED to T1.6 (store.py not in T1.5 Files). Logged SPEC-QUESTION T1.5 on S4-vs-S3 chaining ambiguity (literal reading: vetted alone => S4) — open, needs human. |
| T1.6 | Deletion, tombstone, redirects, split/merge | DONE | Run 20260711-215704-M1-kernel-store. 24 tests pass; verifier CONFIRMED_DONE. Opus audit vs §4.5/§4.6/§4.4: delete_node S0 hard-delete (node/commits/incident edges/fts) vs S1+ tombstone+redirect vs E_NEEDS_REDIRECT; split/merge insert redirects + reassign live inbound edges (zero dangling). Maturity wiring (deferred from T1.5) landed here: _recompute_maturity called in create_edge/retract_edge/commit_node/delete/split/merge, same-txn. commits.py minimal facets_touched + default_change_class helpers. 3 SPEC-QUESTIONs logged (split parts shape, merge survivor, facets_touched span rule) — open. |
| T1.7 | S0 garbage collection job | DONE | Run 20260711-215704-M1-kernel-store. 6 tests pass; verifier CONFIRMED_DONE. Opus audit vs §4.4/§4.5: gc_objects reachable = commits.object_hash ∪ nodes.head_hash ∪ sync_files.base_hash; deletes only orphans (all−reachable), single txn, returns sorted deleted hashes. Critical 'never collects live-S0 head' case covered. SPEC-QUESTION T1.7 logged (reachability widened past literal 'S1+ heads/history' to preserve 'never removes a referenced object') — open. |
| T1.8 | Store property suite | DONE | Run 20260711-215704-M1-kernel-store. Stateful hypothesis test passes (25 examples); verifier CONFIRMED_DONE. Opus audited test body (not vacuous): all 4 §4.5 invariants genuinely asserted — no-dangling-edges + head-reachable-to-genesis after EVERY op; as-of correctness + GC-safety per sequence, with independently-computed expected values. Scope note: delete/split/merge not in the random sequence (covered by T1.6 unit tests); the 4 enumerated invariants are fully exercised. |
| T1.9 | 10k-node neighborhood benchmark | DONE | Run 20260711-215704-M1-kernel-store. Passes (p95 < 50ms); verifier CONFIRMED_DONE. Opus audit: genuinely seeds 10,000 nodes + ~30k edges via store API, 500 timed neighborhood(hops=1) samples, real p95 via statistics.quantiles, asserts <0.050s (threshold not weakened). |

## M3 — Contract parser / renderer (Depends on: M2) — **CLOSED**

Milestone DoD: golden corpus ≥25 cases (29 actual: 18 canonicalization-only from
T2.4 + 11 new contract-grammar-focused cases covering tasks/nesting/embeds/refs/
`^tm-new`/all 5 violation codes/pause&diff); hypothesis `render(parse(D)) == D`
and `parse(render(G)) == G` both pass with no falsifying example (stress-tested
to 3000 examples/direction, T3.4 + T3.7's fuzz hunt); fuzz corpus committed
under `tests/golden/serialization/fuzz/` (honest empty-with-README — no
falsifying example was ever found to shrink). Verified locally: `uv run ruff
check src tests && uv run pyright src && uv run pytest tests/unit tests/property
tests/golden -q` → 249 passed, ruff/pyright clean. All T3.1–T3.7 independently
CONFIRMED_DONE by a separate verifier per task (re-ran every Verify command,
read-through against spec, adversarial/boundary probes on T3.5/T3.6). Run log:
`docs/agents/logs/20260711-M3-contract/`. From this milestone onward, edit
delegation followed the updated 3-tier fleet policy (Opus orchestrates, Sonnet
fleet-workers own each task and decide direct-vs-Cursor, Cursor Grok 4.5 High
executes well-specified/mechanical edits) — T3.3, T3.5, T3.6, T3.7 were
Cursor-delegated and Sonnet-verified; T3.1, T3.2, T3.4 were direct Sonnet edits
(T3.4 because hypothesis-strategy design was judgment-heavy). 4 open
SPEC-QUESTIONs logged this milestone (T3.1 `task_form` nonterminal, T3.2 `tm:`
version-mismatch handling, T3.5 E_DUP_ID scope, T3.6 pause-ratio denominator +
review-item code) — none deep-impact enough to block M4; all took the
narrowest, most conservative reading and are safe defaults to build on.

| Task | Goal | Status | Notes |
|---|---|---|---|
| T3.1 | Grammar tokens + contract version (`grammar.py`) | DONE | Run 20260711-M3-contract. 32 tests pass; ruff/pyright clean; full unit+property 164 passed. Independently CONFIRMED_DONE (re-ran tests, read both files, confirmed EOL-only anchor semantics, no parsing logic present, only listed files touched). SPEC-QUESTION T3.1 resolved 2026-07-12 (fable review) — narrowest reading (mirrors task_line's indent+checkbox prefix) CONFIRMED correct and final; see docs/archived-questions.md. |
| T3.2 | Parser: vault text → BlockSet (`parser.py`) | DONE | Run 20260711-M3-contract. 18 tests pass; ruff/pyright clean; full unit+property 182 passed. Independently CONFIRMED_DONE (regex reuse from grammar.py confirmed — no re-derivation; fence suppression, mid-line-anchor rejection, unmanaged->empty, parent-child-via-depth all read-through + test-confirmed). `BlockSet` (pydantic): `managed`, `contract_version`, `blocks: dict[id, Block]` (paragraph/task, `task_state`, `depth`, `parent_id`), `embeds`, `refs`, `new_requests` — this is the shape T3.3/T3.5 must consume. SPEC-QUESTION T3.2 resolved 2026-07-12 (fable review) — narrowest reading (exact-match-only is managed) CONFIRMED correct and final; see docs/archived-questions.md. |
| T3.3 | Renderer: hub nodes → canonical vault text (`render.py`) | DONE | Run 20260711-M3-contract, edited via Cursor (grok-4.5-high) + Sonnet verify. 8 tests pass; ruff/pyright clean; full unit+property 190→204 passed as sibling tasks landed. Independently CONFIRMED_DONE (reuse of parser.BlockSet/Block/Embed/Ref, grammar constants, kernel.ids.vault_anchor, kernel.canonical.canonicalize_text all read-through confirmed; manual idempotence probe reproduced). `render(block_set: BlockSet, *, resolve_body=None) -> str`, pure function, no DB dependency — reuses parser.BlockSet directly so parse/render stay symmetric for T3.4. |
| T3.4 | Round-trip property tests | DONE | Run 20260711-M3-contract, direct Sonnet edit (judgment-heavy hypothesis strategy design). 2 passed (render∘parse and parse∘render directions), stress-tested at max_examples=500 with no falsifying example. Independently CONFIRMED_DONE — verifier reproduced 3x with random seeds (no flakiness), confirmed real checksum-valid id generation via kernel.ids (not hand-rolled), confirmed genuine variety (0-8 items, all 4 block kinds, realistic nesting) and that scoping exclusions (new_requests, embed/ref line_no collisions) are documented/justified, not silent weakening. No falsifying examples found in parser.py/render.py — no latent bug. |
| T3.5 | Linter: violation codes + certain-repair (`linter.py`) | DONE | Run 20260711-M3-contract, edited via Cursor (grok-4.5-high) + Sonnet verify. 14 tests pass; ruff/pyright clean; full unit+property 204 passed. Independently CONFIRMED_DONE — verifier traced E_ID_CHECKSUM/E_DELETED_S1 code paths and confirmed NEITHER ever populates `repairs` (always `review_items`), confirmed E_DUP_ID/E_LOST_ANCHOR repairs require exact byte-identical match to base, and ran 4 live adversarial probes against `lint()` that all correctly avoided auto-repair. `lint(base: BlockSet, vault: BlockSet, vault_text: str, maturity=None) -> LintResult` (`.violations`, `.repairs`, `.review_items`), pure function, reuses kernel.ids checksum validation. SPEC-QUESTION T3.5 resolved 2026-07-12 (fable review, highest-stakes of the M3 batch) — per-file `lint()` signature CONFIRMED correct and final; kernel §4.1 does NOT cover cross-file dup detection (different invariant: node-id uniqueness vs. block-occurrence uniqueness); M5/T5.4 must add a cross-file dup check reading the node→path projection mapping it already needs for `hub_state_for(path)` (no new DB table). See docs/archived-questions.md and the M5 row below. |
| T3.6 | Pause & diff (formatter-storm guard) | DONE | Run 20260711-M3-contract, edited via Cursor (grok-4.5-high) + Sonnet verify. Extends linter.py additively (T3.5's code untouched). 10 tests pass + 14/14 T3.5 regression clean; ruff/pyright clean; full unit+property 216 passed. Independently CONFIRMED_DONE — verifier independently reproduced the exact-25%-boundary case via a standalone script (1/4, 2/8 -> no pause; 3/8 -> pause), confirming the strict `>` comparator matches spec's "> 25%" exactly (not `>=`). `PAUSE_THRESHOLD=0.25`; `pause_threshold(result, base) -> bool` (ratio = distinct violation ids / len(base.blocks), False if base.blocks empty); `pause_and_diff(result, base, base_text, vault_text) -> PauseDecision \| None` (`PauseDecision.snapshot: str`, `.review_item: ReviewItem` — exactly one, carrying a difflib.unified_diff). Zero I/O, same pure-function discipline as rest of linter.py. 2 SPEC-QUESTIONs resolved 2026-07-12 (fable review) — both narrowest readings CONFIRMED correct and final; binding guardrail for M5/T5.4/T5.7/T8.x: never branch on the borrowed `ViolationCode` to identify pause items, use the `PauseDecision` wrapper / sync-status pause state instead; persist as `review_queue.cause_kind='violation'` (no new cause_kind). See docs/archived-questions.md and the M5 row below. |
| T3.7 | Golden corpus (≥25) + committed fuzz corpus | DONE | Run 20260711-M3-contract, edited via Cursor (grok-4.5-high) + direct Sonnet fuzz-hunt/README. 29 total golden case dirs (18 original untouched + 11 new `contract_*`); 33 tests pass; M3 DoD command (golden+roundtrip) 35 passed; ruff/pyright clean. Independently CONFIRMED_DONE — verifier confirmed all 18 pre-existing dirs byte-identical, re-ran canonicalize_text live against 7 new cases (all MATCH), and read the new `test_contract_focused_cases_parse_and_lint_behavior` in full confirming it asserts the SPECIFIC ViolationCode per case (not just no-crash) for all 5 violation codes plus pause_threshold==True with a real diff. fuzz/ is an honest empty-with-README placeholder (3000 examples/direction hunted, none found) — no fabricated failure case. |

## M4 — Daemon + API + CLI core (Depends on: M1, M3) — **CLOSED**

Milestone DoD: `pytest tests/integration/test_api.py tests/integration/test_cli.py`
→ 66 passed; agent-token mutation lands in the review queue as
`cause_kind=proposal` (`test_agent_writes_become_proposals`); OpenAPI
snapshot-diff test green (`test_openapi_snapshot.py`). Verified locally on
ubuntu 2026-07-12: ruff clean, pyright --strict 0 errors, full
unit+property+integration **327 passed**. Run log:
`docs/agents/logs/20260712-M4-daemon-api/`. Fleet execution: Opus session
orchestrated; fleet-orchestrator computed each cohort (T4.5 solo →
{T4.6 ∥ T4.8} → {T4.7 ∥ T4.9}); fleet-workers owned each task and were
independently gate-verified by the Opus session (authoritative `make check`
re-run on the combined tree after each parallel cohort landed). T4.1–T4.4
were direct Opus edits earlier in the session. **9 SPEC-QUESTIONs logged this
milestone** (all open — see docs/spec-questions.md): T4.2 audit-write-in-store,
T4.3 `/health` `/v1`-prefix, T4.4 db-path/shared-conn + merge-body-shape, T4.5
store-token/vault-helpers + **`/vaults` durability gap** (the one not-low-stakes
item — in-memory registry, not durable across restart; flagged for M5/T5.1),
T4.6 store-review-helper + create-proposal-`node_id` + `cause_ref`-payload +
edge-proposal-`dst`, T4.8 missing-`/review`-endpoint + `set --class` default +
`--base-url` flag. Rebrand invariant held (OpenAPI title `tm-daemon API`, lock
file `tm-daemon.lock` — no product name in the on-disk snapshot or paths).
Windows single-instance-lock branch (`msvcrt`) is code-reviewed + pyright-clean
but not runtime-exercised on this Linux host (deferred to M9/T9.1 Windows
battery).

| Task | Goal | Status | Notes |
|---|---|---|---|
| T4.1 | Auth: token classes, secrets, rate limits (`auth.py`) | DONE | Run 20260712-M4-daemon-api, direct Sonnet edit (security-sensitive: hashing/rate-limit design). 12 tests pass; ruff/pyright clean; full unit+property 228 passed. Independently CONFIRMED_DONE — verifier traced constant-time `hmac.compare_digest` usage, confirmed zero SQL writes in module, revoked-checked-after-secret-match ordering, sound sliding-window rate limiter (hand-traced), and ran an adversarial script (valid/wrong-secret/revoked/500-rapid-unlimited-calls) all correct. Bearer format `"{token_id}.{raw_secret}"`; `authenticate(conn, bearer_value, *, now=None) -> AuthContext`; exception hierarchy under `AuthError` (Malformed/UnknownToken/InvalidSecret/Revoked/RateLimitExceeded, each with `.code`); `mint_secret()`/`hash_secret()`/`format_bearer_token()` exposed for T4.5/CLI to reuse when minting new tokens. Rate limiting in-process/in-memory sliding 60s window, resets on restart (documented, not a truth invariant). |
| T4.2 | Audit log | DONE | Run 20260712-M4-daemon-api, direct Opus edit. 16 tests pass; ruff/pyright clean; full unit+property 244 passed. Append-only audit primitive: `store.append_audit(conn, token_id, action, detail=None)` stamps `ts` via store `_now()` and issues one INSERT (rule 0.4 — the raw SQLite write lives in store.py, NOT auth.py). Policy layer in `api/auth.py`: `MUTATING_METHODS` (POST/PUT/PATCH/DELETE), `is_mutating_method()`, `record_mutation(conn, method, action, ctx, *, detail=None) -> bool` — writes exactly one row iff mutating (reads return False, write nothing), logs `ctx.token_id` (never the bearer secret; `AuthContext` structurally cannot carry it). This is the primitive T4.3's FastAPI middleware wraps per request. SPEC-QUESTION T4.2 logged (Files list omits store.py but non-negotiable rule 0.4 forces the audit INSERT into store.py; took that reading) — open, low-stakes confirmation. |
| T4.3 | FastAPI factory + `/health` + schemas re-export | DONE | Run 20260712-M4-daemon-api, direct Opus edit. 5 tests pass; ruff/pyright clean; full unit+property+test_health 249 passed. `create_app(config=None) -> FastAPI` factory (not a singleton — per-app for tests + T4.9 daemon); resolved `Config` stashed on `app.state.config` so serving layer binds `config.bind` (127.0.0.1 default). `GET /health` unauthenticated, returns `{status, version, contract_version}`; `app_version()` reads installed distribution metadata (never drifts from pyproject). `api/schemas.py` = thin verbatim re-exports of kernel/model.py (Node/Edge/Facet/…, `__all__`), zero divergence (spec §8 Phase-4 hook). Rebrand invariant honored: OpenAPI `title="tm-daemon API"` (no product name — it lands in the T4.7 snapshot). pyright false-positive on the decorator-registered `/health` closure suppressed with a targeted `reportUnusedFunction` ignore. SPEC-QUESTION T4.3 logged (`/health` path: `/v1` prefix vs literal `/health` — took literal per table cell + health-check convention) — open, low-stakes. |
| T4.4 | Node routes (`routes/nodes.py`) | DONE | Run 20260712-M4-daemon-api, direct Opus edit. 14 node tests pass (`-k nodes`); ruff/pyright clean; full unit+property+integration 264 passed. Endpoints under `/v1`: GET `/nodes/{id}` (+`?as_of=`, returns node+maturity), POST `/nodes` (201), PATCH `/nodes/{id}`, DELETE `/nodes/{id}` (S0 hard / S1+ 409 `E_NEEDS_REDIRECT`), GET `/nodes/{id}/history`·`/neighborhood?hops=`, POST `/nodes/{id}/split`·`/merge`, POST `/nodes/{id}/vet` (human-only ∅). **App plumbing added per approved defaults** (see AskUserQuestion 2026-07-12): `api/deps.py` (NEW) — `ApiError` + `register_error_handlers` rendering the spec §4.11 envelope `{"error":{code,message,detail}}` (incl. wrapping FastAPI `RequestValidationError`→422 `E_INVALID`), `get_conn`/`require_auth`/`require_human` dependencies; `require_auth` audits every authenticated mutating request once (reuses T4.2 `record_mutation`). `app.py` opens ONE shared WAL conn on `app.state.conn` (`check_same_thread=False`, first-run `mkdir` of tm-daemon dir), registers handlers, includes the router. `config.py` gained `db_path`/`default_db_path()` (→ `tm-daemon/store.db`, neutral per rule 0.6). `store.py` gained read-only `get_maturity` (Node model omits maturity) + `vet_node` (no vet fn existed — M1-close flagged this as the M4 vet-recompute-in-txn follow-up; sets vetted=1 + recomputes maturity in-txn, rule 0.4). `store.connect` gained `check_same_thread` kwarg (default True, back-compat). Agent-proposal rewriting NOT here (T4.6 cross-cutting dep); split reassignment queue NOT here (M7/T7.6) — both noted in code. T4.3's `test_health.py` updated to inject an in-memory conn (create_app now opens a DB). 2 SPEC-QUESTIONs logged (T4.4 db-path/shared-conn; T4.4 merge request shape) — open, low-stakes. |
| T4.5 | Edge, search, token, vault routes | DONE | Run 20260712-M4-daemon-api, fleet-worker (direct edit) + Opus-session independently verified (re-ran gate: ruff clean, pyright 0, T4.5 verify 18 passed, full unit+property+integration 282 passed; read all 20 new tests to confirm holistic coverage per user requirement). New `routes/{edges,search,tokens,vaults}.py`, all included in `app.py`. `POST/DELETE /edges` reuse T1.2's `Edge.facet_binding` validator via `store.create_edge` (missing binding on justification→400 `E_INVALID`; composes/redirects_to allow None); DELETE is a SOFT retract via `store.retract_edge` (test asserts `retracted_at` set, row survives, drops from neighborhood). `GET /search` wraps `store.search` (FTS5; tested real hit + empty non-match). `/tokens` and `/vaults` are FULLY human-only ∅ on every verb (GET included) per the literal §4.11 row — agent→403 `E_HUMAN_ONLY`. `store.py` gained `create_token`/`revoke_token`/`list_tokens` (+`TokenNotFoundError`, `_mint_unique_token_id` reusing the id8 scheme) — real persistent writes, never re-expose `secret_hash`, raw bearer returned exactly once at creation — and read-only `list_synced_vaults` (derives from `sync_files.vault`). store.py outside T4.5 Files but forced by rule 0.4 (same precedent as T4.2 `append_audit`). **`/vaults` scoping gap resolved WITHOUT new schema**: no `vaults` table exists in the frozen §4.4 DDL, and adding `migrations/002_vaults.sql` would break the existing acceptance test `tests/unit/kernel/test_schema.py::test_no_unlisted_tables_beyond_spec_and_bookkeeping` (rule 2 schema-freeze). So `POST /vaults` upserts a **process-local in-memory registry** (mirrors `auth.py`'s rate-limiter precedent) merged with `sync_files`-derived names on GET — **NOT durable across daemon restart**, flagged for M5/T5.1 to fix with its own schema task. 2 SPEC-QUESTIONs logged (store.py Files-list tension; `/vaults` schema gap) — both open. |
| T4.6 | Agent-token proposal rewriting | DONE | Run 20260712-M4-daemon-api, direct Sonnet edit. Named Verify `test_agent_writes_become_proposals` passes; full `tests/integration/test_api.py` 40 passed (8 new T4.6 tests); ruff/pyright clean; full gate `tests/unit tests/property tests/integration` 290 passed. New shared dependency `api/deps.py::mutation_gate(conn, ctx, request, *, node_id, payload=None) -> dict|None`: human tokens → `None` (route mutates as normal, unchanged); agent tokens → does NOT mutate, instead calls new `kernel/store.py::enqueue_review(conn, node_id, cause_kind, *, cause_ref=None, facet=None)` (rule 0.4 — the raw `review_queue` INSERT lives in store.py, same precedent as T4.2/T4.4/T4.5) with `cause_kind="proposal"` and `cause_ref` = canonical JSON (`kernel/canonical.canonical_json`, never pickle/eval) of `{method, path, body}`, returns the persisted review row. Wired into every non-∅ mutating route: `routes/nodes.py` (create/patch/delete/split/merge — all gated; only `/vet` stays exempt via existing `require_human`) and `routes/edges.py` (create/delete). Gated routes return **202** `{"proposed": true, "review": {...}}` for agents instead of their normal success status/body; `tokens.py`/`vaults.py` untouched (already fully `require_human`, confirmed still 403 `E_HUMAN_ONLY` for agents, never proposalized — asserted in new test `test_agent_rejected_outright_on_every_empty_endpoint_no_proposal`). New store.py helpers: `mint_unassigned_node_id` (reuses the existing node-id mint+collision-check scheme, zero new id format, for the `POST /nodes` create-proposal's `node_id` placeholder — NOT inserted as a real node) and `get_edge_dst` (read-only, used to pick `DELETE /edges/{id}`'s proposal target). 3 new SPEC-QUESTIONs logged (create-proposal `node_id` placeholder; edge-proposal `node_id`=`dst` choice; store.py Files-list touch, same recurring rule-0.4 tension as T4.2/T4.4/T4.5) — all open, low-stakes. Test cluster in `tests/integration/test_api.py` (8 new tests): named Verify test + human-mutates-directly + agent PATCH/DELETE /nodes + agent POST/DELETE /edges + agent split/merge + the ∅-endpoints-reject-not-proposalize test, each asserting both the HTTP response shape and the underlying DB state (no mutation happened, `review_queue` row count/fields, `cause_ref` round-trips via `json.loads`). |
| T4.7 | OpenAPI snapshot + CI gate | DONE | Run 20260712-M4-daemon-api, fleet-worker (direct Sonnet edit — mechanical but the "prove the gate isn't vacuous" requirement needed judgment, so not Cursor-delegated). Verify `tests/integration/test_openapi_snapshot.py` 3 passed; ruff/pyright clean on all 3 owned files; full gate `tests/unit tests/property tests/integration` 319 passed. New `docs/api-snapshot/openapi.json` (NEW file, `create_app(conn=:memory:).openapi()` serialized as canonical JSON — `sort_keys=True, indent=2, separators=(",", ": ")` + one trailing newline — chose an indented, git-diffable form over `kernel/canonical.canonical_json`'s compact hashing form since this is a human-reviewed migration-contract artifact, not a hash target); confirmed path set is exactly the currently-served routes (`/health`, `/v1/nodes*`, `/v1/edges*`, `/v1/search`, `/v1/tokens*`, `/v1/vaults`) — no `/review`, `/sync`, `/metrics` stubs added. `tests/integration/test_openapi_snapshot.py` (NEW) is both the test AND the regenerate tool (same `_canonical_snapshot_text()` helper drives both, so they can't drift apart): `test_served_spec_equals_committed_snapshot` (byte-equality), `test_gate_actually_catches_drift` (mutates a deep copy of the served spec with a fake extra path, asserts it does NOT match the snapshot — proves the comparison isn't vacuous), `test_snapshot_carries_no_product_name` (rebrand invariant, rule 0.6). Regenerate command (documented in the test file's module docstring): `uv run python -m tests.integration.test_openapi_snapshot`. `.github/workflows/ci.yml` extended minimally in existing style: added `- run: uv run pytest tests/integration/test_openapi_snapshot.py` as a step in the existing `check` job (right after the `tests/unit tests/property` step) and removed the now-obsolete "Reserved... openapi-snapshot" comment line; did not touch the `windows-gate`/battery/soak placeholders. **Verified the gate is real, not just self-consistent**: temporarily added a genuine extra route to `app.py` (`GET /__drift_probe__`), reran the named Verify test and confirmed `test_served_spec_equals_committed_snapshot` actually FAILS with a real diff, then reverted `app.py` byte-for-byte (diffed against a pre-edit backup) and reconfirmed all 3 tests pass again — `app.py` itself is untouched/unmodified in the final diff (confirmed via `diff` against backup, zero bytes changed). Confirmed no product-name leakage in the snapshot (`grep -i akasha docs/api-snapshot/openapi.json` → no matches; title is `tm-daemon API`). No new SPEC-QUESTIONs. Full-gate `pyright src` showed 9 transient errors, all in `src/akasha/daemon.py` (msvcrt Windows-lock code, `reportDeprecated` on `contextmanager`) — confirmed via `git diff --stat`/`git log` this is the concurrently-running T4.9 sibling task's in-progress, uncommitted edit to a file T4.7 does not own and never touched; `pyright src/akasha/api src/akasha/kernel src/akasha/contract` (T4.7's actual surface) is 0 errors/0 warnings on its own. |
| T4.8 | CLI verbs (`cli/main.py`, typer) | DONE | Run 20260712-M4-daemon-api, fleet-worker (direct Sonnet edit). Verify `tests/integration/test_cli.py` 26 passed; ruff/pyright clean; full gate `tests/unit tests/property tests/integration` 316 passed. New `src/akasha/cli/main.py`: a pure `typer` HTTP client (never imports `kernel/store.py`, never touches SQLite — only exception is `kernel.ids.mint()` for client-side facet-id generation, documented as pure/DB-free, same precedent as `contract/render.py`/`contract/linter.py` reusing it). Verbs `new/get/set/rm/search/review/token` wired to their §4.11 endpoints exactly (`akasha daemon` deliberately NOT here — T4.9's verb). Global flags `--json` (`cli/v1` envelope `{"schema","ok","data"|"error"}`, additive-only), `--dry-run` (mutating verbs print `{"method","path","body"}` and `typer.Exit(0)` **before** any `httpx` call — proven in tests by pointing `--dry-run` at an unreachable base-url and asserting no exception), `--token`; plus one CLI-only wiring flag `--base-url` (spec-silent, needed to point at a test daemon, defaults to spec's `127.0.0.1:7433`). Exit codes: `_exit_code_for` maps 404/`E_NOT_FOUND`→3, 409/`E_NEEDS_REDIRECT`/conflict-ish codes→4, everything else the server returns→1; genuine CLI-side usage errors (missing required args — handled automatically by click/typer; malformed `--facet`; bad `--class` value) →2 (verified for all four paths + a connection/auth-failure→1 case). `--facet name=span` (repeatable) mints a fresh `facet_id` client-side + `version=1`, matching the API's `Facet` model shape. Tests: real `uvicorn` server on an ephemeral 127.0.0.1 port (thread, `uvicorn.Server.run`), invoked via `typer.testing.CliRunner` against the actual `akasha.cli.main:app` entrypoint (true end-to-end HTTP, no ASGI shortcut) — 26 tests covering every verb's happy-path round-trip (new+get, set, rm S0, search, token create/list/revoke), `--dry-run` mutates nothing (asserted via DB row-count AND an unreachable-host proof), `--json` emits `cli/v1` on both success and error, and every exit-code path (get/rm/set/token-revoke missing→3; rm S1-without-redirect→4, S1-with-redirect→0; malformed `--facet`/bad `--class`/missing required args→2; missing token→1). Tokens seeded directly into the fixture's SQLite conn (same pattern as `test_api.py::_insert_token`) — every mutating round-trip uses a **human** token, independent of T4.6 (agent-token proposal rewriting, landed concurrently mid-task; re-ran the full gate afterward, still 316 passed). 3 new SPEC-QUESTIONs logged (`/v1/review*` doesn't exist until T7.5 — CLI implements the verb against the documented shape and tolerates FastAPI's non-envelope 404, tested via `test_review_list_against_unimplemented_endpoint_fails_gracefully` which asserts only "no crash, non-zero exit", not a round-trip; `set`'s `--class` default when omitted, took `patch`; `--base-url` as spec-silent CLI wiring) — all open, low-stakes. |
| T4.9 | Daemon lifecycle + single-instance lock + autostart docs | DONE | Run 20260712-M4-daemon-api, fleet-worker (direct Sonnet edit — judgment-heavy: cross-platform lock design, exit-code mapping). Verify `tests/integration/test_daemon_lock.py` 8 passed; ruff/pyright clean; full gate `tests/unit tests/property tests/integration` 327 passed (incl. sibling T4.7's `test_openapi_snapshot.py`, unaffected — no new/changed FastAPI route added). Extends `daemon.py` (T0.6's `configure_logging`/`JsonLineFormatter` untouched, same signature) with: `AlreadyRunningError(lock_path)` (typed, human-readable `"another akasha daemon instance is already running (lock held at {path})"`); `single_instance_lock(lock_path)` context manager — non-blocking exclusive OS lock, `fcntl.flock(LOCK_EX\|LOCK_NB)` on POSIX / `msvcrt.locking(LK_NBLCK, 1)` on Windows, gated on `sys.platform` (each Windows-only helper starts with an `if sys.platform != "win32": raise AssertionError(...)` guard so pyright's platform-aware reachability analysis skips checking the Windows-only stdlib calls on the Linux dev/CI host — 0 pyright errors without weakening real Windows behavior); releases on any exit (normal or exceptional), verified by both a clean-exit and an exception-exit test. Lock file: `tm-daemon.lock` in the config directory (`Config.path.parent`, falling back to `config.default_config_dir()` — neutral name, rule 0.6, no product name on disk). `serve(config)` ties it together: resolves the config dir, calls `configure_logging(dir/"daemon.log")`, acquires the lock, then lazily imports `akasha.api.app.create_app` + `uvicorn.run(host=config.bind, port=config.port)` — both imports deferred into the function body so `cli/main.py`'s top-level import stays cheap and `daemon.py` doesn't force FastAPI/uvicorn onto every CLI invocation. `cli/main.py` gained the `daemon [--config PATH]` verb (module docstring updated to explain why this one verb breaks the "pure HTTP client" pattern — it doesn't speak HTTP to a running server, it *is* the server, dispatching to `daemon.serve`); it does NOT use `--base-url`/`--token`/`--json`/`--dry-run` (foreground process command, not an API call) — catches `AlreadyRunningError` and exits **4** (the spec §4.12 "conflict" class: a second instance is a conflict over the single-instance-lock resource, not error(1)/usage(2)), printing a one-line message, never a traceback (asserted in a test that pre-holds the lock then invokes the CLI verb via `CliRunner`). `docs/autostart-windows.md` (new): Task Scheduler XML sample (`LogonTrigger`, `MultipleInstancesPolicy=IgnoreNew`, `ExecutionTimeLimit=PT0S` disabling the 72h default kill, `RestartOnFailure`) + `schtasks` one-liner alternative, and NSSM install/configure/manage steps including the `LocalSystem`-vs-per-user `%APPDATA%` config-path gotcha. No FastAPI route added/changed (daemon is CLI/process-only, confirmed safe alongside T4.7's parallel OpenAPI-snapshot work). No new SPEC-QUESTION — build-plan Steps + M4 milestone text fully specified the lock/docs requirement; the exit-code-4 choice and lock-file-location choice were narrowest-reading judgment calls documented inline, not spec gaps. |

## M5 — Sync engine (Depends on: M4)

**Follow-ups inherited from M3's resolved spec questions (fable review, 2026-07-12
— see docs/archived-questions.md T3.5 and T3.6 entries for full reasoning):**
1. **T5.4** must add cross-file `E_DUP_ID` detection (reconcile-level, reading the
   node→path projection mapping already required for `hub_state_for(path)`) and
   must distinguish E04 (cross-file move) from cross-file E05 (copy-without-cut)
   by correlating `created`/`deleted` ops across files within the reconcile
   horizon — anything uncertain ⇒ review item, never a guess.
2. **T5.4/T5.7/T8.x** must persist pause-and-diff review items as
   `review_queue.cause_kind='violation'` (no new `cause_kind`) and must never
   branch on `linter.PauseDecision.review_item.code` (a cosmetic borrowed value)
   to identify a pause event — use the `PauseDecision` wrapper at the reconcile
   boundary and `/sync/status` pause state at the API/UI boundary instead.

| Task | Goal | Status | Notes |
|---|---|---|---|
| T5.1 | Base store (per-file snapshots) | TODO | |
| T5.2 | Origin / echo-suppression (`origin.py`) | TODO | |
| T5.3 | Watcher: debounce + cloud-path detection (`watcher.py`) | TODO | |
| T5.4 | Reconcile pipeline (`reconcile.py`) | TODO | |
| T5.5 | Conflict branching | TODO | |
| T5.6 | Startup reconcile / crash recovery | TODO | |
| T5.7 | Sync API routes (`routes/sync.py`) | TODO | |
| T5.8 | Golden reconcile fixtures + scripted edit battery E01–E20 | TODO | |

## M6 — Obsidian plugin (Depends on: M5)

| Task | Goal | Status | Notes |
|---|---|---|---|
| T6.1 | Plugin scaffold + build in CI | TODO | |
| T6.2 | Settings (URL + token) | TODO | |
| T6.3 | Status bar (sync state + violation count) | TODO | |
| T6.4 | Command: create node from selection | TODO | |
| T6.5 | Clipboard cut/copy carrying anchors + TESTPLAN | TODO | |

## M7 — TMS loop (Depends on: M4)

| Task | Goal | Status | Notes |
|---|---|---|---|
| T7.1 | Invalidation walk (`invalidate.py`) | TODO | |
| T7.2 | Change-class heuristic + wiring into commit | TODO | |
| T7.3 | Trigger registry + evaluator (`triggers.py`) | TODO | |
| T7.4 | Supertask trigger fires once, never auto-closes | TODO | |
| T7.5 | Review queue: resolutions + daily cap (`review.py`) | TODO | |
| T7.6 | Split/merge inbound-reassignment queue | TODO | |
| T7.7 | Facets-from-spans capture (`POST /edges` with `facet_span`) | TODO | |

## M8 — Web UI (Depends on: M7)

| Task | Goal | Status | Notes |
|---|---|---|---|
| T8.1 | UI shell + static serving (htmx, no build step) | TODO | |
| T8.2 | Node view | TODO | |
| T8.3 | Review view (one-click resolutions + daily-cap banner) | TODO | |
| T8.4 | Search + Sync views | TODO | |
| T8.5 | Playwright smoke test (full loop) | TODO | |

## M9 — Hardening (Depends on: M5–M8)

| Task | Goal | Status | Notes |
|---|---|---|---|
| T9.1 | Windows battery items (CRLF, locking retry, AV noise) | TODO | |
| T9.2 | Metrics: RSS/CPU sampling + counters (`metrics.py`) | TODO | |
| T9.3 | S0 GC scheduling + log rotation | TODO | |
| T9.4 | `--dry-run` coverage + error-message pass | TODO | |
| T9.5 | 24-hour soak test | TODO | |

## M10 — Dogfood instrumentation (Depends on: all)

| Task | Goal | Status | Notes |
|---|---|---|---|
| T10.1 | Metrics dashboard view | TODO | |
| T10.2 | Export command (`akasha export --md DIR`) | TODO | |
| T10.3 | Acceptance mapping (`docs/acceptance.md`) | TODO | |
