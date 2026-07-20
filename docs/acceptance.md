# Acceptance mapping (T10.3)

Maps each PRD `docs/vision.md` §8 MVP user story (1–9) to its verifying
automated test or checked manual script, and states — honestly, per rule
0.2/0.9 and vision invariant 5 ("flagged, never guessed") — whether that
verifier is **GREEN (local Linux)**, **PARTIAL** (some legs green, a named
gap open), **pending manual execution**, **pending first CI push** (the
Windows-CI attestation), or **pending first scheduled nightly run** (the
literal 24h soak).

**No bare checkmarks.** Every row states the verifier by its real on-disk
name, exactly what was run and where/when, and an explicit per-leg status.
Nothing below is represented as checked/green that was not personally run to
completion during this task.

> **Open implementation gap (added 2026-07-19, post-authoring audit):**
> **row 8 is PARTIAL, not green.** Spec §4.10's trigger evaluation has zero
> production call sites — `tms/triggers.py`'s `evaluate`/`run_daily_tick` are
> called only by tests, never by `store.commit_node` or a daily tick — so the
> supertask trigger does not fire in a really-running daemon. Registered as
> **T10.2c**; see row 8 and `docs/spec-questions.md`. All other rows are
> unaffected. This gap was found by auditing the acceptance mapping itself,
> which is exactly what T10.3 Step 2 exists to do ("any gap is a
> `# SPEC-QUESTION:`, not a silent pass").

**Environment for every "local Linux" run below:** Ubuntu (`Linux
richardhcli-Virtual-Machine 6.8.0-1062-azure`, x86_64), Python 3.14.6 (uv
venv), commit `e1bc58f4c1a4f1dceefee50b99b6d1fc202a6595`, run 2026-07-19
(all times below are that date; timestamps embedded in raw tool/log output
are UTC or `-0400` as emitted by the respective tool — noted per row).

## Full local gate (run this task, 2026-07-19)

| Command | Result |
|---|---|
| `make check` (`ruff check src tests` + `pyright --strict src` + `pytest tests/unit tests/property`) | ruff: all checks passed. pyright: 0 errors/0 warnings/0 informations. pytest: **408 passed**, 0 failed, 5.41s. **GREEN (local Linux)**. |
| `make battery` (`pytest tests/battery`) | **47 passed**, 0 failed, 2.09s (`tests/battery/test_edit_battery.py` + `tests/battery/test_windows.py`). **GREEN (local Linux)**. The Windows-specific legs of this same suite (real file-locking retry, CRLF-from-Windows-editor behavior under an actual Windows filesystem) only execute for real on a Windows CI runner — see row 7 and row 9 below. |
| `uv run python tests/battery/soak.py --hours 0.05` (the accelerated in-session proxy T9.5 established; NOT the literal 24h default) | `status: "passed"`; 90/90 ticks completed, 90/90 samples taken, `max_rss_mb: 62.52` (well under the 150MB budget), `mean_idle_cpu_pct: 0.404` (well under the 30% budget), `unhandled_exception_count: 0`. Log: `soak starting: hours=0.05 tick_seconds=2.0 total_ticks=90` → `soak complete` over 2026-07-19T11:41:40 – 11:44:40 (`-0400`, ~3 real minutes). **GREEN (local Linux, accelerated proxy).** The literal `--hours 24` run is the nightly `schedule`-gated CI job (`.github/workflows/ci.yml`, `nightly-soak`, T9.5) and has not yet executed on a real scheduled trigger — that leg is **pending first scheduled nightly run**, same framing as row 9.

These three commands were run as separate invocations (not chained with `&&`
in one shell) so that the ~3-minute soak proxy's own exit code and JSON
output could be captured and quoted verbatim above rather than swallowed by
tail truncation of a combined stream; all three independently exited 0.

---

## Story-by-story mapping

### 1. Capture (`docs/vision.md` §8 story 1)

> "Given the caffeine sentence, the system proposes the three-node
> decomposition with links and an evidence prompt in ≤1.5s; approve-all
> costs ≤3s of attention; the graph gains the nodes with full provenance."

The sentence-decomposition half of this story (front end B, the LLM
decomposer) is explicitly **out of MVP scope** (`docs/vision.md` §8 "Out of
scope for MVP" and PRD §9 Phase 3) — it is not attested here and no code
implements it. What MVP scope actually built is the **deterministic-syntax
capture path** (front end A: `akasha add` / `POST /nodes`), and per the
build-plan's corrected row 1 / the T10.3 spec-questions citation-drift
ruling (2026-07-19), this row's automated leg covers only that: the graph
gains the node with full provenance, functionally, on that path. There is
**no automated timing test anywhere in the tree** (grep-verified
2026-07-19: no `elapsed`/timing assertion in `tests/integration/`; the only
performance test in the repo is T1.9's neighborhood-fetch p95 and T5.8's
E20 sync-cycle perf, neither of which measures capture-to-approval attention
cost).

- **Functional (node creation + provenance) — GREEN (local Linux):**
  `tests/integration/test_api.py::test_nodes_create_and_get_includes_maturity`
  and `tests/integration/test_cli.py::test_new_and_get_round_trip`. Run
  2026-07-19: **2 passed**, 0 failed, 0.62s.
- **≤3s-of-attention timing assertion — pending manual execution.** No
  automated timing test exists; the M10 DoD's alternative leg (a checked
  manual script) has not been run by any human. A manual capture-timing
  check would time an operator's `akasha add ...` / `POST /nodes` round
  trip on the syntax path and confirm ≤3s wall time to a committed node.
  **This leg is unchecked and is represented here as unchecked** — no
  timing number is asserted.

### 2. Contradiction (`docs/vision.md` §8 story 2)

> "Given a new claim conflicting with an existing one, the capture response
> includes the conflict with the old claim's text, date, and evidence; the
> user can adjudicate immediately or defer (both recorded)."

Built by T10.2b (2026-07-19, fable ruling documented in
`docs/spec-questions.md` "T10.3 — story 2 has no verifier and no
implementation"). Verifier: `tests/integration/test_contradiction_surfacing.py`
+ `tests/integration/test_openapi_snapshot.py` (its own `Verify` line).
Run 2026-07-19: **9 passed**, 0 failed, 0.61s — covering exact-duplicate
ranks first with evidence, near-duplicate surfaces, non-claim create
returns `[]`, agent (proposal) create stays 202 without the field,
FTS5-hostile body returns 201 with a sane candidate list (never a 500), and
a dedicated read-only-gate test proving zero writes from the surfacing
path. **GREEN (local Linux).**

Per the T10.2b spec-questions entry, "evidence" in the candidate payload is
narrowly `node_type == "evidence"` only (excludes `proof`) — an open,
non-blocking product question, not a test gap.

### 3. Invalidation (`docs/vision.md` §8 story 3)

> "Editing `half-life(caffeine, ~5h)` to a phenotype-dependent range,
> classified major, badges `because(№1,№2)` stale within the same session;
> the badge names the cause and version; *still holds / revise / retract*
> all function and record adjudication."

Per the build-plan/§9 citation-drift ruling (`docs/spec-questions.md`
"T10.3 — story→verifier table citation drift"), coverage is deliberately
distributed across three real tests (the previously-cited
`test_facet_break_flags_subscribers` does not exist under that name):

- `tests/integration/test_tms.py::test_review_revised_reclassifies_and_cascades`
- `tests/integration/test_tms.py::test_s1_node_retraction_flags_dependents`
- `tests/unit/tms/test_invalidate.py::test_wildcard_binding_flags_on_any_break`
  (the `*`-binding-on-any-break case)

Run 2026-07-19 (integration pair): **2 passed**, 0.47s (run together with
row 5's test, see combined tail below). Run 2026-07-19 (unit,
`test_invalidate.py`, full file including the wildcard case, as part of
`make check`'s unit pass): **10 passed** (file-level; the wildcard test
specifically re-run standalone also passed). **GREEN (local Linux).**

### 4. Refactor / split-merge (`docs/vision.md` §8 story 4)

> "Splitting a node produces a tombstone redirect and a reassignment queue
> covering 100% of inbound references; zero dangling IDs
> (property-tested)."

Verifier: `tests/property/test_split_merge.py` (the actual on-disk path;
build-plan names it correctly). Run as part of `make check`'s property
suite 2026-07-19: **5 passed** (property-based, Hypothesis-driven —
included in the 408-passed `make check` unit+property total above).
**GREEN (local Linux).**

### 5. Time travel (`docs/vision.md` §8 story 5)

> "Any node renders as-of any past date, including which claims were then
> believed and what has since changed."

Per the citation-drift ruling, the correct test name is
`test_nodes_get_as_of_returns_earlier_body` (not `test_as_of`). Verifier:
`tests/integration/test_api.py::test_nodes_get_as_of_returns_earlier_body`.
Run 2026-07-19 together with row 3's two integration tests and
`test_supertask_flag` (row 8): **4 passed**, 0 failed, 0.47s. **GREEN
(local Linux).**

### 6. Review economy (`docs/vision.md` §8 story 6)

> "Daily queue never exceeds cap; dashboard shows inflow vs resolution;
> week-one experience includes at least one genuine 'this contradicts what
> you believed, with source' moment."

This story has two distinct sub-claims — cap *enforcement* and dashboard
*display* — both are covered by real, separately-cited tests:

- **Daily-cap enforcement:** `tests/integration/test_tms.py::test_review_active_queue_daily_cap`
  (T7.5). Run 2026-07-19: **1 passed**, 0.40s.
- **Dashboard display (inflow vs resolution, variance, violation rate,
  crossing rate, facet coverage):** `tests/integration/test_ui_dashboard.py`
  (T10.1, Playwright, real headless Chromium against a live daemon) +
  `tests/unit/test_metrics.py` (T9.2, the underlying `compute_metrics`
  aggregation). Command run 2026-07-19: `uv run pytest
  tests/integration/test_ui_dashboard.py tests/unit/test_metrics.py` —
  **27 passed** (1 in `test_ui_dashboard.py` + 26 in `test_metrics.py`), 0
  failed, 1.15s.

**GREEN (local Linux)** for both sub-claims. The "conversion moment" clause
(a genuine contradiction-with-source surfaced in week one) is a real-usage
outcome of story 2's mechanism (row 2, now built and green) observed during
the one-month dogfood gate itself, not something a unit/integration test
can assert in the abstract — it is the dogfood gate's own success
criterion, not a pre-dogfood acceptance row.

### 7. Contract sync losslessness (`docs/vision.md` §8 story 7)

> "Editing a projected node in Obsidian (in-contract) round-trips to the
> hub byte-losslessly... zero silent guesses across the full scripted edit
> battery."

Verifier: the scripted edit battery E01–E20, `tests/battery/test_edit_battery.py`
(T5.8), part of `make battery`. Run 2026-07-19 (full `make battery`, both
files): **47 passed**, 0 failed, 2.09s — includes the dedicated
`test_silent_guess_count_across_battery` (zero silent guesses) and the
byte-lossless round-trip assertions for E01/E02/E03/E12 etc. **GREEN
(local Linux).**

The battery's file-locking-retry and antivirus-watch-noise cases (T9.1,
`tests/battery/test_windows.py`) run and pass here too (**20 passed**,
included in the 47 above) but only exercise the *logic* on this Linux box —
the real Windows-filesystem lock/retry behavior these tests model can only
be attested for real on a Windows CI runner. That leg is **pending first CI
push** (Windows CI, per the M0/M6/M8/M9 "code-complete, CI-leg pending
first push" framing — see `docs/agents/task-status.md` milestone headers).

### 8. Tasks + supertask + S0 lifecycle (`docs/vision.md` §8 story 8)

> "...closing the last open subtask fires the trigger and the supertask
> appears in the review queue flagged 'all subtasks complete'... a quick
> inline definition is born at S0... and can be promoted up the ladder or —
> if unlinked again — deleted freely."

Verifier: `tests/integration/test_tms.py::test_supertask_flag` (T7.4,
supertask-completion trigger) + battery E06 (delete-s0, S0 free-deletion
lifecycle) + E08 (create-tm-new). Run 2026-07-19:
`test_supertask_flag` — **1 passed** (in the 4-passed batch with rows 3/5
above). E06/E08 run standalone by their parametrized ID:
`test_reused_golden_case_passes_under_its_e_number[E06-delete-s0]` and
`[E08-create-tm-new]` — **2 passed**, 0.17s.

**Status: PARTIAL — S0 lifecycle GREEN (local Linux); the supertask-trigger
production path is NOT yet wired (pending T10.2c).** The S0 half of this
story (inline definition born at S0, freely deleted when unlinked) is fully
attested by E06/E08 above. The supertask half is **not**: `test_supertask_flag`
invokes `tms/triggers.py`'s `evaluate` **directly**, and as of 2026-07-19 that
evaluator has **zero production call sites** — grep-verified: nothing under
`src/akasha/` imports `tms.triggers` at all; the sole in-`src/` reference is
`run_daily_tick`'s own internal call to `evaluate` (`triggers.py:237`), and
`run_daily_tick` has no caller either. Every external caller is a test
(`tests/unit/tms/test_triggers.py`, `tests/integration/test_tms.py`).
Spec §4.10 requires evaluation "(a) after every commit touching the
node or its children, (b) on a daily tick"; T7.3 deferred that wiring to "a
later task" and no task picked it up. So in a really-running daemon, closing
the last open subtask (via `PATCH /nodes` or an Obsidian checkbox toggle)
currently fires nothing, and the supertask would not appear in the review
queue. The trigger *semantics* are correct and tested; the *wiring* is
missing. Registered as **T10.2c** (wire `triggers.evaluate` into
`store.commit_node`, same precedent as T7.2's `invalidate` wiring); see
`docs/spec-questions.md`. This row cannot read GREEN until T10.2c lands and
an integration test drives the real API path end-to-end.

### 9. Residency (`docs/vision.md` §8 story 9)

> "The daemon survives reboot (autostarts), a kill -9 mid-sync (startup
> reconciliation converges, idempotently), and a week of continuous
> background operation within resource budget; edits made in Obsidian
> while the daemon was down are reconciled correctly on restart via the
> base-snapshot three-way path."

Two verifiers, run separately this session (2026-07-19), both against this
task's own execution, not a prior task's numbers:

- **Crash-recovery idempotence:** `tests/integration/test_crash_recovery.py::test_crash_recovery_idempotent`
  (T5.6). Command run 2026-07-19: `uv run pytest
  tests/integration/test_ui_dashboard.py tests/unit/test_metrics.py
  tests/integration/test_crash_recovery.py::test_crash_recovery_idempotent`
  — a three-file batch distinct from row 6's two-file
  (dashboard+metrics-only) command — **28 passed** (1 dashboard + 26
  metrics + 1 crash-recovery), 0 failed, 1.22s. The crash-recovery test
  itself: **1 passed**, within that 28.
- **Residency/resource-budget soak:** `tests/battery/soak.py` (T9.5). Run
  **this session**, 2026-07-19T11:41:40–11:44:40 (`-0400`), accelerated
  proxy `--hours 0.05` (~3 minutes, 90 ticks of 2s each): `status:
  "passed"`, `max_rss_mb: 62.52` (budget 150MB), `mean_idle_cpu_pct: 0.404`
  (budget 30%), `unhandled_exception_count: 0`.

**GREEN (local Linux, accelerated proxy)** for both. Two legs of this story
remain genuinely unattested from this box and are stated honestly as
pending, not silently assumed:

- The **literal 24-hour continuous-operation duration** is the
  `nightly-soak` CI job (`.github/workflows/ci.yml`, gated
  `if: github.event_name == 'schedule'`) — it has not yet run on a real
  scheduled trigger. **Pending first scheduled nightly run.**
- **Autostart-on-reboot** and **kill -9 mid-sync on a real OS** (as
  opposed to the crash-recovery test's simulated interruption) require an
  actual daemon lifecycle under systemd/launchd/Windows-service management,
  which cannot be exercised from this sandboxed session. **Pending first CI
  push / first real-deployment observation** — same framing as row 7's
  Windows-filesystem leg.

---

## Summary

| # | Story | Local-Linux verifier status | Pending leg |
|---|---|---|---|
| 1 | Capture | Functional path GREEN (2 passed) | ≤3s timing — pending manual execution |
| 2 | Contradiction | GREEN (9 passed) | none |
| 3 | Invalidation | GREEN (2+10 passed) | none |
| 4 | Split/merge | GREEN (5 passed, property) | none |
| 5 | Time travel | GREEN (1 passed) | none |
| 6 | Review economy | GREEN (cap: 1 passed; dashboard+metrics: 27 passed) | none (conversion moment is a dogfood-gate outcome, not a pre-gate test) |
| 7 | Contract sync | GREEN (47 passed) | Windows-filesystem lock/retry reality — pending first CI push |
| 8 | Tasks/supertask/S0 | **PARTIAL** — S0 lifecycle GREEN (E06/E08, 2 passed); supertask trigger GREEN only under *direct* evaluator invocation (1 passed) | **supertask-trigger production path unwired** — `tms/triggers.py`'s `evaluate` has zero call sites in `src/akasha/` (spec §4.10 (a)+(b) never wired; T7.3 deferred it, no task picked it up), so in a live daemon closing the last subtask fires nothing. Pending **T10.2c** |
| 9 | Residency | GREEN, accelerated proxy (soak: 90/90 ticks, 0 exceptions) | literal 24h duration — pending first scheduled nightly run; real-OS autostart/kill-9 — pending first CI push / first deployment |

**Seven of nine stories are fully GREEN on local Linux with no automated
gap** (2, 3, 4, 5, 6, 7, 9 — the latter two carrying only external
Windows/24h legs). The two that are not:

- **Story 8 — PARTIAL, a real implementation gap.** Its S0-lifecycle half is
  green (E06/E08), but the supertask-trigger half has **no production call
  site**: spec §4.10's evaluation is never invoked by `store.commit_node` or
  a daily tick, so the trigger cannot fire in a live daemon. This is unbuilt
  wiring, not a pending attestation — the distinction matters. Registered as
  **T10.2c**.
- **Story 1 — functional path green, ≤3s timing clause pending manual
  execution** (no automated timing test exists).

Story 9's literal-duration/real-OS clauses and story 7's Windows-filesystem
clause are the genuinely *pending-attestation* legs — flagged here as
**pending**, never represented as checked. That part mirrors the M0/M6/M8/M9
"code-complete, CI-leg pending first push" framing
(`docs/agents/task-status.md` milestone headers). **Story 8's gap is a
different class and must not be read as attestation debt.**

Per the M10 DoD, when all rows are green **on Windows CI**, the MVP is
code-complete and the one-month dogfood gate (`docs/vision.md` §9 Phase 2)
begins. Two things stand between here and that gate, and they are different
kinds of thing:

1. **One implementation gap — T10.2c** (story 8's trigger wiring). This is
   code that must be written; no amount of CI will turn it green. Until it
   lands, the MVP is code-complete *except* this task.
2. **The external attestations** — the Windows-CI leg awaits the first push
   to a runner, the literal 24h soak awaits its first scheduled nightly
   trigger, and story 1's ≤3s timing awaits a manual run. These are
   attestations this document cannot itself produce from this environment,
   and it does not claim to.

The local-Linux legs of every row **except story 8's supertask half** are
green today (2026-07-19).
