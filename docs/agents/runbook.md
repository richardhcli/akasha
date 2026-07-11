# Overnight autonomous-agent runbook

This describes how to actually kick off an unattended run through
`docs/build-plan.md`, once you're ready to. Nothing here runs automatically —
this is the procedure a human (or a session acting on the human's explicit
request) follows to start one.

## Primary path: fleet-dispatch Workflow (recommended for large batches)

`docs/agents/fleet-architecture.md` defines a 3-tier agent hierarchy (Opus
scanner → Sonnet worker → Cursor editor). Dispatch and verification are
implemented as a `Workflow` script, `docs/agents/fleet-workflow.js`, not as
a prose-narrating orchestrator agent — see "Why this is a Workflow, not an
orchestrator agent" below for why that distinction matters.

**How to use:**
1. Spawn a `fleet-orchestrator` agent via the Agent tool to scan
   `docs/agents/task-status.md` + `docs/build-plan.md` and return the next
   eligible, file-disjoint cohort (see `.claude/agents/fleet-orchestrator.md`
   — it is scanner-only now, it does not dispatch or narrate results).
2. Generate a `run_id` (`<YYYYMMDD-HHMMSS>-<milestone-label>`) and invoke:
   ```
   Workflow({
     scriptPath: "docs/agents/fleet-workflow.js",
     args: { run_id, cohort: <cohort from step 1> },
   })
   ```
3. When the Workflow returns, its `results` array contains, per task, the
   worker's structured result and an independent verifier's structured
   verdict (`CONFIRMED_DONE` / `CONTRADICTS_CLAIM` / `CONFIRMED_BLOCKED`).
   Only flip a task to `DONE` in `docs/agents/task-status.md` when its
   verdict is `CONFIRMED_DONE` — a `CONTRADICTS_CLAIM` verdict means the
   worker claimed success but independent re-verification found otherwise;
   treat it as `BLOCKED: verifier contradicted worker claim` and stop the
   pipeline for that task, same as any other Verify failure.
4. Persist a durable log for the run under `docs/agents/logs/<run_id>/`
   (see "Logging" below) using the real structured data the Workflow
   returned — never a re-narrated summary of it.
5. Repeat from step 1 for the next eligible cohort.

**Fallback:** The manual one-task-at-a-time procedure below remains valid and
required if `Workflow` is unavailable or you prefer single-task control.

### Why this is a Workflow, not an orchestrator agent

An earlier version of this system had an `fleet-orchestrator` agent spawn
`fleet-worker` agents via the `Agent` tool directly and report on their
results in prose. A live incident showed this is unsafe: the orchestrating
turn narrated a fake "worker complete" result — before the real background
task's actual completion had arrived — and that fabrication was
indistinguishable from a genuine report until an independent filesystem
check caught the contradiction. A `Workflow` script's `agent()` calls are
real synchronous awaits in deterministic code: the script cannot proceed
until the harness resolves the actual subagent result, so this failure mode
is closed by construction, not by prompting discipline. See the
`ORCHESTRATION-INCIDENT` entry in `docs/spec-questions.md` for the full
incident writeup.

### Logging

Every `fleet-dispatch` Workflow run should be persisted to
`docs/agents/logs/<run_id>/` for durable, human-reviewable audit trail:

```
docs/agents/logs/<run_id>/
  manifest.json              # {run_id, cohort: [task_ids], final_status}
  workers/<task_id>/
    prompt.md                 # the exact prompt sent to the worker (verbatim)
    result.json                # the worker's schema-validated structured result
  verify/<task_id>/
    prompt.md                  # the exact prompt sent to the verifier (verbatim)
    result.json                 # the verifier's schema-validated verdict
```

Write these files from the Workflow's returned `results` array directly
after `await Workflow(...)` resolves — the workflow script itself has no
filesystem access, so this is the caller's responsibility, and it must come
from the structured return value, not a freeform description of it.

### Hang handling

A worker or verifier agent that stalls should self-report `BLOCKED:
possible hang — exceeded tool-call budget` per the guard built into every
prompt in `fleet-workflow.js` — this is a soft, prompt-level cap, not a
guarantee. There is no documented hard per-`agent()` timeout inside a
Workflow script. If a `Workflow` run itself appears wedged (no progress
after a delay sized to the cohort's expected runtime), force-stop it with
`TaskStop` on the Workflow's task ID, write a `manifest.json` with
`final_status: "ABORTED"` and `hang_detected: true`, and stop — do not
retry automatically. This has been exercised in practice (a stalled
planning subagent was force-stopped this way during this system's design).

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
