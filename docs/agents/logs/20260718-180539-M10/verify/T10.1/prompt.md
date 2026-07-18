You are an independent verifier for build-plan task T10.1 (Metrics dashboard view). You did NOT do the work. Your job is to catch a worker that claims success without having actually done it — do not trust anything below except as a claim to check.

The worker claims:
- status=DONE
- files_changed=["docs/spec-questions.md", "src/akasha/api/app.py", "src/akasha/ui/static/app.js", "src/akasha/ui/templates/dashboard.html", "tests/integration/test_ui_dashboard.py"]
- verify_exit_code=0
- verify_stdout_tail: "tests/integration/test_ui_dashboard.py::test_dashboard_renders_all_four_metric_groups[chromium] PASSED [100%]\n\n============================== 1 passed in 1.09s ==============================="
- The worker also claims it added a GET /dashboard route to src/akasha/api/app.py mirroring the existing /node, /review, /search, /sync routes (include_in_schema=False), removed a test-local route workaround from tests/integration/test_ui_dashboard.py so the test now hits the real production route, and updated docs/spec-questions.md's T10.1 entry from "open" to resolved.
- The worker also claims tests/integration/test_openapi_snapshot.py still passes (3 passed) unaffected by the new route.

Verify command to re-run yourself: uv run pytest tests/integration/test_ui_dashboard.py

Steps:
1. Run the verify command yourself via Bash. Record the REAL exit code and output tail.
2. For every path in files_changed, check it exists on disk and is non-empty.
3. Run `git status --porcelain` and `git diff --name-only` and confirm they are consistent with the claimed files_changed list.
4. Read `src/akasha/api/app.py` and confirm there is a genuine `GET /dashboard` route (not a stub, not commented out) that follows the same `include_in_schema=False` / HTMLResponse pattern as the other four view routes in that file, and actually serves `dashboard.html`.
5. Read `tests/integration/test_ui_dashboard.py` and confirm it no longer registers a test-local `/dashboard` route (grep for `_register_test_dashboard_route` — it should be gone) and that the test drives the real app route via `page.goto(...)`.
6. Also run `uv run pytest tests/integration/test_openapi_snapshot.py` yourself and confirm it genuinely still passes (this checks the new route didn't leak into the OpenAPI snapshot, since include_in_schema=False should exclude it).
7. Read `docs/spec-questions.md`'s T10.1 entry and confirm the Resolution line was actually changed away from "open".
8. Set verdict:
   - CONFIRMED_DONE only if the worker claimed DONE, your own verify run exits 0, every claimed file exists and is non-empty, the /dashboard route is real (not a workaround), and the openapi snapshot test still passes.
   - CONTRADICTS_CLAIM if the worker claimed DONE but any of the above checks fail (e.g. route missing, test still uses the old workaround, snapshot test broken).
   - CONFIRMED_BLOCKED if the worker claimed BLOCKED (not applicable here since it claimed DONE, but follow this if you discover it actually is blocked).

If you have not reached a terminal verdict within roughly 20 tool calls, stop and report notes explaining the hang instead of continuing indefinitely.

End your reply with a fenced ```json block containing exactly these fields: files_exist (array of {path, exists, nonempty}), verify_exit_code, verify_stdout_tail, git_status_matches_claim (boolean), verdict, notes.