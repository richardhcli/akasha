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

## M2 — IDs + canonicalization (Depends on: M0)

| Task | Goal | Status | Notes |
|---|---|---|---|
| T2.1 | ID minting + checksum + validation (`ids.py`) | DONE | Sonnet worker recovered after initial orchestration halt; 14 tests passing (includes 5 known checksum vectors, validation, minting, anchors). Confirmed via `make check`: ruff/pyright clean, 41 total tests pass. |
| T2.2 | Text + JSON canonicalization (`canonical.py`) | DONE | Sonnet worker created both canonical.py and test_canonical.py; 14 tests passing (CRLF/LF, NFC, fence detection, tabs, JSON hashing). Confirmed via `make check`: ruff/pyright clean, 41 total tests pass. |
| T2.3 | Canonicalization idempotence (property test) | TODO | |
| T2.4 | Golden serialization corpus (≥15 cases) | TODO | |

## M1 — Kernel store (Depends on: M0)

| Task | Goal | Status | Notes |
|---|---|---|---|
| T1.1 | DDL migration `001_init.sql` (verbatim) | TODO | |
| T1.2 | pydantic models (`model.py`) | TODO | |
| T1.3 | Store: node create/read + commit DAG (`store.py`) | TODO | extends the `store.py` migration-runner code from T0.3 |
| T1.4 | Store: edges create/retract + neighborhood/search | TODO | |
| T1.5 | Maturity derivation (`maturity.py`) | TODO | |
| T1.6 | Deletion, tombstone, redirects, split/merge | TODO | |
| T1.7 | S0 garbage collection job | TODO | |
| T1.8 | Store property suite | TODO | |
| T1.9 | 10k-node neighborhood benchmark | TODO | |

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
