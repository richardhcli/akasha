# Developing akasha

This tree is for humans building/running akasha from source or packaging
it for distribution. It is deliberately separate from two other doc trees
that look similar but serve different readers:

- [`../user/`](../user/) is for people *using* akasha — installing it,
  running the CLI/web UI, no source checkout assumed.
- [`../agents/`](../agents/) plus [`../../CLAUDE.md`](../../CLAUDE.md) and
  [`../build-plan.md`](../build-plan.md) are the implementation-law/task-queue
  trail for whoever (human or agent) is picking up build-plan tasks —
  dependency order, per-task `Files`/`Verify`, and the task-status ledger.
  Start there instead of here if that's what you're doing.

1. [`setup.md`](setup.md) — get a working environment and run the checks.
2. [`testing.md`](testing.md) — what each test tier means and when to run it.
3. [`contributing.md`](contributing.md) — how work is actually queued and reviewed in this repo.
4. [`windows-packaging.md`](windows-packaging.md) — building `akasha.exe`, the tray icon, and the Windows installer (build-plan T12.5).

**Not covered here:** architecture, schema, and repo layout are specified once in [`../mvp-spec.md`](../mvp-spec.md) §1–§4 (system diagram, directory tree, DDL, API/CLI signatures) — this tree links into it rather than repeating it. Product rationale is [`../vision.md`](../vision.md).
