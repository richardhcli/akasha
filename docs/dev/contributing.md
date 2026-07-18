# Contributing

This repo's build is task-queue-driven, not free-form. The rules and the queue are canonical in one place each — this page only tells you which door to walk through.

1. Read [`../../CLAUDE.md`](../../CLAUDE.md) — the non-negotiable rules (dependency order, no invented schema/endpoints, golden-file freeze, `store.py` as sole DB writer, no pickle/eval/exec, rebrand invariant, `make check`/`make battery` gates, one-task-one-change).
2. Check [`../agents/task-status.md`](../agents/task-status.md) for the next `TODO` task whose dependencies are `DONE`.
3. Read that task's entry in [`../build-plan.md`](../build-plan.md) — it lists the exact `Files` to touch and the `Verify` command that decides done-ness.
4. If the spec is ambiguous at your task, take the narrowest reading, add a `# SPEC-QUESTION:` comment at the site, and log it in [`../spec-questions.md`](../spec-questions.md) — never guess and never invent.
5. Run [`testing.md`](testing.md)'s gate for your milestone, then flip the task's status in `task-status.md` in the same change that closes it.

Running many tasks unattended (multi-agent fleet dispatch) is a separate procedure — see [`../agents/runbook.md`](../agents/runbook.md).
