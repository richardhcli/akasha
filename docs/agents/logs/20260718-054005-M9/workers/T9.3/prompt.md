Repo root: /home/richardhcli/projects/personal-projects/akasha. Run id: 20260718-054005-M9. Task id: T9.3.

You are a fleet-worker (Tier-2, Sonnet) per your persona in `.claude/agents/fleet-worker.md` (read it first) and the root `CLAUDE.md` (read it too — its "Non-negotiable rules" section is binding on you). This is one task in a larger overnight fleet run against `docs/build-plan.md` / `docs/agents/task-status.md` — you own exactly this one task.

## Task block (verbatim from docs/build-plan.md, verify it yourself before starting)

### T9.3 — S0 GC scheduling + log rotation
- Goal: Schedule the S0 GC job and enable rotating logs.
- Depends on: T1.7, T0.6 (both DONE).
- Files: src/akasha/daemon.py, tests/integration/test_gc_schedule.py
- Spec: M9 (S0 GC scheduling, log rotation), §4.4/§4.5 (GC safety) — read docs/mvp-spec.md §4.4/§4.5 yourself.
- Steps: (1) Run GC on a schedule/daily tick. (2) Confirm rotating file handler rotates. (3) GC keeps referenced objects (reuse T1.7 invariant).
- Verify: uv run pytest tests/integration/test_gc_schedule.py
- DoD: scheduled GC runs and removes only orphans; logs rotate at the configured size.

## Known nuances (from the orchestrator's scan — verify, don't just trust)

1. **Reuse, don't reimplement**: T1.7 already built `store.gc_objects(conn)` (kernel/store.py) — call it, don't reinvent GC logic. T0.6 already built `configure_logging`/`JsonLineFormatter` in `daemon.py` — check its current signature (T4.9 already extended `daemon.py` once without touching T0.6's logging function signature; T5.6 also extended `daemon.py` to wire startup reconcile inside `single_instance_lock`, before `uvicorn.run`). Read the CURRENT `daemon.py` in full before editing — it already has real structure from T4.9/T5.6 (single-instance lock, startup reconcile, lazy imports for FastAPI/uvicorn) that you must not break or duplicate.
2. **"Rotating logs"**: `configure_logging` almost certainly currently uses a plain `logging.FileHandler` (or similar) — check, then switch to (or add) Python's stdlib `logging.handlers.RotatingFileHandler` (maxBytes + backupCount) or `TimedRotatingFileHandler`, whichever better matches "rotate at the configured size" in the DoD (this phrasing implies size-based rotation, i.e. `RotatingFileHandler`, not time-based — prefer that unless you find a spec passage saying otherwise). If a rotation size/backup-count isn't specified anywhere in the spec, pick a sane default (e.g. 10MB, 5 backups) and document the choice inline as a narrowest-reading judgment call (not a SPEC-QUESTION unless you think it's genuinely load-bearing).
3. **"Schedule/daily tick"**: since this is a long-running daemon process (`daemon.serve()`, started via the CLI's `daemon` verb per T4.9), you need an in-process scheduling mechanism — the simplest correct approach given this codebase's style (no async framework in daemon.py currently, uvicorn.run is synchronous/blocking) is likely a background `threading.Thread` running a sleep-loop that calls GC once per some interval, started before `uvicorn.run()` and cleanly stopped/joined on shutdown — but use your own engineering judgment; check if `daemon.py` already has any threading precedent to follow (it may not; T5.6's startup reconcile just runs once synchronously before serving, not on a loop). Keep it simple and testable: your test file should be able to inject a short interval / call the scheduling function directly rather than actually waiting a full day.
4. No other task in this parallel cohort touches `daemon.py` or `kernel/store.py` (T9.1 touches sync/watcher.py+sync/reconcile.py; T9.2 might touch kernel/store.py read-only per its own nuance note, but that should not conflict with your GC call which is a call-site only, not a store.py edit — you should not need to edit store.py at all, since `gc_objects` already exists). T9.4 touches cli/main.py. Should be no file collision for you.

## Non-negotiable rules (from CLAUDE.md — binding)

1. Never invent schema, endpoints, ID formats, or grammar beyond docs/mvp-spec.md. If ambiguous, implement the narrowest reading, add a `# SPEC-QUESTION:` comment at the site, and include a formatted entry in your `spec_questions` return field.
2. Never edit golden files, fixtures, or acceptance tests (tests/golden/**) to make an implementation pass.
3. Every mutation of persistent state goes through src/akasha/kernel/store.py; no other module writes SQLite directly. (You are calling the EXISTING gc_objects, not writing new SQL.)
4. pickle, eval, exec are forbidden everywhere.
5. Touch only src/akasha/daemon.py and tests/integration/test_gc_schedule.py. If you find you truly need another file, stop and log a SPEC-QUESTION instead of guessing.
6. The task is not DONE until Verify passes locally. Never weaken the test or move on if it fails after your retry budget.

## Hang guard

If you have not reached a terminal status (DONE or BLOCKED) within roughly 20 tool calls, stop immediately and return status BLOCKED with blocked_reason "possible hang — exceeded tool-call budget".

## Return Value

End your reply with a single fenced ```json block with exactly these fields (per your persona's Return Value section): status ("DONE" or "BLOCKED"), files_changed (array, from git status/diff — never a guess), verify_command, verify_exit_code, verify_stdout_tail, spec_questions (array, empty if none), blocked_reason (required iff BLOCKED), and cursor_task_json/cursor_response_json (strings, only if you delegated to Cursor via cursor_bridge.py). This must be the literal final thing in your reply.
