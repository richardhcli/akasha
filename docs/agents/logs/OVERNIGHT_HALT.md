# Overnight run halted — no eligible cohort

**Invocation:** 2026-07-24, headless Sonnet overnight-dispatch loop (worker mode `claude-only`).

## Why halted

`fleet-orchestrator` scanned `docs/agents/task-status.md` + `docs/build-plan.md` and found no eligible cohort. Independently re-verified by this session (not taken on the subagent's word alone):

- `grep -nE 'TODO|IN PROGRESS|BLOCKED' docs/agents/task-status.md` — the only non-DONE task row is **T11.2**, status `BLOCKED: human-only`. No `TODO` or `IN PROGRESS` rows exist anywhere in the file.
- Task-ID parity check: extracted all `T<n>.<n>` IDs from both `docs/build-plan.md` and `docs/agents/task-status.md`, sorted+deduped, diffed with `comm`. Both files contain exactly the same 81 task IDs — no task exists in one file but not the other, so the scanner is not structurally blind to any task.
- `advisor` checkpoint (Opus-backed review of this session's transcript) confirmed the halt reasoning before this file was written, and additionally recommended the independent greps above (done) rather than trusting the orchestrator's prose alone.

78 of 81 tasks are `DONE`. The sole remaining task, **T11.2**, is deliberately fenced off from autonomous dispatch — `docs/agents/task-status.md` (line ~452) and `docs/build-plan.md` M11 header both state it requires a human judgment call (which real personal-note spans become tracked claims/entities — vision.md human-in-the-loop invariant, PRD R9) and must not be flipped to `TODO` by an agent. This session did **not** flip it, and did not edit `task-status.md` or `build-plan.md`.

## Three things the next human/agent should know

1. **Product gap surfaced by T11.1 (DONE, but with a caveat):** `sync/watcher.py`'s `Watcher` has zero production call sites in `daemon.py`/`api/app.py`. Neither startup `reconcile_all` nor `POST /v1/sync/rescan` scans a newly-registered root's filesystem on first pass — empirically reproduced during T11.1: `files_reconciled: 0` against 5 real on-disk files. This is a **hard prerequisite** for T11.2 step 2 ("confirm ingestion... let the watcher/rescan pick it up") but has no build-plan task of its own and no `TODO` row — it is not dispatchable by the scanner. A human needs to either open a new build-plan task for it or explicitly accept a workaround. See `docs/spec-questions.md` and `docs/dogfood/README.md`'s "Known limitation" section for prior write-ups.
2. **Doc drift in `docs/agents/task-status.md`:** the M11 "Eligibility note for the overnight/fleet scanner" prose (around lines 447–457) still says T11.1 "is left `TODO`, eligible for normal autonomous dispatch." The actual T11.1 table row is `DONE` (run `20260725-030653-M11`). The table row is authoritative; the prose is stale. Not fixed by this session (out of scope for a halt-only invocation — one task = one focused change, and this isn't a task).
3. **M10 gate ambiguity:** M11 build-plan header states `Depends on: M10`. M10's own status is `CODE-COMPLETE`, not explicitly `CLOSED`. If a human later flips T11.2 to `TODO`, whether `CODE-COMPLETE` satisfies M11's dependency gate needs a human ruling — not resolved here.

## Note on repeat halts

If `scripts/fleet/overnight_runner.sh` does not check for the presence of this file before dispatching another invocation, every subsequent invocation tonight will likely repeat this same full scan and halt again. Flagging for a human to check — this session was not asked to and did not modify the runner script's behavior.

## Files touched by this invocation

None in `src/`, `docs/build-plan.md`, or `docs/agents/task-status.md`. Only this halt file was written. No run log via `scripts/fleet/log_run.py` — there were no task dispatch results to log (cohort was empty).
