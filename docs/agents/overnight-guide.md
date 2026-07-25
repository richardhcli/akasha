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
   among already-eligible tasks — see that file for the current goal set
   (it's self-refreshing now, so this guide doesn't hardcode it).
2. Scans `docs/agents/task-status.md` + `docs/build-plan.md` for the next
   eligible, file-disjoint cohort of tasks.
3. Dispatches each task to a worker, then an independent verifier, and
   only marks it `DONE` on a real `CONFIRMED_DONE` verdict.
4. Commits and pushes after each cohort.
5. If nothing is eligible, the invocation writes `docs/agents/logs/
   OVERNIGHT_HALT.md` explaining why — it never invents new work on its
   own. A single halt does **not** stop the loop: it archives the halt
   file with a timestamp and rescans after a cooldown
   (`OVERNIGHT_HALT_RECHECK_SECS`, default 30m), in case a human adds
   goals to `docs/agents/overnight-goals.md` or `docs/build-plan.md` in
   the meantime — which is exactly what happened on the night of
   2026-07-24/25 (a halt, then new tasks added by hand, then a restart).
   Only after `OVERNIGHT_HALT_RETRY_LIMIT` (default 3) consecutive halts
   *with `docs/agents/task-status.md` byte-for-byte unchanged* does the
   loop give up for the night. See `docs/agents/overnight-goals.md`'s
   "When the list is empty" section for the procedure a human uses to
   decide what goes in the *next* goal set.
6. After every cohort (step 3), the invocation also reconciles
   `docs/agents/overnight-goals.md` against the just-updated
   `docs/agents/task-status.md` — striking satisfied entries, or noting
   an empty list — so the goals file can't silently go stale the way it
   did overnight on 2026-07-25 (it kept pointing at two already-`DONE`
   tasks until a human noticed).
7. Before ending, the invocation confirms no background task it or a
   subagent started is still running, and stops it explicitly rather than
   leaving it for the harness's own 600s force-kill timeout.
8. Regardless of halts, the loop stops on its own once any **end
   condition** is reached — these are OR'd, first one wins:
   - a stop file (`scripts/fleet/stop_overnight.sh`)
   - a hard wall-clock *duration* ceiling from start
     (`OVERNIGHT_MAX_RUNTIME_SECS`, default 12h)
   - an optional wall-clock *time-of-day* ceiling (`OVERNIGHT_END_TIME`,
     e.g. `09:00` — unset by default)

   On any of these, the loop never interrupts a `claude -p` call already
   running — it finishes and commits normally, then the runner dispatches
   one final **wrap-up invocation** (`scripts/fleet/
   overnight_wrapup_prompt.md`) instead of another cohort. That
   invocation does not dispatch work; it writes a narrative
   `docs/agents/logs/OVERNIGHT_SUMMARY.<timestamp>.md` (what got done,
   whether eligible work likely remains, current task-status.md state,
   suggested next step), refreshes `overnight-goals.md` one more time,
   confirms no background task is left running, and commits/pushes that
   summary. A halt-retry-limit exit (see step 5 above) does *not* get a
   wrap-up invocation — that path already has its own agent-authored
   explanation in `OVERNIGHT_HALT.*.md`.

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

Override the driving model, worker mode, or runtime ceiling if you want:

```bash
OVERNIGHT_MODEL=opus scripts/fleet/start_overnight.sh              # all-Opus driving loop instead of Sonnet+advisor
OVERNIGHT_WORKER_MODE=hybrid scripts/fleet/start_overnight.sh      # allow Tier-2 workers to delegate to Cursor
OVERNIGHT_MAX_RUNTIME_SECS=21600 scripts/fleet/start_overnight.sh  # 6h ceiling instead of the 12h default
```

The full list of tuning env vars (halt-retry cooldown/limit, rate-limit
backoff timing, minimum runtime slot before starting a new invocation) is
documented in `scripts/fleet/overnight_runner.sh`'s header comment — that
file is the source of truth for exact defaults.

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

The loop stops dispatching new cohorts within `OVERNIGHT_MAX_RUNTIME_SECS`
(default 12h) of starting, whether or not a stop file was ever written —
this is a hard ceiling, not just a target, and it's enforced even in the
middle of a multi-hour rate-limit backoff sleep (checked every ~30s, same
as the stop file). If you'd rather stop by a specific time of day
regardless of when you started it — e.g. "always be done by 9am" — set
`OVERNIGHT_END_TIME` (24h `HH:MM`, optionally with `OVERNIGHT_END_TIME_TZ`
for a specific IANA zone; defaults to system local time). Whichever of the
duration ceiling and the end time comes first wins; if `OVERNIGHT_END_TIME`
has already passed today when the loop starts, it means tomorrow. The one
thing allowed to run past the ceiling is the wrap-up invocation itself
(see "What runs" above) — it's capped separately at
`OVERNIGHT_WRAPUP_TIMEOUT_SECS` (default 20m), so the real worst case is
"ceiling plus up to 20 more minutes," not indefinite. It also refuses to
start a new invocation with less than `OVERNIGHT_MIN_SLOT_SECS` (default
30m) of runtime budget left, so it doesn't start work it would have to cut
off — it exits cleanly a little early instead, after the wrap-up
invocation described above.

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

1. `docs/agents/logs/overnight-runner-last-exit.md` — written on every
   exit path (stop file, halt retry limit exhausted, runtime ceiling, or
   an abnormal exit/signal like a forced `tmux kill-session`): why it
   stopped, how long it ran, and how many invocations succeeded/failed.
   Read this first. One caveat for the signal-triggered case: bash only
   runs a trapped signal's handler once it's back to waiting on
   something interruptible, so if the kill/kill-session lands while a
   `claude -p` invocation is actually in flight, this file doesn't get
   written until that invocation exits on its own — a genuinely hung
   invocation delays it too, it isn't instantaneous.
2. `docs/agents/logs/OVERNIGHT_SUMMARY.*.md` — present if the run ended
   via a planned end condition (stop file, duration ceiling, or
   `OVERNIGHT_END_TIME`) rather than a halt. This is the agent-authored,
   narrative version: what got done, whether eligible work likely
   remains, and a concrete suggested next step — read it before the
   mechanical log.
3. `docs/agents/logs/overnight-runner.log` — the full per-invocation
   timeline if you need more detail than the summaries above.
4. `docs/agents/logs/OVERNIGHT_HALT.*.md` — if the loop ever found no
   eligible work, each occurrence is archived here with a timestamp
   (there can be several from one night, since a single halt no longer
   ends the run — see "What runs" above); each explains the fleet's
   reasoning at that scan.
5. `docs/agents/task-status.md` — scan for any new `BLOCKED:` rows.
6. `docs/spec-questions.md` — anything logged overnight needs a human
   decision before the next run can safely continue past it.
7. `git log` on the current branch — each cohort is its own commit, named
   with its `run_id` and task IDs.
8. `docs/agents/overnight-goals.md` should already be current — every
   cohort (and the wrap-up invocation) reconciles it automatically now.
   If you have new priorities beyond what the last run already knew
   about, edit it anyway; the loop re-reads it every invocation, no code
   change needed, and picks up the change on its own within its
   halt-retry window if a previous run is still up (see "What runs"
   above).

## Prerequisites

Same preconditions as any run per `docs/agents/runbook.md`: `task-status.md`
up to date, `make check` green on the current tree, no unresolved
`BLOCKED:` entries on the critical path. `tmux` must be installed
(`apt-get install -y tmux`); if you're on a disposable root VM, also
consider `apt-get install -y bubblewrap socat` so Claude Code's own
command sandboxing doesn't silently disable itself (see
`scripts/fleet/overnight_runner.sh`'s header comment for why).
