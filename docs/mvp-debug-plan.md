# akasha — MVP Debug Build Plan

**Purpose:** `docs/build-plan.md` sequences the *original* MVP construction
(milestones M0–M11) into per-file tasks. This file is a separate, ongoing
log for bugs and robustness gaps found *after* that construction — during
hardening (M9), dogfood (M11), live Windows testing, or CI runs — that
warrant their own scoped fix but do not belong in the milestone sequence
(they have no `Depends on` chain to an earlier milestone; they're findings
against already-DONE code). **This file is actively constructed**: append a
new entry whenever such a finding surfaces, whether or not it's fixed
immediately. It is not a fixed, closed list.

Entries use ID prefix `D` (Debug), never `T` or `M`, so they can never
collide with a `docs/build-plan.md` task ID. `docs/agents/task-status.md`
tracks `T*`/`M*` status only — this file's own `Status` field is
authoritative for `D*` entries; there is no separate status tracker.

## How to use this file

1. Same non-negotiable rules as `docs/build-plan.md`'s "How to use this
   plan" apply: narrowest-reading judgment calls over invention, no golden
   file edits, all persistent writes through `kernel/store.py`, `pickle`/
   `eval`/`exec` forbidden, product name never on-disk, full gate before
   `DONE`.
2. **One entry = one focused change.** Touch only the files listed under
   `Files`. If a fix legitimately needs an unlisted file, that's a signal
   to stop and reconsider scope, same as `docs/build-plan.md` rule 2.
3. An entry is not `DONE` until its `Verify` command passes locally.
4. Entries may reference the `T*` task whose investigation surfaced them
   (provenance), but are tracked and closed independently.

### Per-entry template

- **Goal** — the single outcome.
- **Found via** — what surfaced it (task, run, live test).
- **Files** — the only files the fix may create or edit.
- **Steps** — the ordered actions.
- **Verify** — the exact command(s) to run.
- **DoD** — the machine-checkable pass condition.
- **Status** — `TODO` / `IN PROGRESS` / `DONE` / `BLOCKED: <reason>`.

---

## D1 — `daemon.py`'s `JsonLineFormatter` silently drops exception tracebacks

- **Goal** — Every `logger.exception(...)` call against the `"akasha"`
  logger must have its traceback recoverable from the JSON-line log, not
  just the bare message.
- **Found via** — T9.8's investigation into the `nightly-soak` RSS breach:
  `soak.py`'s own `logger.exception(...)` calls (on an unhandled action
  exception, or a failed `/v1/metrics` sample) log via `configure_logging`
  (`daemon.py`), whose `JsonLineFormatter.format` builds its payload from
  `record.getMessage()` only — it never calls `record.exc_info` /
  `self.formatException(...)`, so a real traceback is silently discarded.
  Confirmed live: a probe monkeypatching `logging.Logger.exception` to also
  print `traceback.format_exc()` surfaced a real `ctypes.ArgumentError` in
  `metrics._sample_rss_bytes_windows` (see D2) that the JSON log itself
  showed only as `"unhandled exception sampling /v1/metrics"` — no file,
  line, or exception type. This directly degrades M9's own "zero unhandled
  exceptions" DoD: a real nightly regression would log a message and
  nothing else, forcing a `--log-failed` CI re-run (or worse, an
  unreproducible local one) just to see what actually broke.
- **Files** — `src/akasha/daemon.py` (`JsonLineFormatter.format`),
  `tests/unit/test_logging.py` (add exception-path coverage).
- **Steps** — (1) In `JsonLineFormatter.format`, when `record.exc_info` is
  truthy, add a `"traceback"` key to the JSON payload via
  `self.formatException(record.exc_info)` (stdlib `logging.Formatter`'s own
  helper — no new formatting logic). (2) Leave the payload shape unchanged
  when `record.exc_info` is falsy (no `"traceback"` key at all, not an
  empty string) — every existing non-exception log line/test stays
  byte-identical. (3) Add a unit test: call `logger.exception("boom")`
  inside an `except` block, assert the JSON line has a `"traceback"` field
  containing the exception type name and at least one frame.
- **Verify** — `uv run pytest tests/unit/test_logging.py`
- **DoD** — the new test passes; existing `test_logging.py` cases
  (non-exception log lines) still pass unchanged; `make check` green.
- **Status** — DONE 2026-07-28. `JsonLineFormatter.format` now adds a
  `"traceback"` key (via `self.formatException(record.exc_info)`) only
  when `record.exc_info` is truthy — non-exception log lines are
  byte-for-byte unchanged (asserted directly:
  `test_log_record_is_json_with_required_keys` now also asserts
  `"traceback" not in payload`). New
  `test_log_record_includes_traceback_on_exception` covers the
  `logger.exception(...)` path, asserting both the exception type/message
  and `"Traceback (most recent call last)"` appear in the field. Full gate
  (`ruff check src tests`, `pyright src`, `pytest tests/unit
  tests/property`) green.

## D2 — `metrics._sample_rss_bytes_windows` is not safe under concurrent calls

- **Goal** — `GET /v1/metrics`'s Windows RSS sampler must not corrupt or
  crash on a genuine concurrent invocation (two threads calling it at once).
- **Found via** — T9.8's investigation, indirectly: a diagnostic probe that
  called `_sample_rss_bytes()` reentrantly from inside a `gc.callbacks`
  hook (itself only possible because the real function can fire mid-way
  through code that triggers garbage collection) hit
  `ctypes.ArgumentError: argument 2: TypeError: expected
  LP__ProcessMemoryCounters instance instead of pointer to
  _ProcessMemoryCounters`. Root cause: `_sample_rss_bytes_windows` defines
  a fresh `ctypes.Structure` subclass (`_ProcessMemoryCounters`) and
  reassigns the *shared, module-level* `psapi.GetProcessMemoryInfo.argtypes`
  / `.restype` on every single call, rather than doing that setup once.
  `kernel32`/`psapi` are the same cached `ctypes.WinDLL` objects across
  calls, so two calls in flight at once (the second overwriting `argtypes`
  mid-call of the first) race. This specific probe-induced reentrancy isn't
  itself a production path, but the underlying pattern is real: `GET
  /v1/metrics` is a synchronous FastAPI endpoint, dispatched through
  uvicorn's request threadpool — a real daemon under genuine concurrent
  load (e.g. the dashboard polling `/v1/metrics` while another client also
  reads it) can call `_sample_rss_bytes_windows` from two threads at once.
- **Files** — `src/akasha/metrics.py`
  (`_ProcessMemoryCounters`/`_sample_rss_bytes_windows`),
  `tests/unit/test_metrics.py` (add concurrency coverage).
- **Steps** — (1) Hoist `_ProcessMemoryCounters`'s class definition and the
  `kernel32`/`psapi` `argtypes`/`restype` assignments out of
  `_sample_rss_bytes_windows` to module scope (or a `functools.lru_cache`d
  one-time setup helper), so they're computed once at import time / first
  use, never mutated per call. (2) Keep the per-call work limited to what
  must vary per call: constructing a fresh `_ProcessMemoryCounters()`
  instance and calling `GetProcessMemoryInfo` — the instance itself is
  call-local and safe to allocate every time, only the shared `ctypes`
  function metadata needs to stop being reassigned. (3) Add a test that
  calls `_sample_rss_bytes()` concurrently from N threads (e.g.
  `concurrent.futures.ThreadPoolExecutor`) and asserts every call returns a
  positive int with no exception — reproduces the race under the fix's
  absence, passes under its presence. (4) This is Windows-specific code
  (`_sample_rss_bytes_windows`, `pragma: no cover` off-Windows) — the new
  test should skip cleanly on non-Windows hosts, matching this module's
  existing platform-guard convention.
- **Verify** — `uv run pytest tests/unit/test_metrics.py`
- **DoD** — the new concurrency test passes on a real Windows host; no
  regression in existing RSS/CPU sampling tests; `make check` green.
- **Status** — DONE 2026-07-28. Hoisted the `_ProcessMemoryCounters`
  structure definition and the `kernel32`/`psapi` `argtypes`/`restype`
  setup out of `_sample_rss_bytes_windows` into a new
  `functools.lru_cache(maxsize=1)`-wrapped `_windows_memory_api()` helper —
  the DLL bindings are now computed once per process and never mutated
  again; `_sample_rss_bytes_windows` only reads the cached tuple and
  allocates a fresh, call-local `_ProcessMemoryCounters()` instance per
  call, exactly the split D2's Steps (1)/(2) called for. New
  `test_sample_rss_bytes_windows_is_safe_under_concurrent_calls` (skipped
  cleanly off-Windows, matching the module's existing platform-guard
  convention) hammers `_sample_rss_bytes_windows()` from a 16-worker thread
  pool (200 calls) and asserts every call returns a positive int — passed
  on a real Windows host, no `ctypes.ArgumentError`. Full gate (`ruff check
  src tests`, `pyright src`, `pytest tests/unit tests/property` — 415
  passed) green.

## D3 — `nightly-soak`'s scheduled 24h run cannot complete on a GitHub-hosted runner

- **Goal** — The real `schedule`-triggered `nightly-soak` CI job must be
  able to actually finish (pass or fail on its own merits), not be killed
  mid-run by a platform ceiling unrelated to the soak's own DoD.
- **Found via** — T9.8's investigation. `.github/workflows/ci.yml`'s
  `nightly-soak` job runs `uv run python tests/battery/soak.py --hours
  ${{ github.event_name == 'schedule' && '24' || inputs.soak_hours }}` on
  `runs-on: windows-latest`, a GitHub-hosted runner. GitHub-hosted runner
  jobs hard-cap at **6 hours of execution time** regardless of any
  `timeout-minutes` setting (confirmed against GitHub's own Actions-limits
  documentation: "Each job in a workflow can run for up to 6 hours of
  execution time. If a job reaches this limit, the job is terminated and
  fails.") — `timeout-minutes` can only lower that ceiling, never raise it.
  A literal 24h job on this runner class cannot complete. This was never
  previously exposed because both real scheduled runs so far
  (`30194717387`, `30256037982`) died from the RSS-budget breach (T9.8,
  now fixed) at ~4.5h — well before ever reaching the 6h platform ceiling.
  Note `T9.5`'s own original build-plan **Steps** text already
  contemplated this: "(1) Drive realistic edit traffic over 24 h (**or a
  scaled proxy in CI with a full run nightly on `main`**)" — `ci.yml`'s
  literal `'24'` on the `schedule` branch took the stricter of the two
  sanctioned readings, which is what now collides with the runner cap.
- **Files** — `.github/workflows/ci.yml` (`nightly-soak` job's `--hours`
  value on the `schedule` branch only — the `workflow_dispatch` branch's
  `inputs.soak_hours` default, used for fast manual verification, is
  unaffected and stays as-is).
- **Steps** — (1) Change the `schedule`-branch duration from the literal
  `'24'` to a value comfortably under the 6h platform ceiling with real
  margin for CI runner variance/setup overhead — narrowest-reading
  judgment call (not a SPEC-QUESTION, following the exact precedent
  `soak.py`'s own `IDLE_CPU_THRESHOLD_PCT_DEFAULT` comment sets for
  "spec/build-plan names no exact number, so pick one with a documented
  rationale"): T9.5's own Steps text explicitly sanctions "a scaled proxy
  in CI ... nightly on `main`" as an alternative to the literal 24h, so
  this is completing that already-permitted reading, not weakening a firm
  requirement. Suggested starting point: `5` hours (18000s), leaving ~1h of
  margin under the 6h cap for runner provisioning/checkout/`uv sync`
  overhead. (2) Update the comment block above the `nightly-soak` job (and
  `docs/build-plan.md`'s T9.5 entry, if its historical text asserts the
  literal 24h ran/passed in CI) to state the real configured duration and
  why. (3) Do not touch the `workflow_dispatch` `soak_hours` input or its
  default. (4) After landing, trigger one real `workflow_dispatch` run
  (not `schedule`) to confirm the job completes end-to-end within the
  chosen duration before waiting on the next real nightly `schedule` firing
  to confirm it.
- **Verify** — the YAML parses (`yamllint` or a quick `python -c "import
  yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"` if no dedicated
  linter is wired); a `gh workflow run` manual dispatch of `nightly-soak`
  completes (any conclusion, pass or fail on RSS/CPU/exceptions) without
  being killed by the platform's own 6h timeout.
- **DoD** — the `schedule`-triggered `nightly-soak` job's configured
  duration is provably under 6h with documented margin; the next real
  scheduled run completes (rather than being killed mid-flight); `make
  check` green (no `src`/`tests` changes expected, CI-config-only).
- **Status** — TODO.
