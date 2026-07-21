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
M6 (1 entry: T6.5, 2026-07-14), M8 (4 entries: T8.0/T8.1/T8.3/T8.5b,
2026-07-18 via fable rulings), and the **pre-dogfood triage** (9 entries,
2026-07-20 via a fable ruling: T7.1, T7.7, T7.3, T7.5×2, T7.6, T9.2×2,
T10.2b — see that file's "Pre-dogfood spec-question triage" section for the
full ruling on each).

**Open questions: 2** (both from the same 2026-07-20 pre-dogfood triage;
unlike the 9 archived alongside them, these two were judged **buildable now**
— no schema change, no dogfood data required — and are actively being
registered + fleet-built in this session rather than merely documented. They
move to `docs/archived-questions.md` once their build-plan task lands and is
independently verified; until then they stay here as in-progress, not
theoretical.)

## T9.3 — S0 GC scheduling covers only object-level GC, not vision A7's node-retention-by-age
- **Where:** `src/akasha/daemon.py` (`GcScheduler`).
- **Details:** `docs/vision.md` §14 assumption A7: "S0 default GC retention 30 days (configurable); GC blocked at S1 automatically." The archived **T1.7** resolution (`docs/archived-questions.md`) already named the intended two-step lifecycle and explicitly assigned the scheduled age-based S0 *node* deletion job to **T9.3** — but T9.3's actual build-plan Steps/DoD text only describes the existing object-level `gc_objects` orphan-reclamation job. No age-based node deletion exists anywhere in the codebase; no `Config` field for a retention threshold exists either.
- **Narrowest reading taken:** none — flagged as a real, entailed gap rather than silently left at T9.3's narrower literal scope. Fable ruling (2026-07-20): buildable now, no schema change (uses existing `nodes.created_at`/`nodes.maturity` columns; the only new state is a config value, not DB schema).
- **Resolution:** in progress — registered as build-plan task **T9.3b** (see `docs/build-plan.md`), being fleet-built this session. Will archive on independent verification.

## T9.2 — `violation_rate` / `auto_repairs{class}` / `sync_cycle_ms{p50,p95}` have no live producer
- **Where:** `src/akasha/metrics.py` (`_CycleRecorder`, `record_sync_cycle_ms`, `record_auto_repair` — all already built); `src/akasha/sync/reconcile.py` (`Reconciler.on_change` — zero call sites into the recorder).
- **Details:** spec §7 defines these three metrics; `metrics.py`'s recorder API already exists and is exercised directly by `tests/unit/test_metrics.py`, but nothing in production ever calls it, so all three read `0.0`/`{}` in a real running daemon regardless of actual sync activity. `docs/acceptance.md` row 6 (story 6, review economy dashboard) is GREEN on the strength of tests that verify aggregation math and rendering, not live production values for these three fields — a disclosure gap, not a test gap.
- **Narrowest reading taken:** none — flagged as buildable-now (fable ruling, 2026-07-20): pure wiring, no schema (the recorder is in-process, same precedent as `auth.py`'s rate limiter), no dogfood data needed. Exact call sites: wrap `Reconciler.on_change` in a `time.monotonic()`-based timer covering every exit path (`record_sync_cycle_ms`), and call `record_auto_repair(repair.code)` for each certain-repair actually applied in the non-conservative branch (not the conservative/pause&diff branch, where repairs route to review instead).
- **Resolution:** in progress — registered as build-plan task **T9.2c** (see `docs/build-plan.md`), being fleet-built this session alongside a `docs/acceptance.md` row 6 update. Will archive on independent verification.
