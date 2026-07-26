# Overnight goals

**Last refreshed:** 2026-07-26 (T9.6 and T9.7 both landed same day;
T9.8 registered from a real CI finding, then marked `BLOCKED: not
fleet-dispatchable` the same day once its actual scope — a multi-hour
real soak run — turned out to not fit a single worker turn. See
`docs/agents/task-status.md` M9 and `docs/build-plan.md` for the full
detail. **Currently zero eligible `TODO` rows exist anywhere in the
build plan** — see below.). **Read by:** `overnight_prompt.md`, as
priority guidance only — see "What this document is not" below before
using it for anything else.

## Current goal set: none dispatchable

There is nothing for `fleet-orchestrator` to pick up right now:

- **T9.8 — Nightly-soak RSS budget breach.** Real, still-open finding
  (run `30194717387`, 2026-07-26, breached the 150MB DoD ceiling ~4.5h
  into the first real scheduled 24h run — see the T9.5/T9.8 rows in
  `docs/agents/task-status.md` for the full evidence, including a
  promising WAL-checkpoint lead from the instrumentation step that did
  land). **Deliberately marked `BLOCKED: not fleet-dispatchable`, not
  `TODO`** — its remaining steps need multiple hours of real wall-clock
  soak execution per run, which no single `fleet-worker` turn can
  complete (its Bash tool is capped well below that duration, and
  `fleet-worker.md` requires killing any background process before
  returning rather than leaving it running across turns). If a human
  wants to pick this up, the right mechanism is `gh workflow run`
  against the existing `nightly-soak` `workflow_dispatch` input,
  checked in a later session — not a synchronous local run. Do not flip
  this row back to `TODO` for the orchestrator to find; that would just
  burn the overnight window on a task that structurally cannot finish.
  Does **not** block dogfooding — see the T9.5 row for why.

- **Bootstrap-token gap (`docs/spec-questions.md`, T11.1 entry 1).**
  Unchanged from prior goal sets, still optional filler, still not a
  registered build-plan task — the correct fix is almost certainly
  documentation-only (confirm the existing workaround in
  `docs/dogfood/README.md` step 6 is the intended permanent answer and
  mark the spec-question resolved). Since it has no `TODO` row, it is
  not something the orchestrator can select either; it would need to be
  picked up by a human or an explicit, non-autonomous request.

- **T11.2** stays `BLOCKED: human-only` by design (see below) — never
  eligible regardless of any refresh of this file.

An overnight loop started right now will correctly scan, find no
eligible `TODO`, and write a halt file per "When the list is empty"
below — that is the right behavior, not a failure to work around.
Generating the next real goal set requires a human-driven spec-vs-
shipped-code audit (same procedure that found T10.2c, T9.2c, T9.3b,
T9.6) before there is anything new to dispatch.

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
T9.6 itself landed 2026-07-26, same day — see `docs/archived-questions.md`.
Wiring in a real `watchdog.Observer` and doing real live-daemon manual
verification (not just automated tests) surfaced T9.7 directly, and the
very next real *scheduled* `nightly-soak` CI run (the literal 24h leg,
finally possible now that CI billing is fixed) surfaced T9.8 — both
genuine production findings, not narrowest-reading ambiguities, which is
why neither has a `docs/spec-questions.md` entry (see T9.6's own
"Narrowest reading taken: N/A" for the established precedent on that).

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
