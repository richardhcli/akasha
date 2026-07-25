You are the final invocation of tonight's overnight fleet-dispatch loop.
`scripts/fleet/overnight_runner.sh` has decided to stop — see the "Run
context" block appended after this prompt for exactly why (an end
condition: a stop file, the duration ceiling, or the configured
`OVERNIGHT_END_TIME`) and for how many invocations already ran tonight.
This is your one chance to leave a clean, human-readable trail before it
does.

**You are not here to dispatch a new cohort.** Do not scan
`docs/build-plan.md` / `docs/agents/task-status.md` for eligible work and
do not spawn a `fleet-orchestrator`, `fleet-worker`, `fleet-worker-claude`,
or `fleet-verifier` agent. The loop may be stopping with eligible work
still on the table — that's expected (it's a planned end condition, not
"no work left"), and it's exactly what your summary needs to say plainly,
not paper over.

1. Run `git status --porcelain` and `git log --oneline -20`. If you find
   uncommitted changes, do not commit them yourself unless you can
   positively identify them as this run's own leftovers (e.g. a file
   written earlier tonight and never staged) — otherwise just note them
   for a human to look at. Never guess at intent behind someone else's
   in-progress change.
2. Refresh `docs/agents/overnight-goals.md` using the same procedure
   `scripts/fleet/overnight_prompt.md` step 9 defines for after any
   cohort: reconcile it against the current `docs/agents/task-status.md`
   (strike satisfied entries, note if the list is now empty) — do not
   invent a new goal, only reconcile what's already there.
3. Write `docs/agents/logs/OVERNIGHT_SUMMARY.<UTC timestamp,
   YYYYMMDDTHHMMSSZ, matching `date -u +%Y%m%dT%H%M%SZ`>.md` covering:
   - Why the run is stopping (copy the end condition from the "Run
     context" block below verbatim).
   - Whether eligible work likely remains (say so explicitly either way —
     this is the one thing a mechanical exit-summary file can't tell a
     human).
   - Every task that went `DONE` tonight: task IDs and commit hashes from
     `git log --since=<run start, given below>`.
   - Current `docs/agents/task-status.md` state: counts of DONE / TODO /
     IN PROGRESS / BLOCKED, and any `BLOCKED:` rows verbatim.
   - Anything appended to `docs/spec-questions.md` tonight.
   - A concrete suggested next step for whoever reads this in the
     morning: either the next eligible task by ID, or a pointer to
     `docs/agents/overnight-goals.md`'s "When the list is empty" section
     if nothing remains.
4. Before ending, confirm no background task you or any subagent started
   tonight is still running — stop it explicitly rather than leaving it
   for the harness's own timeout to force-kill (see
   `docs/agents/runbook.md`).
5. Stage and commit exactly the files you changed (this summary file, and
   `docs/agents/overnight-goals.md` if you touched it), with a commit
   message naming the end condition, then push. Same git safety rules as
   any other invocation: never force-push, never amend, never `reset
   --hard`/`rebase`/`filter-branch`/`clean -f`. If the push is rejected,
   do not force past it — say so in the summary instead.

**Do not write `docs/agents/logs/OVERNIGHT_HALT.md`.** That file
specifically means "no eligible work was found," which is a different,
narrower claim than "the run is stopping for a planned reason" — don't
conflate them.
