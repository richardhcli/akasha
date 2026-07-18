# Spec questions

Log of ambiguities hit while implementing `docs/build-plan.md`. Per build-plan
rule 0.2 / rule 2: never invent schema, endpoints, ID formats, or grammar
beyond `docs/mvp-spec.md`. When something is ambiguous, implement the
narrowest reading, add a `# SPEC-QUESTION:` comment at the site, and log an
entry here so a human can resolve it.

Entry format:

```
## <task ID> — <one-line question>
- **Where:** <file:line>
- **Narrowest reading taken:** <what was implemented in the meantime>
- **Resolution:** <filled in once a human answers; leave "open" until then>
```

This file holds **open** questions only. Resolved entries are moved to
`docs/archived-questions.md` in a batch when the milestone that raised them
closes (context-size optimization — an agent scanning for outstanding
ambiguities shouldn't have to read past closed ones). See that file for the
full resolved history: M1 (T1.3/T1.5/T1.6/T1.7), M3 (T3.1/T3.2/T3.5/T3.6×2),
M4 (13 entries, 2026-07-12), M5 (10 entries: T5.1/T5.5/T5.8-*, 2026-07-13),
M6 (1 entry: T6.5, 2026-07-14), and M8 (4 entries: T8.0/T8.1/T8.3/T8.5b,
2026-07-18 via fable rulings).

**Open questions: 10** (6 from M7, logged 2026-07-14, plus 4 from M9/T9.2+T9.3,
logged 2026-07-18). T7.2 delete_node gap RESOLVED 2026-07-15 via follow-up
T7.2b; the 4 M8 questions (T8.0/T8.1/T8.3/T8.5b) RESOLVED 2026-07-18 via
fable rulings (see `docs/archived-questions.md`). The remaining 6 M7 entries
and the 4 new M9 entries need a product/spec decision before archiving.

## T7.1 — `composes_touched_facet` predicate is undefined in §4.9
- **Where:** `src/akasha/tms/invalidate.py` (`_composes_touched_facet`, inline `# SPEC-QUESTION:`).
- **Narrowest reading taken:** §4.9's pseudocode calls `composes_touched_facet(edge, touched)` but never defines it. The predicate's first two clauses (`facet_binding in touched` / `facet_binding == '*'`) already cover every `composes` edge with a specific or wildcard binding, so this third clause can only add coverage for a `composes` edge with `facet_binding IS NULL` (a whole-node composition, no facet binding). Implemented as: such a whole-node `composes` edge is touched by ANY non-empty `touched` set — any interface break on the target is relevant to a plain "this node composes that node" subscription, regardless of which facet broke. Covered by a unit test (fires on non-empty touched, silent on empty).
- **Resolution:** open.

## T7.7 — `Facet.name` for facets-from-spans-minted facets
- **Where:** `src/akasha/kernel/store.py` (`mint_facet_from_span`, inline `# SPEC-QUESTION:`).
- **Narrowest reading taken:** §4.2's `Facet.name` is a "short label, unique per node," but the facets-from-spans capture flow (§T7.7) supplies only `facet_span` (the highlighted text) — no name. Implemented `name = facet_id` (the id8 is unique by construction; the span text is neither guaranteed unique nor a short label). The mint commit uses `change_class="minor"` (a brand-new v1 facet is neither removed/renamed nor version-bumped, so §4.9's heuristic gives non-major — NOT a spec question, just noted).
- **Resolution:** open.

## T7.2 -- S1+ node retraction via `delete_node` never wires into `invalidate`
- **Where:** `src/akasha/kernel/store.py` (`delete_node` -- NOT edited by T7.2, which only wired `commit_node`).
- **Narrowest reading taken:** spec 4.9 says "node retraction is always major touching all facets." T7.1's `invalidate` already flags every bound subscriber when handed the full facet-id set (unit-tested), and T7.2 wired that trigger into `commit_node` only (per T7.2's Files list + the orchestrator's sanctioned-edit scope). But a real S1+ tombstone via `delete_node` does not go through `commit_node`, so an actual node-retraction API/CLI call -- as opposed to a synthetic "major commit touching all facets" -- would not currently fire `invalidate`. Left as-is (out of T7.2's scope); the synthetic path is covered and tested.
- **Resolution:** **RESOLVED 2026-07-15 (fixed now — product decision: fix, not defer).** Follow-up task T7.2b wired the S1+ tombstone branch of `store.delete_node` through `invalidate`: it captures the node's full facet-id set before tombstoning, and (after the tombstone UPDATE, BEFORE `_reassign_inbound_edges` so subscriber edges still have `dst == node_id`) calls `invalidate(conn, node_id, head_hash, touched=all facet_ids)` inside the existing transaction (uses `enqueue_review_within_transaction`, deferred import to dodge the circular import — same pattern as `commit_node`). S0 hard-delete branch untouched. `tests/integration/test_tms.py::test_s1_node_retraction_flags_dependents` proves both the pure-tombstone path (facet-bound AND `'*'`-bound dependents flagged) and the `redirect_to` path (dependent flagged, proving invalidate runs before edge reassignment). Full gate green (integration 109). Since `DELETE /nodes/{id}` calls `store.delete_node`, the API path is covered too.

## T7.3 -- where does `recheck_after`'s per-node ISO-date/period schedule persist?
- **Where:** `src/akasha/tms/triggers.py` (module docstring + `run_daily_tick`, inline `# SPEC-QUESTION:`).
- **Narrowest reading taken:** spec 4.10 gives `recheck_after` "params: an ISO date, period" but 4.4's DDL has no column/table for a per-node recheck schedule, and T7.3's Files list is `tms/triggers.py` only (no migration allowed). Adopted: the date/period ride transiently in a caller-supplied `TriggerContext` per `evaluate`/`run_daily_tick` call rather than inventing new persisted state; `run_daily_tick(conn, contexts)` is a thin iterate-and-evaluate wrapper. Sourcing which nodes are due and their schedules (where `recheck_date`/`period` live between ticks) is left to whichever later task wires this into the daemon's daily-tick driver.
- **Resolution:** open -- resolve when the daily-tick daemon driver lands (needs a decision on persisting recheck schedules, possibly a migration).

## T7.5 -- daily-cap ordering references a "user flag" with no backing DDL column
- **Where:** `src/akasha/tms/review.py` (`active_queue`, inline `# SPEC-QUESTION:`).
- **Narrowest reading taken:** spec 4.9's daily cap orders by "(staleness age, inbound-edge count, user flag)" but `review_queue`'s DDL (4.4) has no user-flag/priority column. Implemented the sort key as `(created_at ASC, inbound_edge_count DESC)` with the user-flag tiebreaker treated as absent/constant (nothing to read). The cap is READ-SIDE only (`enqueue_review` stays unbounded — a write-side cap would silently drop TMS signals, violating zero-silent-guesses).
- **Resolution:** open -- a future task adding a user-flag/priority affordance (UI flag on a review) would need a migration + a third sort key.

## T7.5 -- resolution enum has no member for create-node-proposal approval
- **Where:** `src/akasha/tms/review.py` (`approve_proposal` / `_PROPOSAL_APPROVAL_RESOLUTION`, inline `# SPEC-QUESTION:`).
- **Narrowest reading taken:** the `resolution` enum is `still_holds|revised|retracted|dismissed` (4.4) — none semantically means "a create-node proposal was approved and its node minted." `approve_proposal` records `still_holds` ("accepted as proposed, no revision") when it mints + resolves. This is a genuine semantic gap (not merely a missing column); a dedicated `approved` value would need a migration + enum change.
- **Resolution:** open -- worth a human decision on whether proposal approval deserves a distinct resolution value.

## T7.6 -- split reassignment queue has no `cause_kind` enum member
- **Where:** `src/akasha/kernel/store.py` (`split_node`, per-inbound-edge `enqueue_review_within_transaction` call, inline `# SPEC-QUESTION:`); consumed by `src/akasha/tms/review.py` `resolve_reassignment`.
- **Narrowest reading taken:** `review_queue.cause_kind` is a closed enum (4.4): `facet_break|subtasks_closed|evidence_retracted|recheck|conflict|violation|proposal` -- none means "an inbound edge needs human reassignment after a split." Reusing any existing member is UNSAFE (each is load-bearing elsewhere: `recheck` gates triggers idempotence, `violation` makes an item dismissible, `proposal` routes to the mint path -- all in modules T7.6 must not touch), so overloading one would suppress a real review or mis-route resolution. Chose a new, clearly-flagged value `"reassignment"` (`enqueue_review` does no runtime enum validation, and there is no DB CHECK constraint -- verified -- so it is mechanically safe) pending a spec amendment to add it to the closed enum. `resolve_reassignment` records the outcome as `"still_holds"` (the resolution enum likewise has no "reassigned" member -- mirrors the `approve_proposal` precedent, cf. the other T7.5 entry).
- **Resolution:** open -- needs a spec amendment to add `reassignment` to the `cause_kind` enum (and possibly a `reassigned` resolution value). This is the same class of gap as the T7.5 proposal-approval-resolution question.

## T9.3 — does S0 GC scheduling also cover node-retention deletion (vision A7), or only the object-level `gc_objects` job?
- **Where:** `src/akasha/daemon.py` (`GcScheduler`, inline `# SPEC-QUESTION:`); the tension is with `docs/archived-questions.md`'s T1.7 entry.
- **Narrowest reading taken:** the archived T1.7 resolution names T9.3 as the home for a retention-based S0 *node* GC (deleting S0 nodes older than a configurable threshold, default 30 days, per `vision.md` A7) that would run BEFORE `gc_objects` reclaims the now-orphaned objects. But T9.3's actual build-plan Steps ("Run GC on a schedule/daily tick", "GC keeps referenced objects (reuse T1.7 invariant)") and DoD ("removes only orphans") describe only the existing object-level `gc_objects` job — no age-based node deletion, and neither `mvp-spec.md` nor `build-plan.md` defines a retention-days config surface (`Config` has no such field). Implemented `GcScheduler` to schedule only `store.gc_objects` (object-level orphan reclamation) + confirm log rotation, matching the literal Steps/DoD. No S0 node-retention-by-age job exists anywhere in the codebase yet.
- **Resolution:** open -- if vision A7's 30-day node retention is still required for the MVP, it needs a follow-up task (its own Files list touching `kernel/store.py` for the new age-based query + a `Config` field for the threshold) or an explicit decision that it's out of MVP scope.

## T9.2 — store.py touch outside Files list (read-only metrics aggregation helpers)
- **Where:** `src/akasha/kernel/store.py` ("T9.2 read-only metrics aggregation helpers" section, appended after `read_base_snapshot`).
- **Narrowest reading taken:** T9.2's Files list (`src/akasha/metrics.py`, `src/akasha/api/routes/health.py` (or metrics route), `tests/unit/test_metrics.py`) omits `store.py`, but rule 0.4 (all persistent-state reads/writes go through `store.py`) forces this touch — same recurring precedent as T4.2/T4.4/T4.5/T4.6/T5.1/T5.4/T5.5/T5.7. Added 7 new READ-ONLY functions (`facet_coverage_counts`, `count_reviews_created_since`, `count_reviews_resolved_since`, `list_review_created_at_since`, `count_violations_total`, `count_nodes_created_since`, `earliest_node_created_at`); none opens a write transaction (independently confirmed by the verifier). Also touched `src/akasha/api/app.py` (one import + one `include_router` call) — not logged as its own question, matching the T4.5/T5.7 precedent that a new route file's app.py registration is necessary wiring, not a separate ambiguity.
- **Resolution:** open.

## T9.2 — `violation_rate` / `auto_repairs{class}` / `sync_cycle_ms{p50,p95}` have no live producer yet
- **Where:** `src/akasha/metrics.py` (module docstring; `_CycleRecorder`, `record_sync_cycle_ms`, `record_auto_repair`).
- **Narrowest reading taken:** spec §7 defines these three metrics, but no existing DB table records per-cycle events — the §4.4 schema is frozen, and `review_queue` only records violations needing human review, never a quiet sync cycle or a certain-repair application (which by §4.7 definition never reaches the queue). Accurately observing these requires instrumenting `sync/reconcile.py`'s `Reconciler.on_change`, which is outside T9.2's Files list and was concurrently owned by other in-flight build-plan work (T9.1/T9.3) during this run. Implemented `metrics.py` with an in-process recorder (`record_sync_cycle_ms`/`record_auto_repair`); the three metrics compute from whatever's been recorded so far (`0.0`/`{}` until a future task wires the call sites into `reconcile.py`'s certain-repair/cycle-completion points). Satisfies the literal DoD ("every §7 metric appears in /v1/metrics") today.
- **Resolution:** open -- a future task (or T9.5's soak-test author) should add the `reconcile.py` call sites so these three metrics go live; until then they will read as zero/empty in production, not an error, but not representative either.

## T9.2 — `inflow_variance_30d`: population vs. sample variance unspecified
- **Where:** `src/akasha/metrics.py` (`_population_variance`).
- **Narrowest reading taken:** spec §7 names `inflow_variance_30d` but doesn't specify population variance (÷N) vs. sample variance (÷(N-1)). Implemented population variance, treating the 30-day window (zero-filled per calendar day) as a complete, bounded population rather than a sample.
- **Resolution:** open -- flagging in case a dogfood-gate threshold (PRD §11) was calibrated against the other convention.
