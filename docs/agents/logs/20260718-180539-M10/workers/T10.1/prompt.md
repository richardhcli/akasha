You are a fleet-worker executing build-plan task T10.1 (Metrics dashboard view). This task is ~90% already done by a prior overnight run — your job is to FINISH it, not start over.

Goal: UI view for facet coverage, inflow vs resolution + variance, violation rate, crossing rate.
Depends on: T9.2, T8.1 (both DONE).
Files you may create or edit: src/akasha/ui/templates/dashboard.html, src/akasha/ui/static/app.js, src/akasha/api/app.py, tests/integration/test_ui_dashboard.py, docs/spec-questions.md.
Spec reference: M10 (dashboard), spec §7 metrics, §9 story 6.
Steps: (1) Read GET /v1/metrics. (2) Render facet coverage, review inflow vs resolved with variance, violation rate, crossing rate. (3) No new metric definitions — display only.
Verify command: uv run pytest tests/integration/test_ui_dashboard.py
Definition of done: dashboard shows all four metric groups sourced from /v1/metrics, reachable on a real running daemon (not just a test-local route).

## Current state (already on disk, untracked/modified — do not redo this part)

- `src/akasha/ui/templates/dashboard.html` (new, untracked) — static shell, nav includes a Dashboard link to `/dashboard`, four sections: `dashboard-facet-coverage`, `dashboard-review-economy`, `dashboard-violation-rate`, `dashboard-crossing-rate`.
- `src/akasha/ui/static/app.js` (modified) — adds `initDashboardView` + `renderFacetCoverage/ReviewEconomy/ViolationRate/CrossingRate`, fetches `GET /v1/metrics`, renders via `createElement`/`textContent` (never `innerHTML`), wired into `boot()`'s pathname routing. This part is believed complete and correct — read it to confirm, but do not rewrite it unless you find an actual bug.
- `tests/integration/test_ui_dashboard.py` (new, untracked) — a real Playwright test that drives headless Chromium (confirmed working in this sandbox) against a live daemon, seeds real state, and asserts all four metric sections render with the correct values. It currently works around a missing production route by registering a **test-local** `GET /dashboard` route directly on the `FastAPI` app instance inside the test file (see `_register_test_dashboard_route`), instead of hitting a real route in `app.py`.

## The one real gap, and your explicit authorization to close it

`src/akasha/api/app.py` has no `GET /dashboard` route, so `/dashboard` does not resolve on a real running daemon — only in the test's workaround. `docs/spec-questions.md` has an open entry titled "T10.1 — `dashboard.html` has no production route" describing this exact gap and its own two options: (a) add the one-line route to `app.py` (a Files-list completion identical in kind to the precedent already set by T8.1–T8.4 and T9.2, each of which added its own `app.py` view/route as a sanctioned completion, not a stop-and-log scope gap), or (b) confirm the stricter stop-and-log was itself overly conservative.

**You are explicitly authorized and directed to take option (a).** Do NOT stop-and-log this as an out-of-scope-file gap — a prior overnight run already did that and it is why this task stalled. Concretely:

1. In `src/akasha/api/app.py`, add a `GET /dashboard` route that mirrors the existing `/sync` view route byte-for-byte in pattern: `@app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)`, serving `(_TEMPLATES_DIR / "dashboard.html").read_bytes()` as `HTMLResponse(content=content, status_code=200)`. Follow the exact style/comment conventions of the four existing view routes in that file (`/`, `/node`, `/review`, `/search`, `/sync`). `include_in_schema=False` means this must NOT require any OpenAPI snapshot regeneration — confirm `tests/integration/test_openapi_snapshot.py` still passes untouched after your change.
2. Simplify `tests/integration/test_ui_dashboard.py`: remove `_register_test_dashboard_route` and its call site entirely, and let `page.goto(f"{base_url}/dashboard")` hit the real production route now that it exists. Update/remove the module's `SPEC-QUESTION` docstring block since the gap it describes is now resolved (a short note that it was resolved via option (a) is fine, or remove the block — your call, keep it accurate).
3. Update the `docs/spec-questions.md` T10.1 entry's "Resolution" line from "open" to resolved, stating option (a) was taken and citing the T8.1-T8.4/T9.2 precedent it follows.
4. Run the Verify command yourself via Bash and report its REAL exit code and output tail — do not estimate or guess these values. If it fails, diagnose and fix per the retry logic in your persona instructions (never weaken the test).
5. Also sanity-check `uv run pytest tests/integration/test_openapi_snapshot.py` still passes (should be unaffected since the new route is schema-excluded) — report this too, though your primary Verify command above is what matters for DONE/BLOCKED.

## Non-negotiable rules (root CLAUDE.md)

Never invent schema/endpoints/grammar beyond the spec (narrowest reading + `# SPEC-QUESTION:` comment on genuine ambiguity — this particular ambiguity is already resolved per your authorization above, so this shouldn't recur here). Never edit golden files/fixtures to make tests pass. All persistent writes go through `src/akasha/kernel/store.py` (not touched by this task). No pickle/eval/exec anywhere. Touch only the Files listed above.

If you have not reached a terminal status (DONE or BLOCKED) within roughly 20 tool calls, stop immediately and report status BLOCKED with blocked_reason "possible hang — exceeded tool-call budget". Do not continue indefinitely.

Return your result via the required structured schema. files_changed must be the actual output of `git diff --name-only` plus untracked files you created (check with `git status --porcelain`), not a guess. End your reply with a fenced ```json block containing exactly: status, files_changed, verify_command, verify_exit_code, verify_stdout_tail, spec_questions (array, empty if none), blocked_reason (only if BLOCKED).