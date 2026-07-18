# akasha

A local-first, human-in-the-loop personal Truth Maintenance System (pTMS) —
see `docs/vision.md` for the full product rationale. This file is the
entry point for any agent (autonomous or interactive) *implementing* this
repo's build plan. Just want to run/use the product, or set up a dev
environment without picking up a build-plan task? See `docs/user/` and
`docs/dev/` instead — this file and the spec chain below are implementation
law, not user or setup docs.

## Read these first, in order

1. `docs/vision.md` — PRD: *why* the product exists, what's explicitly
   out of scope, the falsified-approaches list (§5) that normatively
   forbids reintroducing certain designs without overturning their stated
   reason.
2. `docs/mvp-spec.md` — the authoritative *what and how* for the MVP
   (repo layout, schema, algorithms, API). Implementation must match this
   spec exactly; it is not a suggestion.
3. `docs/build-plan.md` — the MVP spec sequenced into small, per-file tasks
   (`T0.1`…`T10.3`) with explicit `Verify` commands and Definitions of Done.
   This is the actual work queue.
4. `docs/agents/task-status.md` — current status of every task in the build
   plan. Check this before starting work to avoid duplicating or
   out-of-order work.

## Non-negotiable rules (from `docs/build-plan.md` §"How to use this plan" and `docs/mvp-spec.md` §0)

1. Work milestones **in dependency order** (see the dependency map in
   `docs/build-plan.md`). Never start a task before its `Depends on` tasks
   are `DONE` in `docs/agents/task-status.md`.
2. **Never invent** schema, endpoints, ID formats, or grammar beyond
   `docs/mvp-spec.md`. If something is ambiguous, implement the narrowest
   reading, add a `# SPEC-QUESTION:` comment at the site, and log an entry
   in `docs/spec-questions.md`.
3. **Never edit golden files, fixtures, or acceptance tests** to make an
   implementation pass (`tests/golden/**`). Golden files change only via a
   task that explicitly says so.
4. Every mutation of persistent state goes through
   `src/akasha/kernel/store.py`; no other module writes SQLite directly.
5. All persisted bytes obey canonicalization (`docs/mvp-spec.md` §4.3).
   **`pickle`, `eval`, `exec` are forbidden everywhere** — enforced by
   `tests/unit/test_no_pickle_ban.py` and ruff (`pyproject.toml`
   `[tool.ruff.lint]`).
6. The product name never appears in on-disk formats, anchors, config
   paths, or schema identifiers (rebrand invariant). The neutral prefix is
   `tm` (e.g. anchors `^tm-...`, config dir `tm-daemon`).
7. Run `uv run ruff check src tests && uv run pyright src && uv run pytest
   tests/unit tests/property` (i.e. `make check`) before considering any
   task done; run `uv run pytest tests/battery` (`make battery`) before
   closing any M5+ task.
8. **One task = one focused change.** Touch only the files a build-plan
   task lists under `Files`. Needing to touch an unlisted file is a signal
   the task is misunderstood — stop and log a `# SPEC-QUESTION:` instead of
   guessing.
9. A task is not `DONE` until its `Verify` command passes locally. If it
   fails, the task stays `IN PROGRESS` — do not weaken the test or move on.

## Repo layout

Directory tree, conventions, and toolchain are defined in
`docs/mvp-spec.md` §2–§3 and mirrored on disk:

```
src/akasha/{kernel,contract,sync,tms,api,cli,ui}/   # see docs/mvp-spec.md §2
tests/{unit,property,integration,battery,golden}/
migrations/            # forward-only numbered .sql
plugin-obsidian/       # TypeScript thin client (M6)
docs/
  vision.md            # PRD
  mvp-spec.md           # implementation spec
  build-plan.md         # per-task work queue
  spec-questions.md     # ambiguity log
  agents/
    task-status.md      # per-task DONE/TODO/BLOCKED tracker
    runbook.md           # how to start an unattended multi-agent run
```

Python 3.12+, managed by `uv`. Lint/format: `ruff`. Types: `pyright
--strict` on `src/`. Tests: `pytest` + `hypothesis`. DB: stdlib `sqlite3`,
WAL mode. HTTP: FastAPI + uvicorn on `127.0.0.1:7433`. Full detail in
`docs/mvp-spec.md` §3.

## Autonomous / overnight delegation

If you're picking up unattended, multi-agent work on this repo (not just
answering a question), read `docs/agents/runbook.md` first — it covers
which tasks are safe to fan out in parallel (file-disjoint) versus which
must run sequentially (same-file), and the guardrails above in delegation
form.
