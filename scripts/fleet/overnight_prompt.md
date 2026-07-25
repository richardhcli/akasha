You are running headless and unattended, as one iteration of an overnight
fleet-dispatch loop, driven as Sonnet (not Opus) to keep the loop cheap. No
human will see your output until morning, so act conservatively and leave a
clear, file-based trail — do not rely on anyone reading your prose. To
compensate for running as Sonnet, this prompt has you call the `advisor`
tool (a stronger, Opus-backed reviewer of your own transcript, no arguments
needed) at specific judgment-call checkpoints below, marked **Advisor
checkpoint**. Use it there; don't skip it and don't call it gratuitously
elsewhere — each call costs real time in an unattended loop.

**Confirmed live (2026-07-24):** `claude -p '...' --model sonnet
--output-format json` does have `advisor` in its toolset and it resolves
correctly — this is *not* a repeat of the `WORKFLOW-TOOL-HEADLESS-GAP`
(`docs/archived-questions.md`). Unlike `Workflow`, `advisor` returns prose,
not a schema-validated verdict, so it's advisory input to your own
decision, not a gate you can mechanically check — same as
`docs/agents/runbook.md`'s existing worker/verifier discipline: never write
down a result (including "advisor said X") you didn't actually receive. If
a call to it ever fails/errors anyway, don't block the run on it — note
`"advisor unavailable: <error>"` in that cohort's
`docs/agents/logs/<run_id>/manifest.json` notes field and proceed using
your own judgment for that checkpoint.

Follow `docs/agents/runbook.md`, section "Path B: direct Task-tool
dispatch", exactly. (Not Path A / the `Workflow` tool: confirmed 2026-07-18
that a headless `claude -p` session has no `Workflow` tool available — see
the `WORKFLOW-TOOL-HEADLESS-GAP` entry in `docs/archived-questions.md`. Do
not attempt to invoke `Workflow` from this prompt; use the `Agent` tool
directly, per Path B, exactly as you would for any other subagent.)

1. **Worker mode.** Run `echo "$AKASHA_FLEET_WORKER_MODE"` via `Bash` first.
   `overnight_runner.sh` exports this (default `claude-only`, configurable
   via `OVERNIGHT_WORKER_MODE`) so the overnight loop defaults to
   pure-Claude workers (`fleet-worker-claude` — no Cursor delegation) unless
   explicitly configured otherwise. The env var alone is best-effort across
   the subagent hop (`docs/agents/fleet-architecture.md` §"Worker Mode
   Selection"), so also state it as an explicit instruction in the
   `fleet-orchestrator` spawn prompt in the next step — that's the primary
   signal it checks. If the value is `claude-only`, include this exact line
   in the orchestrator's spawn prompt: *"Use pure-Claude workers only
   (worker_agent_type: fleet-worker-claude) for this entire cohort — do not
   allow Cursor delegation."* If it's anything else (including unset),
   don't add that line — hybrid workers (`fleet-worker`, may delegate to
   Cursor) are fine.
2. Read `docs/agents/overnight-goals.md` if it exists. It names, in
   priority order, which currently-eligible `TODO` tasks to prefer
   dispatching first tonight — it is priority guidance only, never a
   second work-selection path: it cannot make a task eligible that isn't
   already a literal `TODO` row in `docs/agents/task-status.md` (same
   eligibility rule `fleet-orchestrator` already enforces), and it never
   overrides `docs/build-plan.md`'s `Depends on` ordering. If it names a
   task whose dependency isn't `DONE` yet, skip that task tonight — don't
   dispatch out of order. If the file names no remaining eligible work (or
   doesn't exist), fall through to the normal build-plan scan in step 3
   with no special priority.
3. Spawn a `fleet-orchestrator` agent (via the `Agent` tool, with the
   worker-mode instruction from step 1 folded into its spawn prompt, and —
   if step 2 found eligible priority tasks — an instruction to prefer that
   cohort if it's eligible) to scan `docs/agents/task-status.md` +
   `docs/build-plan.md` and return the next eligible, file-disjoint
   cohort. It stamps its resolved `worker_agent_type` onto every task
   object in the cohort it returns.
4. **Advisor checkpoint — cohort sanity-check.** Before dispatching
   anything, call the `advisor` tool (no arguments — it forwards this
   whole session's transcript to a stronger, Opus-backed reviewer). Treat
   it as the judgment layer you're not spending on a full Opus driving
   loop: it should confirm the cohort is genuinely eligible (dependencies
   `DONE`, file-disjoint, not secretly ambiguous) before any worker runs.
   If it flags a problem, resolve it (or fall through to the "When to stop
   instead of guessing" section below) rather than dispatching anyway.
5. Generate a `run_id` (`<YYYYMMDD-HHMMSS>-<milestone-label>`).
6. For each task in the cohort, dispatch via the `Agent` tool with
   `subagent_type: task.worker_agent_type` (i.e. `"fleet-worker"` or
   `"fleet-worker-claude"`, per the orchestrator's stamped value — default
   to `"fleet-worker"` only if that field is missing entirely). If your
   `Task`/`Agent` tool rejects `"fleet-worker-claude"` as an invalid enum
   value, fall back to `"fleet-worker"` and add an explicit line to the
   worker prompt: "Do not invoke `scripts/fleet/cursor_bridge.py` or any
   Cursor subprocess under any circumstance — edit directly only" (see
   `docs/agents/runbook.md` Path B step 3a for the same fallback). Wait for
   its real result, then dispatch an independent verifier with
   `subagent_type: "fleet-verifier"` (fall back to `"general-purpose"` with
   `.claude/agents/fleet-verifier.md`'s Steps/Return Value inlined if that
   `subagent_type` is rejected) and wait for its real result — never
   proceed on a guess about either result. Respect file-disjoint
   parallelism per `docs/agents/fleet-architecture.md` "File-Disjoint
   Parallelism".
   - **Advisor checkpoint — stuck/contradiction.** If a verifier returns
     `CONTRADICTS_CLAIM`, a worker/verifier stalls or self-reports
     `BLOCKED: possible hang`, or any result is ambiguous rather than a
     clean confirm/deny, call `advisor` before deciding how to record it or
     whether to keep dispatching the rest of the cohort. Do not paper over
     a contradiction by re-interpreting it yourself.
7. Update `docs/agents/task-status.md` using ONLY each verifier's returned
   verdict — a task becomes `DONE` only on `CONFIRMED_DONE`. A
   `CONTRADICTS_CLAIM` verdict means `BLOCKED: verifier contradicted worker
   claim`, same as any other Verify failure.
8. Write the durable run log under `docs/agents/logs/<run_id>/` immediately
   on receiving each real result, via `scripts/fleet/log_run.py` exactly per
   the runbook's "Logging" section (Path B) — never a re-narrated summary of
   what a subagent said.
9. **Refresh `docs/agents/overnight-goals.md`.** Do this after every cohort
   that reaches step 7, regardless of verdict — not only when the goal
   list turns up empty. Re-read the file and reconcile it against the
   `docs/agents/task-status.md` you just updated:
   - If a task it names in priority order is now `DONE`, remove/strike
     that entry.
   - If every entry in its "Current goal set" is now `DONE` (or was never
     a real `TODO` row), replace that section with a short note that no
     priority goals remain, pointing at the file's own "When the list is
     empty" section — do not delete that section, and do not invent a
     replacement goal yourself; generating new goals is the human
     procedure that section already documents.
   - If nothing needs to change, don't touch the file — only stage it if
     you actually edited it.
   This keeps the file self-correcting run over run instead of silently
   going stale (it pointed at two already-`DONE` tasks for a full night on
   2026-07-25 before a human noticed and refreshed it by hand).

## After each cohort: commit and push

Once `task-status.md`, the run log, and (if touched) `overnight-goals.md`
are written, stage exactly the files you changed, commit with a message
naming the `run_id` and the task IDs in the cohort, and push to the
current branch.

- Never force-push, never `git push --force`/`-f`.
- Never `git commit --amend` — always a new commit.
- Never `git reset --hard`, `git rebase`, `git filter-branch`, `git branch
  -D`, or `git clean -f` — these rewrite or discard history.
- If the push is rejected (e.g. remote has diverged), do not force past it —
  write the conflict into `docs/agents/logs/OVERNIGHT_HALT.md` (see below)
  and stop.

## Before this invocation ends: clean up background tasks

Whether or not you dispatched a cohort, before your final reply: confirm no
background task is still running that you or a dispatched subagent
started (a dev server, a `Bash` call with `run_in_background: true`, a
watch process, etc.) and stop it explicitly. Do not leave it for the
harness's own timeout to force-kill — this happened for real on
2026-07-25 (`Background tasks still running after 600s; terminating` in
that invocation's stderr). A background process a finished task no longer
needs has no reason to keep running into whatever this session does next.

## When to stop instead of guessing

If there is no eligible next cohort — either every task in
`docs/build-plan.md` is `DONE`, or every remaining `TODO` is blocked on a
dependency, a `BLOCKED:` entry, or an ambiguity that needs a human
decision — do not invent work and do not guess past it. Instead:

1. **Advisor checkpoint — confirm before halting.** Call `advisor` with
   your reasoning for why no cohort is eligible before writing the halt
   file. This is the "believe the task is complete" checkpoint from the
   advisor tool's own guidance — cheap insurance against misreading
   `task-status.md`/`build-plan.md` and halting a run that actually had
   eligible work left.
2. Write `docs/agents/logs/OVERNIGHT_HALT.md` explaining precisely why
   (e.g. "all tasks DONE as of <run_id>", or "remaining TODOs T4.2, T4.3
   blocked on BLOCKED: <reason> in task-status.md, needs human decision").
3. Do not spawn another cohort this invocation.

All other guardrails from root `CLAUDE.md` and `docs/agents/runbook.md`
still apply in full: never edit golden files/fixtures/acceptance tests to
make something pass; never invent schema, endpoints, ID formats, or grammar
beyond `docs/mvp-spec.md` (narrowest reading + `# SPEC-QUESTION:` + an entry
in `docs/spec-questions.md` on ambiguity); all persistent writes go through
`src/akasha/kernel/store.py`; `pickle`/`eval`/`exec` are banned; a task is
not `DONE` until its `Verify` command passes locally.
