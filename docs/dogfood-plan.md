# Dogfood gate: what to watch for, and how to tell intended from not

**Date:** 2026-07-21. **Status this document describes:** the MVP build plan
(M0–M10) is code-complete on Linux — every task in `docs/build-plan.md` is
`DONE` and independently verified (`docs/agents/task-status.md`). This
document is not another status report; it's the operating manual for the
one-month dogfood gate that comes next, written so that, day to day, you
can tell **intended behavior** from **a bug worth filing** from **a known,
accepted limitation** from **a pre-registered signal that changes the
product's direction** — four different things this project has a habit of
conflating if you don't stop and check which one you're looking at.

---

## 0. Read this before you start: one real precondition gap

Vision.md's own phase-gate logic (§9) says Phase 1 exits when acceptance
stories 1/7/8/9 pass **on Windows** — CRLF handling, file-locking retry,
antivirus watch-noise, the `msvcrt` single-instance lock, cloud-vault
(OneDrive/Dropbox) detection — and only then does Phase 2, which the
dogfood month is the exit criterion for, begin. As of this document, **none
of that has ever run on Windows.** Every line of it is written and
code-reviewed; none of it has been runtime-exercised outside this Linux
development environment. `docs/acceptance.md` says this honestly (rows 7
and 9 name the Windows leg as "pending first CI push"), and it is the
single highest-risk, least-tested surface in the entire codebase — higher
risk than anything below in this document, because it's an entire
execution environment that has never actually run the code.

**This is your decision, not this document's:** you can (a) dogfood on
Linux now, with Windows as a known, tracked gap you'll close before calling
Phase 1 truly exited, or (b) hold the dogfood clock until a Windows CI run
(or manual Windows smoke test) happens first. Either is defensible. What
would not be defensible is starting the month believing Windows is
verified when it is only reviewed — so if you dogfood on Windows before a
CI run, **expect first-week bugs specifically in**: daemon autostart/lock
behavior, file-locking retries when Obsidian holds a handle, and vault
behavior if your vault lives inside OneDrive/Dropbox. Those are exactly the
untested surfaces, not random noise.

**Update, 2026-07-24 through 2026-07-26 (this gap is now closed) —** the
paragraph above is left as-written for the historical record (it was
accurate when this document was authored, 2026-07-21), but is now stale:
this precondition gap was closed in the days immediately after. Real
Windows dev-host runs on 2026-07-24 and 2026-07-25 found and fixed five,
then four more, genuine Windows-only bugs this document predicted almost
exactly — CRLF corruption in the sync write-back path, the Windows RSS
sampler silently returning 0, the `msvcrt` single-instance lock raising
the wrong exception type on a real second-acquisition, and (2026-07-25) a
real autostart/kill-9 recovery demonstration via Task Scheduler. The
hosted `windows-latest` CI leg itself went genuinely green for the first
time in this repo's history on 2026-07-26 (run `30183257449`, commit
`aa07bad`, alongside `ubuntu-latest`), after separately resolving a
GitHub Actions billing block. Per `docs/acceptance.md`'s own summary,
**all nine acceptance stories are green** as of that run (story 1's
remaining ≤3s manual-timing leg was the last one, closed 2026-08-01 — see
`docs/acceptance.md` row 1) — the Phase 1 exit condition this section
worried about is met, and per vision.md §9's own sequencing this is what
actually starts the one-month Phase 2 dogfood clock this document is the
operating manual for. See `docs/acceptance.md`'s dated callouts (2026-07-24,
2026-07-25, 2026-07-26) for the full bug-by-bug writeup — do not re-derive
this from scratch, it is already documented there. What remains open and
worth tracking, per that same document: the *literal* 24-hour
`nightly-soak` duration (pending its first fired scheduled run, not
blocked — the cron job is wired and runnable), and a genuine
non-ephemeral real-deployment autostart/kill-9 attestation (the local
Windows dev-host demonstration is real evidence toward this, not a
substitute — hosted CI runners are destroyed per-job and can't produce
this leg by nature). Neither of those blocks the dogfood month that is
now, in fact, already running.

---

## 1. What this month has to prove (the actual thesis, not a vibe)

Quoting vision.md §8 directly, so there's no drift from what "success"
means: *"one user (the founder) can run the full loop inside their existing
Obsidian vault — capture at typing speed via deterministic syntax, truth
store accumulates, interface breaks flag exactly the right dependents,
staleness stays bounded, round-trip is lossless in-contract — for one month
at ~500 canonical nodes across the two launch domains (universal concepts;
task management...), and would not go back."*

That's the whole bar. Everything below is either a way to recognize whether
you're clearing it, or a way to avoid mistaking noise for signal while you
try.

---

## 2. The four-way classification

Every surprising thing that happens this month falls into exactly one of
these. Getting the bucket wrong is the expensive mistake — filing a known
limitation as a bug wastes a cycle; dismissing a real kill-signal as
"just a bug" burns a month before you notice the product doesn't work.

### A. Intended behavior — recognize it, don't report it

A representative (not exhaustive) list, phrased as "if you see exactly
this, it's working":

- **Capture:** typing a claim/definition/task via the deterministic syntax
  (footnote IDs, `- [ ]` task lines, indentation for composition) creates
  nodes with zero model in the loop, offline. A `^tm-new` line gets
  rewritten with a minted ID by the daemon on the next sync cycle — this
  rewrite is expected to appear "by itself" in the file; it is not an
  external edit, it's the intended write-back.
- **Contradiction surfacing:** creating a new **claim** node whose body
  textually overlaps an existing live claim returns up to 5 candidates
  (exact duplicates ranked first) with each candidate's text, date, and any
  attached evidence — via the `POST /nodes` response, not a popup or a
  queued review item. This only fires for `type == "claim"`; creating a
  definition, entity, task, etc. will never show candidates, by design
  (vision §8 story 2's literal subject is claims).
- **Staleness/invalidation:** editing a definition's *wording* (a patch)
  never flags anything; changing, splitting, retyping, or removing a
  *facet* that something else depends on (a major edit) flags exactly the
  bound subscribers, once, non-transitively (a stale-but-unreviewed
  dependent does not cascade further staleness until a human clears it).
  If you see a precision edit cascade staleness, that's wrong — see the bug
  section below. If you see a facet-breaking edit flag only its direct
  facet-bound subscribers and nothing further downstream, that's correct.
- **Tasks:** an indented task list compiles to `composes` edges;
  transcluding the same task into three notes shows one shared state
  everywhere; closing the **last** open subtask enqueues exactly one
  `subtasks_closed` review item flagging the supertask — the supertask's
  own `task_state` is **never** auto-closed by this. You close it
  yourself, always. Today this fires through the Obsidian
  checkbox-toggle/sync path; it does **not** yet fire through a bare
  `PATCH /nodes` API call (see §3 below — this is a known limitation, not
  a bug).
- **Refactor:** splitting or merging a node always produces a tombstone +
  redirect and a reassignment queue covering every inbound reference —
  zero dangling IDs, by construction and property-tested.
- **Time travel:** any node renders as of any past instant, including what
  was believed then and what has changed since.
- **Sync:** editing a managed file in Obsidian in-contract round-trips
  losslessly, including through an offline-then-reopened daemon (the
  three-way base-snapshot reconciliation). A formatter or plugin that
  rewrites >25% of a file's managed blocks in one cycle triggers
  **pause & diff** — the daemon makes zero writes, snapshots the file, and
  opens one review item with a diff, rather than guessing at repairs. This
  is intended and should feel like a circuit breaker, not a malfunction.
- **Review economy:** the daily active queue never exceeds 10 items,
  ordered by staleness age then inbound-edge count (there is deliberately
  no third "importance flag" tiebreaker in the MVP — see §3).
- **Daemon residency:** the daemon survives reboot (autostart), survives a
  `kill -9` mid-sync (reconciles idempotently on restart — the same code
  path as any offline period), and idles at ~0% CPU / well under 150MB RSS
  when nothing is happening.

### B. Genuine bug — file it

If something contradicts the "intended" list above, or silently does
something vision.md's own falsified-approaches list (§5) explicitly
forbids — silent global edit propagation (F3), silent repair outside the
certainty criterion (§4.7), a dangling reference after any operation, an
unbounded/growing review queue, RSS climbing past 150MB with no recovery,
or the daemon writing anything the user didn't cause and can't see in the
audit log — that's a real bug. File it with: what you did, what you
expected (cite the relevant intended-behavior line above or the vision/spec
section), what actually happened, and whether it reproduces. Use
`docs/spec-questions.md`'s entry format as the template even for pure bugs
(not just ambiguities) — it already captures exactly the right fields
(where, what was expected, what to do about it) and keeps one place to
scan.

### C. Known, accepted limitation — do NOT file, this list exists so you don't have to

These are real gaps, already found and deliberately deferred (not missed
by accident) — re-reporting them wastes a cycle re-discovering what's
already written down in `docs/archived-questions.md`. If you hit one of
these, this document is the fix, not a new bug report:

| You'll notice... | Why it's not a bug |
|---|---|
| `PATCH /nodes/{id}` cannot flip a task's `task_state` via a bare API call — only the Obsidian checkbox/sync path closes tasks today | `PatchNodeBody` deliberately has no `task_state` field (mvp-spec §4.11's own table never lists one); task closure is a contract-syntax concept, not an HTTP PATCH field, by spec design (T10.2c) |
| `violation_rate`/`auto_repairs`/`sync_cycle_ms` on the dashboard read `0`/empty right after a fresh daemon start, or reset after a restart | The metrics recorder is deliberately in-process/in-memory (same precedent as the rate limiter) — it's a real, live signal once cycles run, but resets on every restart; this is documented, not a truth invariant |
| Periodic "recheck after N days" triggers never fire, no matter how long a node sits | `recheck_after`'s daily-tick leg has no persisted schedule in the MVP schema — explicitly descoped post-MVP (T10.2c ruling); `all_subtasks_closed` and facet-interface-break triggers both work today, this specific condition doesn't yet |
| A conflict branch's recorded "fork point" is the node's current head, not the exact commit the vault last agreed on | Accepted for the MVP; the true fork point would need a new schema column that nothing yet consumes (T5.5) |
| `as_of` queries can, in rare cases around a conflict branch, return a branch commit instead of the mainline head for that instant | The as-of semantics predate branching and are branch-unaware; not wrong, just coarser than a fully branch-aware query would be (T5.5) |
| Hard-deleting an S0 node that's still referenced by an Obsidian anchor leaves the vault line behind, surfaced as `E_UNKNOWN_ANCHOR` only the next time you touch that line | S0 hard-delete has no vault-side propagation mechanism by design (no "delete tombstone" concept exists for S0); the line isn't silently lost, it's flagged on next contact (T5.8-4) |
| The daily review queue's ordering has no "I flagged this as important" override | No user-flag column exists in the frozen schema for the MVP; ordering is staleness-age then inbound-edge-count only (T7.5) |
| A `POST /nodes` proposal approval, or a split's reassignment-queue resolution, records resolution `still_holds` rather than something reading as "approved"/"reassigned" | `still_holds` is deliberately repurposed ("accepted as proposed, no revision") rather than inventing a new enum value against the frozen schema (T7.5/T7.6) |
| Free-prose capture, an LLM suggesting structure, contradiction detection on anything richer than exact/near-duplicate text | Front-end B (the LLM decomposer) is explicitly Phase 3, entirely out of MVP scope — not a missing feature, a not-yet-started phase |
| No mobile app, no multi-device sync, no federation, no monetization, no MCP server, no task recurrence/scheduling, no executable script notes | All explicitly out of MVP scope per vision §8's own "Out of scope" line — don't file these as gaps, they're phase-4/5 or permanently excluded |

### D. Kill-signal / pre-registered falsification — not a bug, a decision point

Vision §9 and §11 pre-register exactly what should make you stop and
reconsider the design, not just patch around it. These are the ones to
actually watch, because confusing one for an annoying bug (or vice versa)
is the single most expensive mistake you can make this month:

- **Review inflow chronically exceeds capacity.** If the daily queue is
  regularly hitting or would exceed its cap of 10 even after resolutions,
  vision §7.7/§11 is explicit: *"if inflow exceeds capacity persistently,
  the product is failing, not the user."* The fix is redesigned damping
  (narrower facet binding, better non-transitive staleness), never "just
  raise the cap" or "just resolve faster."
- **Facet coverage stays chronically low.** If most of your S2+
  definitions never accrete real (non-`*`) facet bindings, the
  interface-break invalidation walk is inert — you have a transclusion/task
  syncer, not a truth-maintenance system, R8's exact failure mode. This is
  the reason the metrics dashboard tracks `facet_coverage` at all; watch it
  from week one, not at the end of the month.
- **Real-use contract violation rate stays high.** Vision §11: the
  violation rate should "feel like a spellchecker, not a nag." If ordinary
  Obsidian editing (not adversarial testing) chronically produces
  violations, the contract doctrine itself is in question — Phase 1's own
  kill signal (§9), worth re-litigating even though the milestone gate has
  technically passed.
- **The founder-gate itself, at month's end:** ≥500 canonical nodes
  accumulated, review inflow ≤ capacity the whole month, and — the
  qualitative bar — you genuinely prefer this to plain notes and would not
  go back. Vision §9 is explicit that this is a **go/no-go for everything
  after**: Phase 3 (LLM decomposer) and beyond do not proceed on a failed
  gate. Don't let a month of real usage end without deliberately checking
  this against the actual thesis in §1 above, not against "did anything
  crash."
- **The border-toll (R10), watched not solved.** Every capture pays a
  classification decision at the boundary (is this a task? a claim? a
  definition?). Vision explicitly accepts this friction rather than solving
  it, but asks you to track the "crossing rate" (how often you cross that
  boundary) as a leading indicator. Rising friction here isn't a stop-ship
  signal by itself, but it's the canonical early warning if the whole
  capture experience is starting to feel like ontology engineering instead
  of typing.

---

## 3. What to actually check, and how often

**Daily (takes under a minute):** open the dashboard (`GET /dashboard`) and
glance at facet coverage, today's review-queue size (should never exceed
10), and whether anything in the queue has sat unresolved past ~7 days for
a high-inbound-edge-count node (median staleness age <7 days for
top-centrality nodes is the vision §11 target).

**Weekly:** check `rss_bytes`/`idle_cpu_pct` haven't drifted (budget: RSS
<150MB, idle CPU ≈0%); check `inflow_variance_30d` isn't showing a spike
you didn't expect (a real hub refactor should route through branch-and-
migrate per R13, never a bulk-approve session); reread the review queue's
resolution history and ask honestly whether the week included at least one
genuine "this contradicts what I believed, with source" moment — vision
§2/§11 calls this the whole product's north star, and it's explicitly
engineered for, not hoped for. If week one passes with zero contradiction
surfacing ever firing, that's worth investigating (is your usage pattern
generating claim-type nodes at all? story 2 only fires on claims).

**End of month:** the founder-gate checklist in §2.D above, plus a genuine
gut check against §1's thesis sentence, word for word.

## 4. If something breaks mid-month

- **Daemon crash or forced kill:** expect it to reconcile idempotently on
  restart via the same three-way path as any offline period. This is
  exactly the scenario T9.5/T5.6 tested — if restart does *not* converge
  cleanly, that's a genuine, high-priority bug (§2.B), not a limitation.
- **A plugin/formatter mangles a file:** expect pause & diff (§2.A above),
  never silent data loss and never a crash. If you see silent loss, that's
  the highest-severity bug class this project has (it's exactly what the
  contract doctrine exists to prevent) — file it immediately with the
  before/after file contents attached.
- **You're not sure if something is a bug or a known limitation:** check
  the table in §2.C first. If it's not there, check
  `docs/spec-questions.md`/`docs/archived-questions.md` for the relevant
  task ID before filing — most genuinely surprising behavior in this
  codebase has already been found once and has a documented, deliberate
  reason.
