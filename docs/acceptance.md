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

> **Real Windows verification pass, 2026-07-24.** All prior legs in this
> document were run on Linux; the framing throughout ("pending first CI
> push") reflected that the GitHub Actions `windows-latest` runner had
> never actually executed this suite. This session ran `make check`
> (ruff/pyright/unit+property), `make battery`, the full integration suite
> (incl. Playwright/Chromium), and the accelerated soak proxy directly on a
> real Windows 11 host (not CI — a local dev-host run, still a materially
> stronger attestation than the prior simulated-only coverage). This
> surfaced and fixed **five real, previously-latent Windows bugs**, all
> in code paths explicitly flagged elsewhere in this repo as
> "code-reviewed but never runtime-exercised on Windows":
> 1. **No `.gitattributes`** — a plain Windows `core.autocrlf=true` checkout
>    (the common Windows git default, also the `windows-latest` runner's
>    default) silently CRLF-corrupted all 114 byte-exact golden fixtures
>    plus the OpenAPI snapshot, failing 29 golden tests for reasons
>    unrelated to any code defect. Fixed via a new root `.gitattributes`
>    (`tests/golden/** -text`, `docs/api-snapshot/openapi.json -text`).
> 2. **`metrics.py`'s Windows RSS sampling silently returned 0** on every
>    real process: the `ctypes` calls to `GetCurrentProcess`/
>    `GetProcessMemoryInfo` had no declared `argtypes`/`restype`, so ctypes'
>    default 32-bit-int marshaling truncated the 64-bit pseudo-handle and
>    the call failed with `ERROR_INVALID_HANDLE` (confirmed via a live
>    `GetLastError()` probe). Fixed by declaring the real Win32 signatures.
> 3. **`reconcile.py`'s `write_if_diff` corrupted every synced file's line
>    endings on Windows** — `Path.write_text(text, encoding="utf-8")`
>    applies the platform-default newline translation, rewriting the
>    already-canonical (spec §4.3, LF-only) text to CRLF on disk on every
>    write-back. This is a real product defect on the spec's own
>    release-gate platform, not a test artifact; it surfaced as 3 genuine
>    `tests/battery` failures (E08, E12, E16) with real files on disk
>    ending in `\r\n`. Fixed with `newline=""`.
> 4. **`daemon.py`'s Windows single-instance lock (`_acquire_windows`,
>    T4.9) raised a raw `PermissionError` instead of the typed
>    `AlreadyRunningError`** on a genuine second-acquisition attempt: the
>    file-content bootstrap read (`handle.read(1)`) sat *before* the
>    `try/except OSError` that only wrapped the `msvcrt.locking()` call,
>    and reading an already-locked byte range itself raises
>    `PermissionError` on Windows. Fixed by widening the `try:` to cover
>    the whole acquisition sequence.
> 5. **`pyright src` had 10 new errors on Windows** (zero on Linux) in the
>    POSIX `fcntl`-based lock path (`_acquire_posix`/`_release_posix`):
>    typeshed only declares `fcntl`'s POSIX-only members off
>    `sys.platform != "win32"`, so analyzing on a Windows host flips which
>    branch gets type-checked (the mirror image of the existing
>    `msvcrt`-branch guard already in place for Linux analysis). Fixed by
>    adding the mirror `sys.platform == "win32": raise AssertionError(...)`
>    guard.
>
> (A sixth, narrower issue — three property/perf tests leaving a sqlite3
> connection open when `tempfile.TemporaryDirectory()` tries to clean up,
> which POSIX tolerates and Windows doesn't — was also fixed; test-fixture
> hygiene, not a product bug.)
>
> **Result after all five fixes:** `ruff check` clean, `pyright src` 0
> errors, and **639 passed** across `tests/unit tests/property
> tests/integration tests/battery` on this real Windows host — the first
> time this repo's full suite has been proven green on Windows rather than
> Linux-only-plus-simulation. This is still a **local Windows dev-host
> run, not the GitHub Actions `windows-latest` CI runner** — see the
> updated row 7/9 notes below for what remains genuinely pending (the
> hosted-CI leg itself, the literal 24h soak, and real-OS
> autostart/kill-9/deployment).
>
> **Gap closed 2026-07-20 (T10.2c):** the open implementation gap noted below
> (added 2026-07-19 by a post-authoring audit) — spec §4.10's
> `all_subtasks_closed` trigger evaluation had zero production call sites —
> is now fixed. `store.commit_node` evaluates `all_subtasks_closed` for the
> committed node's parent supertask(s) inside the same commit transaction
> (mirroring how T7.2 wired `invalidate`), so closing the last open subtask
> through any `store.commit_node` caller (today: the sync/reconcile
> checkbox-toggle path; not yet `PATCH /nodes`, which has no `task_state`
> field to accept — a pre-existing, separate HTTP-surface gap) now flags the
> supertask for review. **Row 8 is GREEN**, not PARTIAL. See row 8 below for
> the re-run test evidence.

> **Gap closed 2026-07-20 (T9.2c):** `metrics.py`'s `violation_rate`,
> `auto_repairs{class}`, and `sync_cycle_ms{p50,p95}` (spec §7, part of row
> 6's dashboard-display sub-claim below) had a complete recorder API
> (`record_sync_cycle_ms`/`record_auto_repair`, already exercised by
> `tests/unit/test_metrics.py`) but zero production call sites, so all
> three read `0.0`/`{}` in a really-running daemon regardless of actual
> sync activity — a disclosure gap this file did not previously name (row
> 6 below cited only the aggregation/rendering tests; the gap was tracked
> in `metrics.py`'s own module docstring and `docs/spec-questions.md`'s T9.2
> entry, still open there pending archival once this task's landing is
> independently verified — never surfaced in this file). Now fixed:
> `sync/reconcile.py`'s `Reconciler.on_change` times every real sync cycle
> (`time.monotonic()`, `try`/`finally`, covering the quiet, hub-only,
> pause&diff, and normal-completion exit paths — the guard for an
> unregistered path is excluded, since it does zero reconciliation) and
> records each certain-repair (`E_LOST_ANCHOR`/`E_DUP_ID`, spec §4.7)
> actually applied silently — never the same repairs when a conservative
> (cloud-synced) root instead routes them to review (T5.4). See row 6
> below for the re-run test evidence and a real, freshly observed
> non-zero sample of all three metrics from a live `on_change` run.

> **Windows dogfood-readiness pass, 2026-07-25.** Four real gaps closed on
> this same real Windows 11 host used for the 2026-07-24 pass above:
>
> 1. **`test_openapi_snapshot.py`'s own regeneration path corrupted its
>    output to CRLF on Windows** (`_write_snapshot` used
>    `Path.write_text(..., encoding="utf-8")` with no `newline=""`) — the
>    same bug class as 2026-07-24's `reconcile.py` fix, just in the
>    snapshot tooling instead of the sync write-back path. Found because
>    T11.3's `sync_rescan` docstring change had never been accompanied by
>    a snapshot regen, so `tests/integration/test_openapi_snapshot.py`
>    failed for real on this host; fixed, then the snapshot was
>    regenerated for real (content-only diff, verified LF-only on disk).
>    Full gate re-confirmed green on Windows after the fix: `ruff` clean,
>    `pyright src` 0 errors, `tests/unit tests/property` 411 passed,
>    `tests/battery` 47 passed, `tests/integration` 187 passed (645
>    total). Commit `12ed9b9`, pushed to `origin/main` — the
>    `windows-latest` hosted CI leg (row 7) has not been independently
>    observed from this session (no `gh`/API access here); **still
>    pending a human checking the Actions run for that push.**
> 2. **T8.4's pause&diff inspector — code-verified only since M8, never
>    driven with real pause data on any platform — was exercised for
>    real** via `scripts/dogfood/init.sh` (new: see
>    `docs/dogfood/windows-service.md`): an 8-block scratch vault file was
>    synced clean, 4 of its blocks were bumped to S1+ maturity (a real
>    inbound `cites` edge each, over HTTP), then those 4 lines were
>    deleted from the vault file and rescanned. This produced a genuine
>    `E_DELETED_S1`-driven pause (4/8 = 0.5 > the 0.25 `PAUSE_THRESHOLD`)
>    with a real unified diff, correctly nested under its sync root's
>    `pauses` bucket (not `unresolved`) — confirming the `sync_files`/
>    `cause_ref` path-matching this depends on is sound. Screenshotted
>    live in a real Windows Chromium browser (Playwright MCP, since the
>    `claude-in-chrome` extension was not connected on this host) at
>    `/sync`, showing "Pause & diff inspector (1)" with the diff rendered
>    in the `<pre>`; resolved via `/review`'s `still_holds` button,
>    confirmed back to "No open reviews." The 4 S1+ nodes were confirmed
>    to survive (never silently deleted) throughout. `/node`, `/search`,
>    and `/dashboard` were also browser-driven in the same session and
>    render correctly (dashboard showed real non-zero live metrics:
>    `violation_rate: 0.5`, `inflow(7d): 1`, `resolved(7d): 1`).
> 3. **Real-OS residency (row 9)'s autostart/kill-9 leg partially closed**
>    on this local Windows dev-host — see the dedicated callout under row
>    9's discussion above for the full writeup, including the negative
>    result that Task Scheduler's native restart-on-failure does not
>    work and the supervisor-wrapper mechanism used instead.
> 4. New disposable lifecycle scripts, `scripts/dogfood/*.sh` and
>    `scripts/windows-service/*.ps1`, formalize the scratch-vault and
>    Windows-service setup/teardown used for (2) and (3) above so a future
>    dogfooder (human or agent) doesn't have to re-derive them by hand —
>    see `docs/dogfood/windows-service.md` for full usage and the
>    least-privilege elevation model.

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

**GREEN (local Linux)** for both sub-claims. Both were GREEN before
2026-07-20 on the strength of aggregation-math and rendering tests alone —
they did not attest that `violation_rate`, `auto_repairs{class}`, and
`sync_cycle_ms{p50,p95}` (three of the counters this same dashboard
displays) had a live production producer; see the "Gap closed 2026-07-20
(T9.2c)" note above this table. That gap is now closed:
`tests/unit/sync/test_reconcile.py` gained three tests
(`test_on_change_records_sync_cycle_ms_for_quiet_cycle`,
`test_on_change_records_auto_repair_for_silently_applied_certain_repair`,
`test_conservative_root_routing_does_not_record_auto_repair`) driving the
real `Reconciler.on_change` path, and `uv run pytest
tests/unit/test_metrics.py tests/integration/test_openapi_snapshot.py
tests/unit/sync/test_reconcile.py` — **70 passed**, 0 failed, 1.04s (run
2026-07-20). Independently confirmed with fresh, freshly observed
live values (not seeded directly into the recorder): three real
`Reconciler.on_change` cycles against a real in-memory store + real temp
files (one non-conservative certain-repair applied silently, one quiet
re-run of the same file, one threshold-triggered pause&diff violation
— checksum-invalid anchor, 1/1 blocks = 100% > the 25% pause threshold —
on a second, single-block file) produced `compute_metrics(conn)` == `violation_rate:
0.333...` (1 violation / 3 cycles), `auto_repairs: {'E_LOST_ANCHOR': 1}`,
`sync_cycle_ms: {'p50': 0.539, 'p95': 0.782}` (milliseconds) — all three
previously-zero counters now non-zero/non-empty under real traffic, run
2026-07-20. Full `make check` (**411 passed**) and `make battery`
(**47 passed**) re-confirmed unregressed the same run.

The "conversion moment" clause
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
included in the 47 above) but only exercised the *logic* on Linux via
simulated `winerror` injection — the real Windows-filesystem lock/retry
behavior these tests model was, until 2026-07-24, only attested that way.

**Update, 2026-07-24:** the full `make battery` (all 47) was re-run on a
real Windows 11 host — genuinely exercising the real filesystem, real
`msvcrt` locking, and a real CRLF write-back path, not simulation. This
found and fixed a genuine production bug (`reconcile.py`'s write-back was
silently corrupting LF to CRLF on every Windows write via
`Path.write_text`'s platform-default newline translation — 3 battery cases,
E08/E12/E16, caught it) and a real single-instance-lock bug (`daemon.py`'s
`_acquire_windows` raised an unwrapped `PermissionError` instead of
`AlreadyRunningError` on genuine second-acquisition; see the top-of-doc
callout for both). After both fixes: **47 passed** on real Windows. **GREEN
(local Windows dev-host)** for the filesystem-lock/retry/CRLF reality this
row cares about. What remains pending is narrower than before: the actual
GitHub Actions `windows-latest` **hosted CI runner** itself has still never
executed this suite (this was a local dev-host run, not CI) — that leg is
**pending first CI push**, per the M0/M6/M8/M9 "code-complete, CI-leg
pending first push" framing (see `docs/agents/task-status.md` milestone
headers).

### 8. Tasks + supertask + S0 lifecycle (`docs/vision.md` §8 story 8)

> "...closing the last open subtask fires the trigger and the supertask
> appears in the review queue flagged 'all subtasks complete'... a quick
> inline definition is born at S0... and can be promoted up the ladder or —
> if unlinked again — deleted freely."

Verifier: `tests/integration/test_tms.py::test_supertask_flag` (T7.4,
supertask-completion trigger, edited by T10.2c) +
`tests/integration/test_tms.py::test_supertask_flag_fires_via_real_commit_path_not_direct_evaluate`
(new, T10.2c — drives the scenario purely through `store.commit_node`, the
real production commit path, never calling `triggers.evaluate()` at all) +
battery E06 (delete-s0, S0 free-deletion lifecycle) + E08 (create-tm-new).

Re-run 2026-07-20 (T10.2c, on top of commit
`2564a98fe501326719e0c713a18db676b61566d2`), each command run standalone
and freshly observed:

- `uv run pytest tests/integration/test_tms.py -k supertask` — **2 passed**,
  0 failed, 0.46s (both `test_supertask_flag` and the new
  `test_supertask_flag_fires_via_real_commit_path_not_direct_evaluate`).
- `uv run pytest "tests/battery/test_edit_battery.py::test_reused_golden_case_passes_under_its_e_number[E06-delete-s0]" "tests/battery/test_edit_battery.py::test_reused_golden_case_passes_under_its_e_number[E08-create-tm-new]"`
  — **2 passed**, 0 failed, 0.18s.
- Full regression check re-run the same session: `uv run pytest
  tests/integration/test_tms.py` — **14 passed** (whole file, all T7.x rows
  plus both T10.2c tests); `uv run pytest tests/integration` — **176
  passed**; `make check` (ruff + pyright + unit/property) — **408 passed**,
  ruff clean, pyright 0 errors; `make battery` — **47 passed**. No
  regressions anywhere in the suite from the commit-path wiring.

**Status: GREEN (local Linux).** The S0 half of this story (inline
definition born at S0, freely deleted when unlinked) remains attested by
E06/E08 above. The supertask half is now attested end-to-end through the
real production path (T10.2c, 2026-07-20): `store.commit_node` evaluates
the `all_subtasks_closed` condition for the committed node's parent
supertask(s) inside the same commit transaction as the commit itself
(mirroring T7.2's `invalidate` wiring exactly — deferred import, and
`enqueue_review_within_transaction` rather than the standalone
`enqueue_review`, to avoid prematurely committing the in-flight
transaction). Closing the last open subtask through `store.commit_node` now
enqueues exactly one `subtasks_closed` review on the supertask, as a direct
side effect of that commit — no separate `evaluate()` call is needed, and
the new test asserts this without ever calling `evaluate()`. `commit_node`
is the shared choke point for every production task-state-changing caller,
so this reaches the sync/reconcile checkbox-toggle path today; it does
**not** yet reach `PATCH /nodes` specifically, because `PatchNodeBody`
(`api/routes/nodes.py`) does not accept a `task_state` field (adding one was
outside this task's Files list) — `PATCH /nodes` cannot close a task today
regardless of this wiring, so this is a pre-existing HTTP-surface gap, not a
T10.2c regression or limitation.
Idempotence holds (re-committing after all subtasks are already closed
enqueues no duplicate) and the supertask's own `task_state` is never
auto-closed by this path — both explicitly asserted. Scope was narrowed to
`all_subtasks_closed` only, per the T10.2c build-plan task: `facet_interface_changed`
was already live (T7.2), `evidence_retracted` is covered by T7.2b, and
`recheck_after` remains out of scope (no persisted schedule exists — a
separate, already-logged open question, not part of this row's gap).

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

**GREEN (local Linux, accelerated proxy)** for both, as of 2026-07-19.

**Update, 2026-07-24:** the accelerated soak proxy (`--hours 0.05`) was
re-run on a real Windows 11 host: `status: "passed"`, 90/90 ticks, 90/90
samples, `max_rss_mb: 62.38`, `mean_idle_cpu_pct: 0.018`,
`unhandled_exception_count: 0`. Notably, this is the *first* real Windows
RSS reading this project has ever produced — `metrics.py`'s Windows RSS
sampler (`ctypes` + `GetProcessMemoryInfo`) had a latent bug (undeclared
`argtypes`/`restype` truncating the 64-bit process handle, causing
`ERROR_INVALID_HANDLE` and a silent `0` return on every prior — necessarily
simulated or untested — invocation) that was found and fixed this session
(see the top-of-doc callout). **GREEN (local Windows dev-host, accelerated
proxy)**, now backed by genuine Windows resource sampling rather than a
code path that had never actually run.

Two legs of this story remain genuinely unattested from any box run so far
and are stated honestly as pending, not silently assumed:

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

> **Real-OS autostart/kill-9 leg, local Windows dev-host, 2026-07-25.**
> `scripts/windows-service/{init,deinit,destroy}.ps1` register the daemon
> as a real Windows Task Scheduler task (`AtLogOn` trigger) on this real
> Windows 11 host and were run end-to-end: registered, started (`GET
> /health` 200), then the daemon process was `taskkill /F`'d **twice in a
> row**. Both times a genuinely new process (confirmed via a new PID
> listening on the port) came back within ~2 seconds. This is a real
> local-machine attestation of autostart + crash recovery, **not** the
> hosted-CI or real-deployment leg the bullet above still names as
> pending — that distinction is deliberate, not an upgrade of this
> paragraph's scope.
>
> **Negative result worth recording:** Task Scheduler's own
> `RestartCount`/`RestartInterval` settings were tried first and do
> **not** reliably restart a force-killed long-running task process — a
> live test (register with `RestartCount=3`/`RestartInterval=1min`, kill
> the daemon, poll for 3.5 minutes) showed `LastTaskResult` flip to a
> failure code with no restart ever occurring. The recovery actually
> demonstrated above comes from a small supervisor-loop wrapper script
> these tools generate (Task Scheduler's job is reduced to autostart-at-
> logon only) — see `docs/dogfood/windows-service.md` for the mechanism,
> the full negative-result writeup, and the privilege model (these
> scripts request elevation per-operation, on demand, only if the host's
> policy actually denies the non-elevated call; they never require or
> hold a standing admin session).

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
| 7 | Contract sync | GREEN (47 passed, local Linux **and** local Windows as of 2026-07-24; full gate incl. integration re-confirmed green on Windows 2026-07-25 after the openapi-snapshot CRLF fix, commit `12ed9b9`) | hosted `windows-latest` CI runner itself — pushed 2026-07-25, run result **pending human confirmation** (no `gh`/API access from this session) |
| 8 | Tasks/supertask/S0 | **GREEN** — S0 lifecycle (E06/E08, 2 passed) + supertask trigger via the real commit path (2 passed), re-run 2026-07-20 (T10.2c) | none |
| 9 | Residency | GREEN, accelerated proxy (soak: 90/90 ticks, 0 exceptions, local Linux **and** local Windows as of 2026-07-24); real-OS autostart + kill-9 recovery demonstrated on local Windows dev-host 2026-07-25 (Task Scheduler + supervisor wrapper, 2/2 kills recovered in ~2s each — see callout above) | literal 24h duration — pending first scheduled nightly run; hosted-CI / real-deployment autostart-kill-9 attestation — pending first CI push / first deployment (local dev-host leg above is evidence toward this, not a substitute for it) |

**Eight of nine stories are fully GREEN on local Linux with no automated
gap** (2, 3, 4, 5, 6, 7, 8, 9 — story 8 as of the T10.2c re-run on
2026-07-20; 7 and 9 carrying only external Windows/24h legs). The one that
is not:

- **Story 1 — functional path green, ≤3s timing clause pending manual
  execution** (no automated timing test exists).

**Story 8's former gap is now closed (T10.2c, 2026-07-20):** the
supertask-trigger production path was unwired (spec §4.10's evaluation was
never invoked by `store.commit_node` or a daily tick) as of this document's
2026-07-19 authoring; `store.commit_node` now evaluates `all_subtasks_closed`
for the committed node's parent supertask(s) inside the same commit
transaction, so the trigger fires in a live daemon on the real commit path.
See row 8 above for the full re-run evidence (test names, commands, pass
counts).

Story 9's literal-duration/real-OS clauses and story 7's Windows-filesystem
clause are the genuinely *pending-attestation* legs — flagged here as
**pending**, never represented as checked. That part mirrors the M0/M6/M8/M9
"code-complete, CI-leg pending first push" framing
(`docs/agents/task-status.md` milestone headers).

Per the M10 DoD, when all rows are green **on Windows CI**, the MVP is
code-complete and the one-month dogfood gate (`docs/vision.md` §9 Phase 2)
begins. One thing stands between here and that gate now that T10.2c has
landed:

- **The external attestations** — the Windows-CI leg was pushed
  2026-07-25 (commit `12ed9b9`) but its run result has not been
  independently observed from this session and is not claimed as green;
  the literal 24h soak awaits its first scheduled nightly trigger; story
  1's ≤3s timing awaits a manual run; and the real-OS autostart/kill-9
  leg now has a local-Windows-dev-host demonstration (2026-07-25, see row
  9) but still awaits the hosted-CI/real-deployment leg specifically.
  These are attestations this document cannot itself produce from this
  environment, and it does not claim to.

The local-Linux legs of every row are green: rows 1–7 and 9 as of
2026-07-19 (this document's authoring session), row 8 as of 2026-07-20
(T10.2c's re-run, see above). Local-**Windows** dev-host evidence was
added 2026-07-24 (full-suite pass, 5 real bugs found/fixed) and extended
2026-07-25 (openapi-snapshot CRLF fix, pause&diff browser verification,
autostart/kill-9 recovery demonstration) — see the dated callouts above;
none of this substitutes for the hosted `windows-latest` CI leg itself,
which remains the literal M10 DoD gate.
