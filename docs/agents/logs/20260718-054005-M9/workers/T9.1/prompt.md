Repo root: /home/richardhcli/projects/personal-projects/akasha. Run id: 20260718-054005-M9. Task id: T9.1.

You are a fleet-worker (Tier-2, Sonnet) per your persona in `.claude/agents/fleet-worker.md` (read it first) and the root `CLAUDE.md` (read it too — its "Non-negotiable rules" section is binding on you). This is one task in a larger overnight fleet run against `docs/build-plan.md` / `docs/agents/task-status.md` — you own exactly this one task.

## Task block (verbatim from docs/build-plan.md, verify it yourself before starting)

### T9.1 — Windows battery items (CRLF, locking retry, AV noise)
- Goal: Handle Windows file-locking retries and AV-induced transient errors; confirm CRLF handling end-to-end.
- Depends on: T5.8 (DONE).
- Files: src/akasha/sync/watcher.py, src/akasha/sync/reconcile.py, tests/battery/test_windows.py
- Spec: M9 (CRLF, locking retry, AV noise), §6.2 E09 in docs/mvp-spec.md — read it yourself.
- Steps: (1) Retry-with-backoff on Windows sharing-violation/locked-file errors. (2) Tolerate transient AV-held handles. (3) Confirm CRLF files canonicalize with no spurious diff (E09).
- Verify: uv run pytest tests/battery/test_windows.py
- DoD: locked-file writes retry and succeed; CRLF arrival produces no spurious diff.

## Known nuance (from the orchestrator's scan — verify, don't just trust)

This machine is Linux, but the Verify command's own doc comment says "(Windows CI leg)". You cannot literally exercise `msvcrt`/Windows sharing-violation errno paths on this host. Follow the exact precedent already set in this codebase for this situation: T4.9's Windows single-instance-lock code (`src/akasha/daemon.py`) is guarded by `if sys.platform != "win32": raise AssertionError(...)` guards specifically so pyright's platform-aware reachability analysis skips it, and is "code-reviewed + pyright-clean but not runtime-exercised on this Linux host" — that is an ACCEPTED, already-used pattern in this repo, not a gap you need to invent a new solution for. For T9.1: implement the retry-with-backoff logic in a way that is platform-guarded where it must call Windows-only APIs, but structure the retry LOGIC itself (the backoff loop, the exception classification, the CRLF canonicalization check) so it is unit-testable on Linux by simulating/mocking the specific transient-error condition (e.g. inject a fake exception that mimics `PermissionError`/`OSError` with a Windows sharing-violation-like errno, or a swappable retry-predicate function) rather than requiring an actual Windows filesystem. `tests/battery/test_windows.py` should genuinely exercise your retry logic (not be vacuous), even though it runs on Linux — real coverage of the retry/backoff mechanism and the CRLF canonicalization path, via mocks/fakes for the OS-specific trigger only. If you find the existing `watcher.py`/`reconcile.py` write paths don't have an obvious single retry point, use your own engineering judgment on where to add it (e.g. wrapping the file-write calls that already exist, per §4.8's write-back / base_store.put path) and document the choice inline; if it's genuinely ambiguous whether this belongs in watcher.py (read-side, filesystem-event handling) or reconcile.py (write-back), pick the narrowest reading based on where the actual OS-level file write happens, and note it.

## Non-negotiable rules (from CLAUDE.md — binding)

1. Never invent schema, endpoints, ID formats, or grammar beyond docs/mvp-spec.md. If ambiguous, implement the narrowest reading, add a `# SPEC-QUESTION:` comment at the site, and include a formatted entry in your `spec_questions` return field.
2. Never edit golden files, fixtures, or acceptance tests (tests/golden/**) to make an implementation pass.
3. Every mutation of persistent state goes through src/akasha/kernel/store.py; no other module writes SQLite directly.
4. pickle, eval, exec are forbidden everywhere.
5. Touch ONLY the files listed above under Files. If you find you truly need an unlisted file, stop and log a SPEC-QUESTION instead of guessing (same rule 0.8 precedent as many prior tasks touching store.py — but this task's Files list does NOT include store.py, so you should NOT need it; if you think you do, that's a strong signal to stop and ask, not touch it).
6. The task is not DONE until Verify passes locally. Never weaken the test or move on if it fails after your retry budget.

## Hang guard

If you have not reached a terminal status (DONE or BLOCKED) within roughly 20 tool calls, stop immediately and return status BLOCKED with blocked_reason "possible hang — exceeded tool-call budget".

## Return Value

End your reply with a single fenced ```json block with exactly these fields (per your persona's Return Value section): status ("DONE" or "BLOCKED"), files_changed (array, from git status/diff — never a guess), verify_command, verify_exit_code, verify_stdout_tail, spec_questions (array, empty if none), blocked_reason (required iff BLOCKED), and cursor_task_json/cursor_response_json (strings, only if you delegated to Cursor via cursor_bridge.py). This must be the literal final thing in your reply.
