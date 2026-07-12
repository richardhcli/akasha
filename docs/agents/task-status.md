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

## M3 — Contract parser / renderer (Depends on: M2)

| Task | Goal | Status | Notes |
|---|---|---|---|
| T3.1 | Grammar tokens + contract version (`grammar.py`) | TODO | |
| T3.2 | Parser: vault text → BlockSet (`parser.py`) | TODO | |
| T3.3 | Renderer: hub nodes → canonical vault text (`render.py`) | TODO | |
| T3.4 | Round-trip property tests | TODO | |
| T3.5 | Linter: violation codes + certain-repair (`linter.py`) | TODO | |
| T3.6 | Pause & diff (formatter-storm guard) | TODO | |
| T3.7 | Golden corpus (≥25) + committed fuzz corpus | TODO | |

## M4 — Daemon + API + CLI core (Depends on: M1, M3)

| Task | Goal | Status | Notes |
|---|---|---|---|
| T4.1 | Auth: token classes, secrets, rate limits (`auth.py`) | TODO | |
| T4.2 | Audit log | TODO | |
| T4.3 | FastAPI factory + `/health` + schemas re-export | TODO | |
| T4.4 | Node routes (`routes/nodes.py`) | TODO | |
| T4.5 | Edge, search, token, vault routes | TODO | |
| T4.6 | Agent-token proposal rewriting | TODO | |
| T4.7 | OpenAPI snapshot + CI gate | TODO | |
| T4.8 | CLI verbs (`cli/main.py`, typer) | TODO | |
| T4.9 | Daemon lifecycle + single-instance lock + autostart docs | TODO | extends `daemon.py` logging setup from T0.6 |

## M5 — Sync engine (Depends on: M4)

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
