# Overnight autonomous-agent runbook

This describes how to actually kick off an unattended run through
`docs/build-plan.md`, once you're ready to. Nothing here runs automatically —
this is the procedure a human (or a session acting on the human's explicit
request) follows to start one.

## Preconditions

- `docs/agents/task-status.md` is up to date — the next `TODO` task's
  dependencies are all `DONE`.
- `make check` is green on the current tree (`uv run ruff check src tests &&
  uv run pyright src && uv run pytest tests/unit tests/property`).
- No open `BLOCKED:` entries in `docs/agents/task-status.md` on the critical
  path (`M0 → M1/M2 → M3 → M4 → M5 → M7 → M10`, per the dependency map in
  `docs/build-plan.md`).

## Starting a run

Use the `Workflow` tool (or the `schedule` skill for a cron-triggered start)
with a script that:

1. Reads `docs/agents/task-status.md` to find the next `TODO` task per
   milestone in dependency order (`M0 → {M1, M2} → M3 → M4 → {M5, M7} → {M6,
   M8} → M9 → M10`; `M6` and `M8` are parallelizable once their deps close,
   per `docs/build-plan.md`'s dependency map).
2. Spawns one agent per task, each briefed with: the task's full entry from
   `docs/build-plan.md` (Goal/Depends on/Files/Spec/Steps/Verify/DoD), the
   root `CLAUDE.md` rules, and an instruction to run the task's `Verify`
   command before reporting done.
3. On success, flips that task's row in `docs/agents/task-status.md` to
   `DONE` and moves to the next eligible task. On failure, sets
   `BLOCKED: <reason>` and stops that branch rather than guessing past it.
4. Never starts a task whose dependencies aren't `DONE` — `pipeline()` is
   safe within a milestone's independent tasks; tasks with `Depends on`
   pointing at same-milestone siblings need a barrier or sequential
   pipeline stage.

Because tasks within a milestone often share files (e.g. `T1.3`–`T1.7` all
touch `src/akasha/kernel/store.py`), run same-file tasks **sequentially**,
not in parallel — parallel agents editing the same file will conflict.
`docs/build-plan.md`'s per-task `Files` list tells you which tasks are
file-disjoint and safe to fan out.

## Guardrails carried over from `docs/build-plan.md` and root `CLAUDE.md`

- Never invent schema, endpoints, ID formats, or grammar beyond
  `docs/mvp-spec.md`. Ambiguity → narrowest reading + `# SPEC-QUESTION:` +
  an entry in `docs/spec-questions.md`.
- Never edit golden files, fixtures, or acceptance tests to make something
  pass.
- All persistent writes go through `src/akasha/kernel/store.py`.
- `pickle`/`eval`/`exec` are banned everywhere (enforced by
  `tests/unit/test_no_pickle_ban.py` and the ruff config in
  `pyproject.toml`).
- A task is not `DONE` until its `Verify` command passes locally.

## Morning review

Check `docs/agents/task-status.md` for `BLOCKED:` rows and
`docs/spec-questions.md` for anything logged overnight — both need a human
decision before the next run continues past them.
