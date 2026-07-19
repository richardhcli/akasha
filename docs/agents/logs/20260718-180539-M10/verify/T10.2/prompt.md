You are an independent verifier for build-plan task T10.2 (Export command, `akasha export --md DIR`). You did NOT do the work. Your job is to catch a worker that claims success without having actually done it — do not trust anything below except as a claim to check.

## Background so you can judge correctness, not just presence

T10.2 was re-specified today by a fable-model spec ruling (read `docs/spec-questions.md`'s two "## T10.2" entries and `docs/build-plan.md`'s current T10.2 entry for the authoritative design). Settled decisions: (1) transport is a new read-only `GET /sync/export` endpoint on `src/akasha/api/routes/sync.py` (not a direct-store CLI read, not a stop-and-log), and the OpenAPI snapshot MUST be regenerated since this is a real schema-visible `/v1` endpoint (unlike T10.1's UI-shell route); (2) scope is "sync-projection-tracked nodes" (every path in `store.list_sync_files` with a base snapshot), with a separate `unfiled_node_count` for live nodes not in any projection — never silently included or dropped.

## The worker's claim

- status=DONE
- files_changed=["docs/api-snapshot/openapi.json", "src/akasha/api/routes/sync.py", "src/akasha/cli/main.py", "src/akasha/kernel/store.py", "src/akasha/sync/reconcile.py", "tests/integration/test_api.py", "tests/integration/test_export.py"]
- verify_exit_code=0, verify_stdout_tail shows "12 passed"
- Added `GET /v1/sync/export` returning `{"items": [{sync_root, relative_path, text}, ...], "unfiled_node_count": N}` ordered by (sync_root name, POSIX relative path), using `require_auth` (any token class).
- Added a `read_only: bool = False` keyword param to `hub_state_for` in `sync/reconcile.py` that suppresses its `store.enqueue_review` side effect when True, with existing call sites unaffected (default False).
- Added `store.list_live_node_ids(conn) -> set[str]` (read-only) used to compute `unfiled_node_count`.
- Added `export --md DIR` to `cli/main.py`: pure HTTP client, writes each item's `text` via `dest.write_bytes(text.encode("utf-8"))` (no newline translation), prints a `cli/v1` summary of `files_written` + `unfiled_node_count`.
- New `tests/integration/test_export.py` (9 tests) including a control test proving `read_only` isn't dead code (an unprojectable-body scenario enqueues a review when `hub_state_for` is called WITHOUT `read_only=True`, but not through the export path). Added 5 endpoint tests to `tests/integration/test_api.py`.
- OpenAPI snapshot regenerated via `uv run python -m tests.integration.test_openapi_snapshot`, claimed additive-only diff.
- Worker also claims full gate green: ruff, pyright, tests/unit, tests/property, full tests/integration, tests/battery all passing.

Verify command to re-run yourself: `uv run pytest tests/integration/test_export.py tests/integration/test_openapi_snapshot.py`

## Steps

1. Run the verify command yourself via Bash. Record the REAL exit code and output tail.
2. For every path in files_changed, check it exists, is non-empty, and is genuinely modified/created (check `git status --porcelain` / `git diff --name-only` — confirm no unclaimed changes and no claimed-but-absent files).
3. Read `src/akasha/api/routes/sync.py`'s new `GET /sync/export` route in full. Confirm: it is read-only (no `store.enqueue_review`, no other write call anywhere in its body or anything it calls that isn't explicitly read-only); it actually calls `hub_state_for(..., read_only=True)` (not the default); response shape genuinely matches `{items: [{sync_root, relative_path, text}], unfiled_node_count}` ordered as claimed; it skips `list_sync_files` rows with no base snapshot.
4. Read `hub_state_for` in `src/akasha/sync/reconcile.py`. Confirm the new `read_only` param genuinely gates the `store.enqueue_review` call (not a no-op flag), and that the two pre-existing call sites in `Reconciler.on_change` were NOT changed to pass `read_only=True` (they must keep enqueueing reviews for real sync cycles — only the export path should suppress it).
5. Read `tests/integration/test_export.py` in full. Confirm it isn't vacuous: it must actually seed a sync root + managed file, call the real endpoint (or CLI against a live daemon), and assert real byte-for-byte content matches the canonical render — not just "no crash". Confirm the control test genuinely proves the read_only suppression works (asserts review-queue state differs between a read_only=True call path and a direct read_only=False call, or equivalent).
6. Confirm re-export byte-stability is actually tested (export twice, or export then diff against the canonical render directly) — not merely asserted in prose.
7. Confirm `unfiled_node_count` is tested with a real unfiled node (a node created but never linked to any sync file) — not just asserted as always 0.
8. Run `uv run python -m tests.integration.test_openapi_snapshot` yourself (or re-run `tests/integration/test_openapi_snapshot.py`) and confirm the snapshot genuinely reflects the new endpoint (grep `docs/api-snapshot/openapi.json` for `/v1/sync/export` — it must be present) and the diff from HEAD~1 is additive-only (no unrelated endpoint changes).
9. Run the full gate yourself: `uv run ruff check src tests && uv run pyright src && uv run pytest tests/unit tests/property tests/integration -q` and separately `uv run pytest tests/battery -q`. Record real results — do not trust the worker's claim that these are green.
10. Set verdict:
    - CONFIRMED_DONE only if the worker claimed DONE, your own verify run exits 0, every claimed file exists/is genuinely changed, the read-only guarantee is real (not just claimed), the tests are non-vacuous, and the full gate (ruff/pyright/unit/property/integration/battery) is genuinely green.
    - CONTRADICTS_CLAIM if the worker claimed DONE but any of the above checks fail (e.g. the endpoint isn't actually read-only, a test is vacuous, the snapshot wasn't really regenerated, the full gate fails).
    - CONFIRMED_BLOCKED if the worker actually claimed BLOCKED (not applicable here — it claimed DONE).

If you have not reached a terminal verdict within roughly 25 tool calls, stop and report notes explaining why instead of continuing indefinitely.

End your reply with a fenced ```json block containing exactly these fields: files_exist (array of {path, exists, nonempty}), verify_exit_code, verify_stdout_tail, git_status_matches_claim (boolean), verdict, notes.