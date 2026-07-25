# Overnight goals

**Last set:** 2026-07-25, at user request. **Read by:** `overnight_prompt.md`,
as priority guidance only — see "What this document is not" below before
using it for anything else.

## Current goal set (in priority order)

1. **T11.3 — Wire filesystem discovery for newly registered sync roots.**
   `TODO` in `docs/build-plan.md`/`docs/agents/task-status.md`. Closes the
   T11.1-surfaced gap (`docs/spec-questions.md`) where a brand-new sync
   root's pre-existing files are never scanned. Dispatch this first — T11.4
   depends on it and is vacuous without it.
2. **T11.4 — Scaled dogfood ingestion smoke test (1 → 10 → 100 real
   notes).** `TODO`, `Depends on: T11.3`. Do not dispatch until T11.3 is
   `DONE`. Deliberately content-blind (a fixed template anchor per file,
   never real note meaning) — see its build-plan entry for exactly why this
   does not cross into T11.2's territory. Access data in `data/(10) Concepts`.
3. **Bootstrap-token gap (`docs/spec-questions.md`, T11.1 entry 1).** Lower
   priority, no task registered yet because the correct fix is almost
   certainly documentation-only: the workaround (`docs/dogfood/README.md`
   step 6, mirroring `tests/battery/soak.py`'s own pattern) already works
   and is documented. If picked up, the DoD is "confirm the workaround is
   the intended permanent answer and mark the spec-question resolved" —
   **never** invent a new `/tokens` bootstrap endpoint or CLI flag not in
   `mvp-spec.md` §4.11/§4.12 (rule 2). This is optional filler, not a
   priority item; skip it if T11.3/T11.4 fill the night.

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
