You are running headless and unattended, as one iteration of an overnight
fleet-dispatch loop. No human will see your output until morning, so act
conservatively and leave a clear, file-based trail — do not rely on anyone
reading your prose.

Follow `docs/agents/runbook.md`, section "Path A: fleet-dispatch Workflow",
exactly (this script runs under Claude Code, which has a `Workflow` tool, so
Path A — not the Cursor-oriented Path B — is the one that applies here):

1. Spawn a `fleet-orchestrator` agent to scan `docs/agents/task-status.md` +
   `docs/build-plan.md` and return the next eligible, file-disjoint cohort.
2. Generate a `run_id` (`<YYYYMMDD-HHMMSS>-<milestone-label>`) and run the
   `fleet-dispatch` Workflow (`docs/agents/fleet-workflow.js`) for that
   cohort.
3. Update `docs/agents/task-status.md` using ONLY the Workflow's returned
   verdicts — a task becomes `DONE` only on `CONFIRMED_DONE`. A
   `CONTRADICTS_CLAIM` verdict means `BLOCKED: verifier contradicted worker
   claim`, same as any other Verify failure.
4. Write the durable run log under `docs/agents/logs/<run_id>/` per the
   runbook's "Logging" section, from the Workflow's real structured return
   value — never a re-narrated summary of it.

## After each cohort: commit and push

Once `task-status.md` and the run log are written, stage exactly the files
you changed, commit with a message naming the `run_id` and the task IDs in
the cohort, and push to the current branch.

- Never force-push, never `git push --force`/`-f`.
- Never `git commit --amend` — always a new commit.
- Never `git reset --hard`, `git rebase`, `git filter-branch`, `git branch
  -D`, or `git clean -f` — these rewrite or discard history.
- If the push is rejected (e.g. remote has diverged), do not force past it —
  write the conflict into `docs/agents/logs/OVERNIGHT_HALT.md` (see below)
  and stop.

## When to stop instead of guessing

If there is no eligible next cohort — either every task in
`docs/build-plan.md` is `DONE`, or every remaining `TODO` is blocked on a
dependency, a `BLOCKED:` entry, or an ambiguity that needs a human
decision — do not invent work and do not guess past it. Instead:

1. Write `docs/agents/logs/OVERNIGHT_HALT.md` explaining precisely why
   (e.g. "all tasks DONE as of <run_id>", or "remaining TODOs T4.2, T4.3
   blocked on BLOCKED: <reason> in task-status.md, needs human decision").
2. Do not spawn another cohort this invocation.

All other guardrails from root `CLAUDE.md` and `docs/agents/runbook.md`
still apply in full: never edit golden files/fixtures/acceptance tests to
make something pass; never invent schema, endpoints, ID formats, or grammar
beyond `docs/mvp-spec.md` (narrowest reading + `# SPEC-QUESTION:` + an entry
in `docs/spec-questions.md` on ambiguity); all persistent writes go through
`src/akasha/kernel/store.py`; `pickle`/`eval`/`exec` are banned; a task is
not `DONE` until its `Verify` command passes locally.
