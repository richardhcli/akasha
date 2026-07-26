# Overnight goals

**Last refreshed:** 2026-07-26 (T9.6 registered — see
`docs/agents/task-status.md` M9 and `docs/build-plan.md`; this refresh
follows the same reconciliation procedure `overnight_prompt.md` step 9
and `overnight_wrapup_prompt.md` now apply automatically after every
cohort, so this file shouldn't need another manual refresh unless
priorities change). **Read by:** `overnight_prompt.md`, as priority
guidance only — see "What this document is not" below before using it
for anything else.

## Current goal set (in priority order)

1. **T9.6 — Wire the live filesystem `Watcher` into `daemon.serve()`.**
   The top priority. A real, confirmed (not narrowest-reading-ambiguous)
   gap: `mvp-spec.md`'s own architecture diagram says "watcher →
   sync/reconcile → kernel", but `Watcher` (T5.3, fully built and unit
   tested) has zero production call sites — a running daemon only ever
   reconciles at startup or on an explicit `POST /v1/sync/rescan`, never
   on a live filesystem edit. Found via this project's standard
   spec-vs-shipped-code audit (the same method that found T10.2c, T9.2c,
   T9.3b, T11.3). Full Goal/Depends on/Files/Spec/Scope-narrowing/Steps/
   Verify/DoD are in `docs/build-plan.md`'s M9 section; all of its
   `Depends on` tasks are already `DONE`, so it is immediately eligible
   for normal `fleet-orchestrator` dispatch. **Read the Scope-narrowing
   section before dispatching** — it names a specific, easy-to-miss
   correctness pitfall (the watcher's callback must bind to ONE
   persistent `Reconciler`, never a fresh one per event, or echo
   suppression and cross-file-move tracking silently break while a naive
   test would still pass).

2. **Bootstrap-token gap (`docs/spec-questions.md`, T11.1 entry 1).**
   Unchanged from the prior goal set, still optional filler, not a
   priority — pick up only after T9.6 lands (or in parallel, since it
   touches no file T9.6 touches). No task registered yet because the
   correct fix is almost certainly documentation-only: the workaround
   (`docs/dogfood/README.md` step 6, mirroring `tests/battery/soak.py`'s
   own pattern) already works and is documented. If picked up, the DoD is
   "confirm the workaround is the intended permanent answer and mark the
   spec-question resolved" — **never** invent a new `/tokens` bootstrap
   endpoint or CLI flag not in `mvp-spec.md` §4.11/§4.12 (rule 2).

If the loop finds no eligible `TODO` beyond these, it halts normally —
see "When the list is empty" below for how a human generates the next
real goal set (T11.2 remains the sole other non-`DONE` build-plan task,
and it's `BLOCKED: human-only` by design, not something this file can ever
make eligible).

## Context for this refresh (2026-07-26)

Hosted GitHub Actions CI was fully non-functional (account billing) from
2026-07-24 through earlier this same day — see `docs/agents/task-status.md`'s
top-of-file callout and `docs/acceptance.md`'s matching one. It is now
fixed and the repo has its first fully green hosted CI run in its
history. That real execution immediately found and closed two more real
bugs (a stale `astral-sh/setup-uv` action tag, and a genuine E20
5,000-block perf-gate failure in `sync/reconcile.py` fixed by removing
two redundant full-file parses and an N+1 node-fetch pattern — see recent
commits). None of that is overnight-loop-actionable (it's already done),
but it's the reason T9.6 was found *now*: closing out the Windows-CI leg
prompted a fresh spec-vs-shipped-code audit, per this file's own "When
the list is empty" procedure below, which is what actually surfaced T9.6.

## What this document is not

This is priority guidance among tasks that are **already** literal `TODO`
rows in `docs/agents/task-status.md` — it does not authorize dispatching
anything that isn't. `fleet-orchestrator`'s eligibility scan only selects
literal `TODO` rows (confirmed against
`.claude/agents/fleet-orchestrator.md`); this document does not add a
second, prose-driven work-selection path into a
`--dangerously-skip-permissions` loop. If every goal above is `DONE` and
this document hasn't been refreshed yet, the loop's normal build-plan scan
still applies — it just currently has nothing else eligible (see "When the
list is empty" below).

**T11.2 stays `BLOCKED: human-only`.** Nothing in this document, tonight or
any future revision of it, authorizes flipping that row to `TODO`. Deciding
which real personal-note spans become tracked claims/entities is reserved
for a human by `docs/vision.md`'s human-in-the-loop invariant (PRD §5
F-list, R9) — see the M11 header note in `docs/build-plan.md`.

## When the list is empty

If every goal above is `DONE` (or was never eligible, e.g. still blocked on
a dependency) and `fleet-orchestrator`'s scan of `docs/build-plan.md` +
`docs/agents/task-status.md` finds no other eligible `TODO`, the loop does
**not** invent new goals — it writes `docs/agents/logs/OVERNIGHT_HALT.md`
per `overnight_prompt.md`'s existing "When to stop instead of guessing"
section and stops. Generating the *next* goal set is a human decision,
using the procedure this project has actually used every time so far (the
same method that found T10.2c, T9.2c, T9.3b, and the T11.1 gap this
document's T11.3 fixes): **audit `docs/mvp-spec.md` section by section
against the shipped code** — for each spec'd behavior, grep for the
function(s) that implement it and confirm they have a real production call
site (not just a test calling them directly), and confirm the described
end-to-end path actually fires under normal use, not just under direct
unit invocation. A gap found this way gets a `docs/spec-questions.md`
entry and, once scoped, a new build-plan task in the same shape as the
ones in this file — then a refreshed revision of this document naming it,
same as this one did for T11.3/T11.4.

Once a human refreshes this document with a new goal set, the loop picks
it up on its next invocation automatically — no code change needed, since
`overnight_prompt.md` re-reads this file every run.
