Repo root: /home/richardhcli/projects/personal-projects/akasha. Run id: 20260718-054005-M9. Task id: T9.1.

You are the independent fleet-verifier per your persona in `.claude/agents/fleet-verifier.md` (read it first). You did NOT do this work. Treat the worker's claim below as an assertion to check, not a fact.

## Task's exact Verify command
uv run pytest tests/battery/test_windows.py

## Worker's claimed result (verify independently, do not trust)
```json
{
 "status": "DONE",
 "files_changed": ["src/akasha/sync/reconcile.py", "src/akasha/sync/watcher.py", "tests/battery/test_windows.py"],
 "verify_command": "uv run pytest tests/battery/test_windows.py",
 "verify_exit_code": 0,
 "verify_stdout_tail": "20 passed in 0.19s",
 "spec_questions": []
}
```

The worker's own summary (for your context, not to be trusted blindly): added `is_transient_lock_error` + `retry_with_backoff` in `watcher.py` (classifies OSError.winerror in {5,32,33}, generic exponential backoff), wrapped `Debouncer.poll`'s re-queue behavior on a transient error surviving retry, and wrapped the two OS-level calls in `reconcile.py` (`on_change`'s vault-file read, `write_if_diff`'s existence-read + `os.replace`) in the retry helper. New `tests/battery/test_windows.py` has 20 tests simulating locked-file/transient errors via a settable `OSError.winerror` attribute (since this host is Linux) plus an E09 CRLF confirmation test.

## Your job

1. Run `uv run pytest tests/battery/test_windows.py` yourself via Bash. Record the real exit code and tail.
2. Confirm all 3 claimed files exist and are non-empty (`tests/battery/test_windows.py` is new).
3. Read the actual diff for `src/akasha/sync/watcher.py` and `src/akasha/sync/reconcile.py` (`git diff -- src/akasha/sync/watcher.py src/akasha/sync/reconcile.py`) and confirm the changes are real retry/backoff logic wired into genuine OS-level I/O call sites (not vacuous — e.g. a retry loop that's never actually invoked by any real file-write path), and confirm the 20 tests in the new file genuinely exercise that logic (not tautological/vacuous assertions) — spot check at least 3 of the test bodies.
4. Run `git status --porcelain` and confirm consistency (expect other files modified/untracked by concurrent sibling tasks T9.2/T9.3/T9.4 — not a discrepancy against THIS worker's claim, only check the 3 claimed files).
5. Also re-run the broader regression the worker claims still passes: `uv run pytest tests/unit/sync tests/property -q` (a scoped slice of "everything else didn't break" — do not run the full multi-minute battery/integration suite, this scoped slice is enough for your independent check) and note the result.
6. Set your verdict per your persona's rules: CONFIRMED_DONE / CONTRADICTS_CLAIM / CONFIRMED_BLOCKED.

End your reply with the single fenced ```json block your persona specifies (task_id, files_exist, verify_exit_code, verify_stdout_tail, git_status_matches_claim, verdict, notes).
