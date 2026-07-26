# Overnight goals

**Last refreshed:** 2026-07-26 (T9.6 landed same day; T9.7/T9.8
registered from real findings the T9.6 session surfaced — see
`docs/agents/task-status.md` M9 and `docs/build-plan.md`; this refresh
follows the same reconciliation procedure `overnight_prompt.md` step 9
and `overnight_wrapup_prompt.md` now apply automatically after every
cohort, so this file shouldn't need another manual refresh unless
priorities change). **Read by:** `overnight_prompt.md`, as priority
guidance only — see "What this document is not" below before using it
for anything else.

## Current goal set (in priority order)

1. **T9.7 — `GET /v1/search` 500s on a hyphenated (or other FTS5-operator)
   query term.** Top priority: a real, deterministically-reproducible
   production bug on a core, everyday user-facing feature (a human hits
   this within minutes of normal dogfood use — hyphenated terms are
   ordinary), found via T9.6's live-daemon manual verification pass. Fix
   should reuse T10.2b's existing FTS5-quoting precedent in
   `find_contradiction_candidates` rather than reinventing query
   escaping. Full Goal/Depends on/Files/Spec/Steps/Verify/DoD are in
   `docs/build-plan.md`'s M9 section; all `Depends on` are already
   `DONE`.

2. **T9.8 — Nightly-soak RSS budget breach on the first real scheduled
   24h run.** Real finding, not a harness defect: run `30194717387`
   (2026-07-26 08:30–12:58 UTC) breached the 150MB DoD ceiling at tick
   8019/43200 (~4.5h in). Heartbeat data shows a flat memory floor with
   an escalating periodic spike (~24 min cadence) — points at a
   per-corpus-size periodic cost (leading unconfirmed hypothesis: FTS5
   segment automerge, same subsystem as T9.7), not an obvious classic
   leak. Lower priority than T9.7 because it blocks the M9 DoD claim, not
   ordinary interactive dogfooding (a human restarting the daemon
   regularly never approaches the sustained synthetic load that took 4.5
   hours to trigger it). Full Goal/Depends on/Files/Steps/Verify/DoD
   (instrumentation-first, then isolate `search` vs `vault_edit` action
   weights before attempting a fix) are in `docs/build-plan.md`'s M9
   section.

3. **Bootstrap-token gap (`docs/spec-questions.md`, T11.1 entry 1).**
   Unchanged from the prior goal set, still optional filler, not a
   priority — pick up only after T9.7/T9.8 land (or in parallel, since it
   touches no file either of those touch). No task registered yet because
   the correct fix is almost certainly documentation-only: the workaround
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
