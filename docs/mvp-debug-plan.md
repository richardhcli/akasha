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
- **Status** — DONE 2026-07-28. Changed `nightly-soak`'s `--hours` on the
  `schedule` branch from the literal `'24'` to `'5'` (18000s), leaving ~1h
  margin under the 6h GitHub-hosted-runner execution cap — the
  `workflow_dispatch` branch's `inputs.soak_hours` (default `"0.05"`) is
  untouched, matching Steps (3). Updated the `schedule`-trigger comment,
  the `workflow_dispatch.inputs.soak_hours` description, and the
  `nightly-soak` job's own comment block (all in `ci.yml`) to state the
  real 5h duration and cite D3 as the reason, rather than the old
  literal-24h/build-plan-Verify framing. `docs/build-plan.md`'s T9.5 entry
  was left untouched — its text doesn't assert the 24h ever ran/passed in
  CI (that's a forward-looking Verify spec, not a historical claim), so
  the Step (2) parenthetical condition doesn't apply, and T9.5 isn't in
  this entry's `Files` list. `uv run --with pyyaml python -c "import
  yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"` parses clean
  (no project `pyyaml` dependency exists, so used an ephemeral `uv run
  --with`). `gh workflow view "CI"` independently confirms the exact
  failure mode this fixes: real run `30343169643` (the first `schedule`
  run after T9.8's RSS fix landed) shows `cancelled` at `6h0m14s` — killed
  by the platform ceiling, not a soak failure on its own merits. Full gate
  (`ruff check src tests`, `pyright src`, `pytest tests/unit
  tests/property` — 415 passed) green, as expected for a CI-config-only
  change touching no `src`/`tests` files. Step (4)'s real
  `workflow_dispatch` verification run (and the subsequent real `schedule`
  firing) were intentionally not triggered as part of this fix — a 5h
  CI run has real cost/runner-time and shared-visibility implications, so
  it's left for the user to trigger explicitly.

## D4 — daemon sends no CORS headers; the Obsidian plugin's every fetch is blocked

- **Goal** — A request from the Obsidian plugin's renderer origin
  (`app://obsidian.md`) to the daemon's `/v1/*` API must not be blocked by
  the browser's CORS preflight check, so `plugin-obsidian/src/statusbar.ts`
  (and any future plugin API call) can actually reach the daemon.
- **Found via** — First real Obsidian-vault dogfood run against a live
  daemon (this session, 2026-07-30): plugin installed, enabled, Daemon URL
  + a freshly minted human bearer token entered correctly in Settings →
  TM Hub (confirmed against `GET /v1/sync/status` succeeding from a plain
  HTTP client with the same token/URL at the same moment) — status bar
  stayed on `TM: offline` indefinitely instead of `TM: synced · 0
  violations`. Obsidian's own DevTools console (`Ctrl+Shift+I`) showed the
  real cause on every poll tick:
  `Access to fetch at 'http://127.0.0.1:7433/v1/sync/status' from origin
  'app://obsidian.md' has been blocked by CORS policy: Response to
  preflight request doesn't pass access control check: No
  'Access-Control-Allow-Origin' header is present on the requested
  resource.` followed by `net::ERR_FAILED`. `statusbar.ts`'s `apiFetch`
  catches this as a generic network failure and (correctly, given no other
  signal is available) reports `TM: offline` — so the plugin's own offline
  handling isn't the bug; the daemon never answers the browser's OPTIONS
  preflight with the headers Chromium/Electron's `fetch` requires. Grepped
  the full tree for `CORS`/`Access-Control-Allow-Origin` and
  `CORSMiddleware`/`add_middleware` in `src/`: zero matches anywhere —
  `src/akasha/api/app.py`'s `create_app` never registers
  `fastapi.middleware.cors.CORSMiddleware` (or sets the header any other
  way), so this isn't a regression, it's a gap that was never exercised
  end-to-end before (T6.2–T6.5's `plugin-obsidian/TESTPLAN.md` is a manual
  plan that, per its own file, had never actually been run against a real
  vault until now). This blocks **all** plugin→daemon traffic, not just the
  status bar poll — `create-node-from-selection` and any future plugin API
  call would hit the identical preflight failure, since Electron's
  renderer processes enforce CORS like a normal browser origin.
- **Files** — `src/akasha/api/app.py` (register `CORSMiddleware`),
  `tests/unit/test_app.py` or a new `tests/unit/test_cors.py` (assert the
  preflight/response headers), `plugin-obsidian/TESTPLAN.md` (note this was
  the real blocker T6.2/T6.3 would have hit, once it's fixed).
- **Steps (proposed, not yet taken — narrowest reading needs a decision
  logged as its own spec-question first)** — (1) Add `docs/spec-questions.md`
  entry: spec §4.11/§3 document the daemon binding to `127.0.0.1` only and
  the API surface, but say nothing about CORS/allowed origins for a
  browser-embedded client like the Obsidian plugin (`app://obsidian.md`) or
  the shipped web UI's own pages (same-origin, unaffected) — narrowest
  reading is to allow exactly `app://obsidian.md` (the plugin's fixed
  Electron origin) plus whatever origin(s) the settings UI documents for
  local dev, not a wildcard `*`, since this daemon also carries bearer
  tokens and wildcard-plus-credentials is both spec'd-nowhere and a real
  weakening of the localhost-only posture spec §3 establishes. (2) Register
  `CORSMiddleware` in `create_app` with that narrow allow-list, `POST`
  requests carrying `Authorization` + `Content-Type` headers permitted, and
  no wildcard. (3) Unit test: a `TestClient` `OPTIONS` preflight against
  `/v1/sync/status` with an `Origin: app://obsidian.md` header gets back
  `Access-Control-Allow-Origin: app://obsidian.md`; an arbitrary untrusted
  `Origin` does not.
- **Verify** — `uv run pytest tests/unit/test_cors.py` (or wherever Steps
  (3) lands); manually re-open the dogfood fixture vault
  (`docs/dogfood/fixtures/vault-1`) in Obsidian with TM Hub configured and
  confirm the status bar reaches `TM: synced · N violations` within one
  ~5s poll tick, with zero CORS errors in DevTools console.
- **DoD** — `plugin-obsidian/TESTPLAN.md`'s T6.2/T6.3 steps (settings
  persistence, live status bar) pass against a real daemon with zero CORS
  console errors; `make check` green.
- **Status** — DONE 2026-07-31. `docs/spec-questions.md`'s D4 entry logged
  the origin-allow-list judgment call first (exactly `app://obsidian.md`,
  no wildcard); the user then explicitly directed fixing D4, which is what
  resolves that spec-question (the "human" the entry asked to adjudicate
  it). Registered `fastapi.middleware.cors.CORSMiddleware` in `create_app`
  (`src/akasha/api/app.py`) with a module-level `_CORS_ALLOWED_ORIGINS =
  ["app://obsidian.md"]` constant, `allow_credentials=False` (auth is a
  bearer token in the `Authorization` header, never a cookie, so no
  credentialed-CORS mode is needed), `allow_methods=["GET", "POST",
  "PATCH", "DELETE"]`, `allow_headers=["Authorization", "Content-Type"]`.
  New `tests/integration/test_cors.py` (not `tests/unit/` — corrected
  against this repo's actual convention of putting `TestClient`-driven
  `create_app` tests under `tests/integration/`, e.g. `test_health.py`,
  once checked; the original Files list's `tests/unit/test_app.py` guess
  predated that check): asserts a preflight from the Obsidian origin gets
  `Access-Control-Allow-Origin: app://obsidian.md` back, an actual `GET
  /health` from that origin carries the same header, an untrusted origin
  (`https://evil.example`) gets no `Access-Control-Allow-Origin` header at
  all (starlette's `CORSMiddleware` still answers 200 to the preflight
  itself — the browser is what blocks the real request client-side on the
  missing header), and a guard test that fails loudly if
  `_CORS_ALLOWED_ORIGINS` is ever loosened to `*`. Full gate: `uv run ruff
  check src tests` (clean), `uv run pyright src` (0 errors), `uv run pytest
  tests/integration/test_cors.py -v` (4 passed), `uv run pytest tests/unit
  tests/property` (415 passed, no regressions), and a targeted rerun of
  every existing UI/health/app/openapi-snapshot integration test (89
  passed) to confirm adding global CORS middleware didn't change any
  existing response shape/headers test depends on. `plugin-obsidian/
  TESTPLAN.md` was not edited — no reference to the CORS blocker existed
  there worth amending (its steps already describe the correct manual
  flow; they simply couldn't succeed until now). Live re-verification
  against a real Obsidian vault (not just the unit-level TestClient
  checks) is this session's immediate next step. **Update:** done, same
  session — restarted the daemon (code changes need a fresh process),
  confirmed `curl`-equivalent `Access-Control-Allow-Origin: app://obsidian.md`
  on `/health`, then re-entered a valid token in a real running Obsidian
  instance's TM Hub settings (the prior token had gone stale from an
  unrelated fresh-DB restart earlier in the session) and watched the status
  bar reach `TM: synced · 0 violations` live, with zero CORS errors in
  DevTools console.

## D5 — Web UI has no in-page way to ever set the bearer token

- **Goal** — A user opening any UI view for the first time must have some
  in-page affordance to authenticate, not just the existing "Set tm_token
  in localStorage to use this view." notice with no accompanying way to act
  on it.
- **Found via** — Same live dogfood session as D4. After fixing D4, setting
  up the Obsidian plugin still required manually opening browser DevTools
  and calling `localStorage.setItem('tm_token', ...)` by hand to test the
  web viewer at all — grepping `src/akasha/ui/static/app.js` confirmed
  every one of the five views' `init*View()` functions calls `getToken()`
  and, if empty, renders the notice and returns; nothing anywhere ever
  writes to `localStorage`. `tests/integration/test_ui_smoke.py` and its
  siblings all pre-seed the token via Playwright's
  `page.context.add_init_script(...)` — confirming this was the *test
  harness's* substitute for a real affordance, not evidence one existed.
- **Files** — `src/akasha/ui/static/app.js` (`initAuthBar`/`renderAuthBar`/
  `renderAuthForm`/`maskToken`, wired into `boot()`), all six
  `src/akasha/ui/templates/*.html` (add `<div id="tm-auth-bar"></div>` next
  to `<nav>`), `tests/integration/test_ui_shell.py` /
  `test_ui_node.py` / `test_ui_review.py` / `test_ui_search_sync.py` /
  `test_ui_dashboard.py` (assert the container renders), new
  `tests/integration/test_ui_auth_bar.py` (behavior).
- **Steps taken** — Narrowest reading (SPEC-QUESTION logged in
  `docs/spec-questions.md` D5, resolved the same way T8.3's inline
  revise-textarea was: spec §4.13 names four views + Dashboard with no
  fifth "settings" affordance, so implement the smallest thing that closes
  the gap rather than block on a spec amendment). (1) One shared,
  always-visible `#tm-auth-bar` container added to every template next to
  `<nav>` — not a separate settings page, so it's impossible to land on any
  view with no way to act. (2) `app.js`: no token → inline
  `<input type="password">` + "Save token" button, writing to the *same*
  `localStorage.tm_token` key every existing `getToken()` call already
  reads (no new storage mechanism), then `window.location.reload()` so the
  already-initialized view picks it up the same way a manual DevTools call
  always did. (3) Token set → masked display (`abcd…wxyz`, never the raw
  value) + "Change token" (re-shows the form) + "Clear token"
  (`removeItem` + reload, a lightweight logout). (4) No cookies, no new
  endpoint, no schema change — purely a client-side affordance around
  existing behavior.
- **Verify** — `uv run pytest tests/integration/test_ui_auth_bar.py
  tests/integration/test_ui_shell.py tests/integration/test_ui_node.py
  tests/integration/test_ui_review.py tests/integration/test_ui_search_sync.py
  tests/integration/test_ui_dashboard.py -v`
- **DoD** — A fresh browser profile with zero prior `localStorage` state,
  given only the daemon URL, can authenticate and use every view purely
  through in-page UI — no DevTools, no console, ever; `make check` green.
- **Status** — DONE 2026-07-31. `uv run ruff check src tests` clean;
  `uv run pytest tests/integration/test_ui_auth_bar.py
  tests/integration/test_ui_shell.py tests/integration/test_ui_node.py
  tests/integration/test_ui_review.py tests/integration/test_ui_search_sync.py
  tests/integration/test_ui_dashboard.py -v` — 15 passed. First pass of
  `test_clear_token_button_logs_out` failed for a test-authoring reason, not
  a product bug: it seeded the token via Playwright's
  `page.context.add_init_script`, which re-runs on every navigation
  *including the reload* Clear triggers, silently re-seeding the very token
  the test was checking got cleared. Fixed by seeding through the UI's own
  Save flow instead (the same path a real user takes), matching how
  `test_saving_token_through_the_ui_actually_authenticates` already does
  it — no `app.js` change needed, only the test. Full regression: `uv run
  pytest tests/integration -q` (210 passed) and `uv run pytest tests/unit
  tests/property -q` (415 passed), confirming the six new template edits
  and the shared auth-bar code didn't disturb any existing view/route.

## D6 — `/search?q=` deep links do nothing until the user retypes and resubmits

- **Goal** — Navigating directly to a URL like `/search?q=term` (bookmarked,
  shared, or typed by hand) must run that search on load, not silently show
  an empty form.
- **Found via** — Same dogfood session. Browsing `/search?q=weather`
  directly (Chrome extension navigation) rendered the bare form with an
  empty input and empty results list; only after manually retyping the same
  query into the box and clicking Search did results appear. Reading
  `initSearchView` in `app.js` confirmed why: the `submit` handler is the
  *only* code path that ever calls `/v1/search` — nothing reads
  `window.location.search` on load.
- **Files** — `src/akasha/ui/static/app.js` (`initSearchView`), new
  `tests/integration/test_ui_search_deep_link.py`.
- **Steps taken** — Extracted the submit handler's body into a local
  `runSearch(q)` closure (no behavior change for the form-submit path), then
  added a load-time check: `new URLSearchParams(window.location.search).get
  ("q")` — if present, hydrate `#search-input`'s value and call
  `runSearch()` immediately. No `?q=` present → unchanged (empty form, no
  auto-search of an empty string).
- **Verify** — `uv run pytest tests/integration/test_ui_search_deep_link.py -v`
- **DoD** — `/search?q=<term>` on load shows the same results a manual
  submit of the same term would; `/search` with no query param is
  byte-for-byte unchanged behavior; `make check` green.
- **Status** — DONE 2026-07-31. `uv run pytest
  tests/integration/test_ui_search_deep_link.py -v` — 2 passed (query-param
  hydration + auto-run; no-param path unchanged). Covered by the same full
  regression run as D5 (`tests/integration` 210 passed, `tests/unit
  tests/property` 415 passed).

## D7 — the live filesystem watcher tracks non-`.md` files (e.g. Obsidian's own `workspace.json`) as managed contract files

- **Goal** — The daemon must only ever reconcile `*.md` files under a sync
  root, matching the convention every other entry point into `on_change`
  already enforces, so an editor's own non-contract housekeeping files
  (Obsidian's `.obsidian/` app-state, or any other non-markdown file that
  happens to live under a vault directory) never get parsed as contract
  text or permanently added to `sync_files`.
- **Found via** — A holistic post-D4/D5/D6 dogfood pass against a freshly
  restarted daemon (this session, 2026-07-31): `GET /v1/sync/status` (both
  via the Sync UI view and directly) showed `"files": [...]` with 2 entries
  for a sync root registered against `docs/dogfood/fixtures/vault-1`, which
  contains exactly one real content file (`note1.md`). The second entry was
  `...\vault-1\.obsidian\workspace.json` — Obsidian's own generated
  app-state file (window layout, active pane, etc.), rewritten on nearly
  every UI interaction while the vault is open, which it had been earlier
  in this same session. Traced the two entry points into
  `Reconciler.on_change`: `discover_untracked_files` (T11.3) walks
  `Path(root_path).rglob("*.md")` and `reconcile_all` only replays rows
  already in `sync_files` (themselves only ever seeded via that same
  `*.md`-scoped discovery, or a prior watcher event) — both already
  `*.md`-scoped. `sync/watcher.py`'s `_WatchdogEventHandler.on_any_event`
  was the one exception: it forwards every raw `watchdog` event under the
  recursively-observed `root_path` straight to `notify_event` with only a
  directory check and a reconcile-temp-file regex filter, no extension
  check at all. `on_change` itself has no defense either — it unconditionally
  `Path(path).read_text(...)`s and `parse()`s whatever it's handed; a JSON
  file happens to parse as an empty, harmless `BlockSet` (no `ANCHOR_RE`
  matches), so this never crashed or raised a violation — it just silently
  and permanently added a non-contract file to `sync_files`, polluting the
  Sync view's `files: N` count and its `root.files` list with an
  Obsidian-internal file the daemon has no business managing, and burning a
  full read+parse+`hub_state_for`+write-back-diff reconcile cycle on every
  one of Obsidian's own saves (a real, ongoing cost for as long as that
  vault stays open in Obsidian, not a one-time blip).
- **Files** — `src/akasha/sync/watcher.py` (`_is_managed_candidate`,
  `_WatchdogEventHandler.on_any_event`), `tests/unit/sync/test_watcher.py`
  (add coverage).
- **Steps taken** — Added a small pure predicate,
  `_is_managed_candidate(path) -> bool`, checking
  `PurePath(path).suffix.lower() == ".md"` (case-insensitive: this
  project's primary dogfood platform is Windows, per `docs/dogfood/
  README.md`) — the exact same `.md` convention `discover_untracked_files`
  already applies via its `rglob("*.md")`, just enforced at the one place
  that previously had no filter. Wired it into `on_any_event` alongside the
  existing `_RECONCILE_TEMP_FILE_RE` check, for both `src_path` and
  `dest_path` (a rename *into* a non-`.md` name, e.g. `note.md` ->
  `note.md.bak`, is correctly dropped too — the destination is what would
  end up tracked). No change to `on_change`, `reconcile_all`,
  `discover_untracked_files`, or any DB schema — purely narrows what ever
  reaches the watcher's own `notify_event` call.
- **Verify** — `uv run pytest tests/unit/sync/test_watcher.py -v`
- **DoD** — A raw `watchdog` event for a non-`.md` path (including one
  disguised via a rename's `dest_path`) never reaches `notify_event`; the
  existing directory-skip and temp-file-skip behavior is unchanged; `make
  check` green.
- **Status** — DONE 2026-07-31. New
  `test_watchdog_event_handler_ignores_non_md_paths` (alongside the
  existing `test_watchdog_event_handler_routes_src_and_dest_but_skips_dirs`)
  asserts a `.obsidian/workspace.json` src-only event is dropped, a
  case-insensitive `note.MD` src event is forwarded, and an `a.md` -> `.../
  data.json` src+dest event forwards only the managed `src_path`. Full
  gate: `uv run ruff check src tests` (clean), `uv run pyright src` (0
  errors), `uv run pytest tests/unit/sync/test_watcher.py -v` (22 passed,
  up from 21), `uv run pytest tests/unit tests/property -q` (416 passed, up
  from 415 — no regressions). Live re-verification: restarted the fixture
  daemon fresh, re-registered the sync root, confirmed `GET
  /v1/sync/status` showed exactly one file (`note1.md`) with no
  `.obsidian/*` entries even after Obsidian was reopened against the same
  vault and its `workspace.json` changed again.

## D8 — search results and review/sync items show a node's id as plain text, with no link to its node view

- **Goal** — Anywhere the web UI already displays a node id (a search hit,
  a review-queue item, a sync-status violation/conflict/pause), that id
  must be a real link to `/node?id=<id>`, not plain text the user has to
  manually copy into the URL bar.
- **Found via** — The same holistic dogfood pass that found D7 (this
  session, 2026-07-31), after D5's auth bar made it possible to actually
  use every view end-to-end for the first time. The natural next action —
  clicking a search result or a review item to see the full node — had no
  affordance: `renderSearchResults` rendered `"id: " + node.id + " (" +
  node.node_type + ")"` as inert text, `renderReviewItem` rendered
  `"node_id: " + review.node_id` the same way, and `renderReviewSummary`
  (shared by the Sync view's violations/pauses/conflicts/unresolved lists)
  didn't surface `node_id` at all despite `GET /v1/sync/status` already
  returning it on every item. Confirmed live in Chrome: `/search?q=weather`
  showed two matching claims with their ids spelled out in the results
  text, `/review` showed a real `E_UNKNOWN_ANCHOR` violation's `node_id`
  the same way — neither was clickable.
- **Files** — `src/akasha/ui/static/app.js` (`nodeLink`,
  `renderSearchResults`, `renderReviewItem`, `renderReviewSummary`), new
  `tests/integration/test_ui_node_links.py`.
- **Steps taken** — Added one small helper, `nodeLink(nodeId, text)`,
  returning a plain `document.createElement("a")` with `href = "/node?id="
  + encodeURIComponent(nodeId)` and `textContent = text` (never
  `innerHTML`, matching this module's existing XSS discipline for
  server-derived free text). Wired it into the three render functions that
  already had a node id in hand: `renderSearchResults` (the `"id: ..."`
  line), `renderReviewItem` (the `"node_id: ..."` line, only when
  `review.node_id` is truthy — proposal items creating a *new* node
  legitimately have `node_id: null` and correctly keep the plain
  `"node_id: (none)"` text), and `renderReviewSummary` (added a `node_id:
  <link>` segment before the existing `path:`/`cause_kind:`/`created_at:`
  text, again only when the item carries one). No new data, no new
  endpoint, no schema change — every value linked was already present in
  the existing `/v1/search`, `/v1/review`, and `/v1/sync/status` responses.
- **Verify** — `uv run pytest tests/integration/test_ui_node_links.py -v`
- **DoD** — A search result's id and a review item's (non-null) `node_id`
  are both real links landing on that node's `/node?id=<id>` page showing
  the correct body; the existing `node_id: (none)` proposal-item text is
  unchanged; `make check` green.
- **Status** — DONE 2026-07-31. New
  `tests/integration/test_ui_node_links.py`: `test_search_result_links_to_node_view`
  creates a node via a human token, searches for it, asserts the result's
  link has `href="/node?id=<id>"`, clicks it, and asserts the node view
  shows the right body; `test_review_item_links_to_node_view` creates a
  node, then has an agent-class token PATCH it (proposal-rewritten per spec
  §4.11 into a review item with a real `node_id`), asserts the review
  item's link has the same `href` shape, clicks it, and asserts the node
  view shows the *original* (pre-proposal) body. Full gate: `uv run ruff
  check src tests` (clean), `uv run pyright src` (0 errors), `uv run pytest
  tests/integration/test_ui_node_links.py -v` (2 passed), `uv run pytest
  tests/integration -q` (212 passed, up from 210 — no regressions), `uv run
  pytest tests/unit tests/property -q` (416 passed, unaffected — this is a
  UI-only change).

## D9 — five of six UI views' nav bars have no link to `/dashboard`

- **Goal** — Every one of the six views (`/`, `/node`, `/review`, `/search`,
  `/sync`, `/dashboard`) must let a user reach every other view from its nav
  bar; a live user must never have to type `/dashboard` into the address bar
  by hand because it's the only route unreachable from navigation.
- **Found via** — A holistic dogfood UI sweep (this session, 2026-07-31,
  post-D5/D6/D7/D8), driving a real browser against a live daemon. Static
  review of `src/akasha/api/app.py`'s six `ui_*` route handlers confirmed
  each serves its own standalone `src/akasha/ui/templates/*.html` file
  directly (`_TEMPLATES_DIR / "<name>.html").read_bytes()`) — there is no
  shared Jinja `{% extends %}` base, so the `<nav>` block is physically
  copy-pasted six times. `dashboard.html` (added by T10.1) has a correct
  5-link nav including `<a href="/dashboard">Dashboard</a>`, but the other
  five templates (`base.html`, `node.html`, `review.html`, `search.html`,
  `sync.html` — all predating T10.1) were never updated when the dashboard
  route was added, so each still has only the original 4 links. Confirmed
  live in a real Chrome tab against a running daemon: the nav on `/`,
  `/node`, `/review`, `/search`, and `/sync` reads exactly "Node Review
  Search Sync" with no Dashboard entry, while `/dashboard`'s own nav reads
  "Node Review Search Sync Dashboard". No existing test caught this —
  `tests/integration/test_ui_dashboard.py::test_dashboard_route_serves_shell`
  only asserts the dashboard page's own containers/scripts, and no test
  anywhere asserted nav-link parity across views. Checked
  `docs/spec-questions.md`/`docs/archived-questions.md` first: D5's entry
  there covers the *auth-bar* affordance being added to all six templates,
  not nav-link parity — this is a distinct, previously unlogged gap.
- **Files** — `src/akasha/ui/templates/base.html`,
  `src/akasha/ui/templates/node.html`,
  `src/akasha/ui/templates/review.html`,
  `src/akasha/ui/templates/search.html`,
  `src/akasha/ui/templates/sync.html`, new
  `tests/integration/test_ui_nav.py`.
- **Steps taken** — Added the one missing `<a href="/dashboard">Dashboard</a>`
  line to each of the five templates' `<nav>` block, in the same position
  `dashboard.html` already uses (immediately after the Sync link) — no
  markup restructuring, no move to a shared template/`{% extends %}`
  (that's a larger refactor than this narrow gap needs, and would touch
  every view's rendering path at once instead of a one-line-per-file fix).
  New `tests/integration/test_ui_nav.py` hits all six routes via
  `TestClient` and asserts every response body contains all five nav
  `<a href=...>` links, so any future view addition that forgets to update
  the other five templates' nav (the same mistake T10.1 made) fails CI
  immediately instead of waiting for another live dogfood pass to notice.
- **Verify** — `uv run pytest tests/integration/test_ui_nav.py -v`
- **DoD** — Every one of the six views' rendered `<nav>` contains links to
  all five other views (Node/Review/Search/Sync/Dashboard as applicable);
  `make check` green.
- **Status** — DONE 2026-07-31. `uv run ruff check src tests` clean; `uv run
  pyright src` (0 errors); `uv run pytest tests/integration/test_ui_nav.py
  -v` (1 passed). Full regression: `uv run pytest tests/unit tests/property
  -q` (415 passed, 1 skipped — unaffected, this is a UI-only change) and
  `uv run pytest tests/integration -q -k "not chromium"` (202 passed, 10
  deselected — the `[chromium]` Playwright-driven tests, including
  `test_ui_dashboard.py`'s own live-browser nav render, could not be run in
  this environment; see note below). One unrelated failure was observed in
  the same run, `test_watcher_wiring.py::
  test_live_edit_is_reconciled_with_no_manual_rescan` — untouched by this
  entry's own fix (no file in this entry's `Files` list is anywhere near
  `src/akasha/sync`). **Correction, logged the same day**: this was
  originally guessed here to be a sandbox FUSE-mount/`inotify` artifact and
  deliberately not filed. That guess was wrong — a direct repro (bypassing
  pytest, with `on_any_event`/`notify_event` traced) showed the *real*
  cause: a genuine, unbounded self-triggering event loop, unrelated to
  FUSE. See debug-plan D10, which fixes it.
  **Environment note**: this session's sandbox cannot launch headless
  Chromium at all (`chrome-headless-shell: error while loading shared
  libraries: libXdamage.so.1` — no root/sudo available to install the
  missing X11 deps `playwright install --with-deps` would normally add),
  so none of the `[chromium]`-parametrized Playwright integration tests
  could be run here to independently re-verify this fix; the live-browser
  confirmation above (via Claude-in-Chrome against a daemon running
  directly on the user's own machine, not this sandbox) is what actually
  exercised the rendered DOM for this entry.

## D10 — the live watcher's own echo-suppression read starts an unbounded self-triggering event loop, so a real edit is never reconciled

- **Goal** — `Watcher` must fire `on_cycle` for a real on-disk edit within
  one debounce window of it going quiet, exactly as
  `tests/integration/test_watcher_wiring.py::
  test_live_edit_is_reconciled_with_no_manual_rescan` (T9.6's own
  acceptance test) asserts — not hang indefinitely.
- **Found via** — Investigating why that exact test was failing (initially
  misdiagnosed in D9 as a sandbox FUSE/`inotify` artifact and not filed —
  see D9's correction note). Direct repro outside pytest, with
  `_WatchdogEventHandler.on_any_event` and `Watcher.notify_event` traced:
  writing one file produced a normal `notify_event` call, but then kept
  producing an endless stream of alternating `FileOpenedEvent`/
  `FileClosedNoWriteEvent` for the *same* path forever, each one re-entering
  `notify_event` and re-arming the debounce window before it could ever
  elapse — `on_cycle` (`Reconciler.on_change`) was never called, even after
  5 seconds, confirmed by adding a trace wrapper around it that never fired.
  Root cause: `notify_event`'s echo-suppression step calls
  `content_hash_fn(path)` (`daemon.py::_watcher_content_hash`, a plain
  `Path.read_text`) to hash the file's current content — on this platform's
  `watchdog` inotify backend, that *read* itself raises its own
  `"opened"`/`"closed_no_write"` events, which the same recursively-
  scheduled observer picks straight back up, re-entering `on_any_event` ->
  `notify_event` -> another read -> another event, with no bound. This is
  the exact echo-suppression wiring `daemon.serve()` uses in production
  (`watcher = Watcher(..., origin_tracker=watch_origin,
  content_hash_fn=_watcher_content_hash, ...)`), not a test-only
  configuration, so a real running daemon on any platform whose file-system
  watch backend reports read (open/close-without-write) events would hit
  this too — a genuine, previously-undetected regression in T9.6's own
  "watcher actually reconciles a live edit" guarantee, and a standing CPU
  cost (`_poll_loop`'s backstop keeps the process alive, but the debounce
  window for that path effectively never closes while the loop runs).
  Separately confirms a real gap in this repo's own gating: `tests/
  integration` (where this acceptance test lives) is not part of either
  `make check` or `make battery` (see `Makefile`) and is not otherwise
  referenced as a required gate anywhere in `CLAUDE.md`/`docs/build-plan.md`
  — nothing was ever failing loudly. Not filing a new D-entry for that
  gate gap itself (out of this entry's narrow scope), but flagging it here
  since it is why a T9.6-acceptance-test-level regression went unnoticed.
- **Files** — `src/akasha/sync/watcher.py` (`_WatchdogEventHandler
  .on_any_event`, new `_NON_CONTENT_EVENT_TYPES`),
  `tests/unit/sync/test_watcher.py` (add coverage).
- **Steps taken** — Added `_NON_CONTENT_EVENT_TYPES = frozenset({"opened",
  "closed", "closed_no_write"})` and one early-return in `on_any_event`
  (right after the existing `is_directory` check): if
  `getattr(event, "event_type", None)` is one of those three, return
  without ever calling `notify_event`. Compared as plain strings rather
  than `watchdog.events.EVENT_TYPE_*` constants to keep this module's own
  stated "no import-time dependency on watchdog beyond `Watcher.start`"
  design goal — these three values are watchdog's own stable public event
  ­type surface, not an internal detail. No change to `_is_managed_candidate`,
  `_RECONCILE_TEMP_FILE_RE`, `notify_event`, the `Debouncer`, or
  `content_hash_fn` itself — the fix is purely "never forward a
  non-content event type past the watchdog boundary," symmetric with D7's
  "never forward a non-`.md` path past the watchdog boundary." A genuine
  edit still raises a `"modified"`/`"created"`/`"moved"` event alongside
  the harmless open/close pair every write also raises, so no real edit is
  ever suppressed by this filter.
- **Verify** — `uv run pytest tests/unit/sync/test_watcher.py -v &&
  uv run pytest tests/integration/test_watcher_wiring.py -v`
- **DoD** — A real on-disk edit under a live `Watcher` (with echo
  suppression wired, matching production) reconciles within one debounce
  window with no manual rescan; `"opened"`/`"closed"`/`"closed_no_write"`
  events never reach `notify_event`; every existing event-routing test
  (non-`.md` filtering, src/dest routing, directory skip) is unaffected;
  `make check` green.
- **Status** — DONE 2026-07-31. Direct repro (outside pytest, traced) before
  the fix: `on_cycle` never fired, "STILL never reconciled after 5s". Same
  repro after the fix: reconciled in 0.1s (one debounce window). `uv run
  ruff check src tests` clean; `uv run pyright src` (0 errors); `uv run
  pytest tests/unit/sync/test_watcher.py -v` (23 passed, up from 22 — new
  `test_watchdog_event_handler_ignores_non_content_events`); `uv run pytest
  tests/integration/test_watcher_wiring.py -v` (1 passed, was failing
  before this fix). Full regression: `uv run pytest tests/unit
  tests/property -q` (416 passed, 1 skipped — up from 415, the one new
  unit test); `uv run pytest tests/integration -q -k "not chromium"` (203
  passed, 10 deselected `[chromium]` tests not runnable in this
  environment — **zero failures now**, up from 202 passed / 1 failed
  before this fix).
