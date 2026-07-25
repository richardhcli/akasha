# Overnight goals

**Last refreshed:** 2026-07-25 (T11.3/T11.4 both landed `DONE` overnight —
see `docs/agents/task-status.md`; this refresh follows the same
reconciliation procedure `overnight_prompt.md` step 9 and
`overnight_wrapup_prompt.md` now apply automatically after every cohort,
so this file shouldn't need another manual refresh unless priorities
change). **Read by:** `overnight_prompt.md`, as priority guidance only —
see "What this document is not" below before using it for anything else.

## Current goal set (in priority order)

No priority goals remain: both T11.3 and T11.4 (the previous goal set)
are `DONE`. The one item below was always explicitly optional filler, not
a priority, so it stays as the only live entry:

1. **Bootstrap-token gap (`docs/spec-questions.md`, T11.1 entry 1).** No
   task registered yet because the correct fix is almost certainly
   documentation-only: the workaround (`docs/dogfood/README.md` step 6,
   mirroring `tests/battery/soak.py`'s own pattern) already works and is
   documented. If picked up, the DoD is "confirm the workaround is the
   intended permanent answer and mark the spec-question resolved" —
   **never** invent a new `/tokens` bootstrap endpoint or CLI flag not in
   `mvp-spec.md` §4.11/§4.12 (rule 2).

If the loop finds no eligible `TODO` beyond this optional item, it halts
normally — see "When the list is empty" below for how a human generates
the next real goal set (T11.2 remains the sole non-`DONE` build-plan task,
and it's `BLOCKED: human-only` by design, not something this file can ever
make eligible).

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
