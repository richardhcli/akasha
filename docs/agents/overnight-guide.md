# Overnight autonomous runs: user guide

A short, practical walkthrough for kicking off an unattended overnight
build-plan run and checking on it later. For the underlying mechanism and
guardrails, see `docs/agents/runbook.md`; for architecture, see
`docs/agents/fleet-architecture.md` and `scripts/fleet/README.md`. This
page only covers the day-to-day "how do I actually run this" part.

## What runs, and what it does tonight

`scripts/fleet/overnight_runner.sh` loops, calling headless `claude -p`
(Sonnet by default) against `scripts/fleet/overnight_prompt.md` roughly
every ~5 hours (the account's rolling usage window — see "How long it
runs" below), until you stop it or it runs out of eligible work. Each
invocation:

1. Reads `docs/agents/overnight-goals.md` for tonight's priority order
   among already-eligible tasks (see that file for the current goal set —
   right now: T11.3, then T11.4).
2. Scans `docs/agents/task-status.md` + `docs/build-plan.md` for the next
   eligible, file-disjoint cohort of tasks.
3. Dispatches each task to a worker, then an independent verifier, and
   only marks it `DONE` on a real `CONFIRMED_DONE` verdict.
4. Commits and pushes after each cohort.
5. If nothing is eligible, writes `docs/agents/logs/OVERNIGHT_HALT.md`
   explaining why and stops — it never invents new work on its own. See
   `docs/agents/overnight-goals.md`'s "When the list is empty" section for
   the procedure a human uses to decide what goes in the *next* goal set.

The driving loop runs as Sonnet to keep it cheap, but consults the
`advisor` tool (Opus) at the judgment-call points that actually matter —
cohort sanity-checks, stuck/contradictory results, and before concluding
no work remains — so you get frontier-model judgment where it counts
without paying for it on every mechanical step.

**Human-in-the-loop boundary:** the loop will never touch `docs/build-plan.md`'s
`T11.2` row (marked `BLOCKED: human-only`) or anything else requiring a
judgment call about which real content becomes a tracked claim — that's a
hard rule (`docs/vision.md`'s human-in-the-loop invariant), not just a
prompting convention. See `docs/agents/overnight-goals.md` for the current
guardrails in full.

## Start it

```bash
scripts/fleet/start_overnight.sh
```

This launches the loop detached inside a tmux session named
`akasha-overnight` — it survives your terminal or SSH session closing.
It's idempotent: running it again while a copy is already up just tells
you so, rather than starting a second (double-committing) instance.

Override the driving model or worker mode if you want:

```bash
OVERNIGHT_MODEL=opus scripts/fleet/start_overnight.sh          # all-Opus driving loop instead of Sonnet+advisor
OVERNIGHT_WORKER_MODE=hybrid scripts/fleet/start_overnight.sh  # allow Tier-2 workers to delegate to Cursor
```

## Watch it

```bash
tmux attach -t akasha-overnight   # live view; detach again with Ctrl-b d (do NOT close the window)
tail -f docs/agents/logs/overnight-runner.log   # same output, durable on disk
```

Per-cohort detail (what each task's worker/verifier actually did) lands
under `docs/agents/logs/<run_id>/` — see `docs/agents/runbook.md`'s
"Logging" section for the exact layout.

## Stop it

```bash
scripts/fleet/stop_overnight.sh
```

This is a **soft** stop: it touches `scripts/fleet/.stop`, which the loop
checks roughly every 30 seconds, including mid-backoff-sleep — so it exits
promptly between invocations, but always finishes whatever cohort is
currently in flight (including its commit/push) first, rather than being
cut off mid-write. If you need to kill a genuinely hung invocation instead,
`tmux kill-session -t akasha-overnight` or `kill $(cat
scripts/fleet/.overnight_runner.pid)` — only do this if you specifically
want to abandon an in-progress task, not as the normal stop path.

## How long it runs, and how it handles rate limits

There's no documented way to check the account's usage window before
calling, so the loop treats a failed `claude -p` invocation as the signal.
If the failure text includes an exact reset time (the common case), it
sleeps until then; otherwise, two failures in a row are treated as the
usage window being exhausted and it sleeps ~5h before retrying. Between
successful invocations it only pauses ~15s — it chains cohorts as fast as
it can, rather than literally waiting a fixed 5h between every run. Tune
via `OVERNIGHT_RESET_SECS`, `OVERNIGHT_SHORT_BACKOFF_SECS`,
`OVERNIGHT_BETWEEN_RUNS_SECS`, `OVERNIGHT_FAIL_THRESHOLD` if you need
different pacing.

## Morning-after checklist

1. `docs/agents/logs/OVERNIGHT_HALT.md` — if present, read it; it explains
   exactly why the loop stopped (all eligible work done, or something
   blocked).
2. `docs/agents/task-status.md` — scan for any new `BLOCKED:` rows.
3. `docs/spec-questions.md` — anything logged overnight needs a human
   decision before the next run can safely continue past it.
4. `git log` on the current branch — each cohort is its own commit, named
   with its `run_id` and task IDs.
5. If you have a new set of priorities for the next run, refresh
   `docs/agents/overnight-goals.md` — the loop re-reads it every
   invocation, no code change needed.

## Prerequisites

Same preconditions as any run per `docs/agents/runbook.md`: `task-status.md`
up to date, `make check` green on the current tree, no unresolved
`BLOCKED:` entries on the critical path. `tmux` must be installed
(`apt-get install -y tmux`); if you're on a disposable root VM, also
consider `apt-get install -y bubblewrap socat` so Claude Code's own
command sandboxing doesn't silently disable itself (see
`scripts/fleet/overnight_runner.sh`'s header comment for why).
