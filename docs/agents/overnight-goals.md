# Overnight goals

**Last refreshed:** 2026-08-06 (M13/M14 kickoff + cohort 2 landed same day:
T13.1, T13.2, T13.3, T13.4, T14.1, T14.5, T14.6 all `DONE` via two live
`fleet-orchestrator`→`fleet-worker`→`fleet-verifier` cohorts, `make check`
green throughout — 678 tests passed, zero regressions. The predecessor
M0-M12 goal set this file used to describe is archived at
`docs/pre-mvp/task-status.md`; this file now tracks the new
`docs/build-plan.md` M13-M17 plan exclusively.
**Read by:** `overnight_prompt.md`, as priority guidance only — see
"What this document is not" below before using it for anything else.

## Current goal set: M13/M14 close-out, in dependency order

1. **T13.5 — Web UI: node view shows and toggles task state + subtask
   structure.** Depends on T13.1 (DONE), T13.3 (DONE) — eligible now.
   Needs a real headless Chromium (`uv run playwright install chromium`);
   `make check-fast` is a fallback only if a browser genuinely isn't
   available (root `CLAUDE.md` rule 7), never a substitute. Shares
   `src/akasha/ui/static/app.js` with the already-landed T14.5/T14.6 — no
   remaining file collision, safe to dispatch alone or alongside T13.6/T14.2
   (those touch different files).
2. **T13.6 — Project review-resolution commits back to the vault too.**
   Depends on T13.3 (DONE) — eligible now. Touches `routes/review.py` +
   `tests/integration/test_projection_writeback.py`; the latter is shared
   with the already-landed T13.3 but that dependency already serializes
   them, so no live collision remains.
3. **T14.2 — CLI `akasha edge add` / `akasha edge rm`.** Depends on T14.1
   (DONE) — eligible now. First link in the T14.2→T14.3→T14.4 sequential
   chain (all touch `src/akasha/cli/main.py`); do not dispatch T14.3/T14.4
   until this one is `DONE` and flipped in `task-status.md`.

Once T13.5 and T13.6 both land, M13 closes. Once T14.2→T14.3→T14.4 all
land, M14 closes. Only then do M15/M16/M17 become milestone-eligible (see
their `Depends on:` headers in `docs/build-plan.md`) — do not dispatch any
M15/M16/M17 row before both milestones are fully closed, even if a task's
own `Depends on` list looks satisfied in isolation.

**T15.2 and T16.2 stay `BLOCKED: human-only` permanently.** Nothing in
this document, tonight or any future revision of it, authorizes flipping
either row to `TODO`. Deciding which of the user's real tasks/definitions
become tracked nodes is reserved for a human by `docs/vision.md`'s
human-in-the-loop invariant (PRD §5 F-list, R9, design invariant 3) — same
standing boundary as pre-mvp T11.2, which remains `BLOCKED: human-only` in
`docs/pre-mvp/task-status.md` and is not superseded by this file. T15.1
and T16.1 (content-blind autonomous legs) ARE dispatchable once M13/M14
close respectively, but never before their milestone gate is satisfied.

## Context for this refresh (2026-08-06)

A spec-vs-shipped-code audit on 2026-08-05 found the product's two
flagship capabilities were each reachable from only part of the system:
todo sync's hub→vault half was unwired, and the definition DAG had no
CLI/UI surface to build or navigate it from. `docs/build-plan.md` M13-M17
was authored to close both gaps, validate them against real use, and
document the result. Two live cohorts already closed the flagship gap
(T13.3, hub-side mutations now re-project to the vault file) plus six
other tasks — see `docs/agents/task-status.md` for full evidentiary notes
citing real commit hashes and verifier findings on every landed row.

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

## When the list is empty

If every goal above is `DONE` (or was never eligible, e.g. still blocked on
a dependency) and `fleet-orchestrator`'s scan of `docs/build-plan.md` +
`docs/agents/task-status.md` finds no other eligible `TODO`, the loop does
**not** invent new goals — it writes `docs/agents/logs/OVERNIGHT_HALT.md`
per `overnight_prompt.md`'s existing "When to stop instead of guessing"
section and stops. If M13 and M14 have both closed and T15.1/T16.1 are also
`DONE`, the only remaining rows are T17.1/T17.2/T17.3 (doc-only, milestone-
gated on M13+M14) and the two permanent `BLOCKED: human-only` rows — once
T17.1-T17.3 land too, the whole M13-M17 plan is closed and generating the
*next* goal set is a human decision, using the same spec-vs-shipped-code
audit procedure that produced this plan (`docs/build-plan.md`'s own header
documents the method): audit `docs/mvp-spec.md` section by section against
the shipped code — for each spec'd behavior, grep for the function(s) that
implement it and confirm they have a real production call site (not just a
test calling them directly), and confirm the described end-to-end path
actually fires under normal use, not just under direct unit invocation.

Once a human refreshes this document with a new goal set, the loop picks
it up on its next invocation automatically — no code change needed, since
`overnight_prompt.md` re-reads this file every run.
