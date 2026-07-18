Repo root: /home/richardhcli/projects/personal-projects/akasha. Run id: 20260718-054005-M9. Task id: T9.4.

You are a fleet-worker (Tier-2, Sonnet) per your persona in `.claude/agents/fleet-worker.md` (read it first) and the root `CLAUDE.md` (read it too — its "Non-negotiable rules" section is binding on you). This is one task in a larger overnight fleet run against `docs/build-plan.md` / `docs/agents/task-status.md` — you own exactly this one task.

## Task block (verbatim from docs/build-plan.md, verify it yourself before starting)

### T9.4 — --dry-run coverage + error-message pass
- Goal: Ensure every mutating CLI verb supports --dry-run; audit error messages for clarity.
- Depends on: T4.8 (DONE).
- Files: src/akasha/cli/main.py, tests/integration/test_cli_dry_run.py
- Spec: §4.12 (--dry-run returns would-be request), M9 (dry-run coverage, error-message pass) — read docs/mvp-spec.md §4.12 yourself.
- Steps: (1) Verify each mutating verb returns the would-be request under --dry-run and mutates nothing. (2) Standardize error messages against the API error envelope. (3) Add a coverage test enumerating the verbs.
- Verify: uv run pytest tests/integration/test_cli_dry_run.py
- DoD: every mutating verb has a passing --dry-run case with zero state change.

## Known nuances (from the orchestrator's scan — verify, don't just trust)

1. **This is largely an audit, not new-feature work**: per T4.8's own completion notes in docs/agents/task-status.md (read the T4.8 row in full), `--dry-run` was already implemented for mutating verbs at T4.8 time ("mutating verbs print `{"method","path","body"}` and `typer.Exit(0)` before any httpx call — proven in tests by pointing --dry-run at an unreachable base-url and asserting no exception"). Your job is to (a) enumerate every CURRENT mutating verb in `cli/main.py` (there may be more now than at T4.8 time — check T4.10/T4.11/T7.x/T8.x for anything that added CLI surface) and confirm each one genuinely has working --dry-run coverage, fixing any that don't; (b) write the NEW coverage test file `tests/integration/test_cli_dry_run.py` that enumerates every mutating verb systematically (T4.8's own `tests/integration/test_cli.py` tests --dry-run per-verb already but is not the dedicated enumeration file this task wants — don't just duplicate it, build a systematic table-driven/parametrized enumeration so a future new verb is caught if it lacks --dry-run); (c) do a pass over CLI-emitted error messages for consistency/clarity against the API's §4.11 error envelope shape (`{"error":{code,message,detail}}`) — this is a real audit, use judgment on what "standardize" means, document your specific changes.
2. No sibling task in this parallel cohort touches `cli/main.py` or any test file you're touching (T9.1→sync/, T9.2→metrics.py+api/routes/, T9.3→daemon.py) — should be no collision.
3. If you find a genuinely missing --dry-run case (not just missing test coverage, but an actual mutating verb bypassing --dry-run), fix it in cli/main.py per the existing --dry-run pattern (print request dict + typer.Exit(0) before any httpx call) — that's within your Files list, not a scope violation.

## Non-negotiable rules (from CLAUDE.md — binding)

1. Never invent schema, endpoints, ID formats, or grammar beyond docs/mvp-spec.md. If ambiguous, implement the narrowest reading, add a `# SPEC-QUESTION:` comment at the site, and include a formatted entry in your `spec_questions` return field.
2. Never edit golden files, fixtures, or acceptance tests (tests/golden/**) to make an implementation pass.
3. Every mutation of persistent state goes through src/akasha/kernel/store.py; no other module writes SQLite directly. (cli/main.py is a pure HTTP client per T4.8's design — do not add any direct DB/store access.)
4. pickle, eval, exec are forbidden everywhere.
5. Touch only src/akasha/cli/main.py and tests/integration/test_cli_dry_run.py. If you find you truly need another file, stop and log a SPEC-QUESTION instead of guessing.
6. The task is not DONE until Verify passes locally. Never weaken the test or move on if it fails after your retry budget.

## Hang guard

If you have not reached a terminal status (DONE or BLOCKED) within roughly 20 tool calls, stop immediately and return status BLOCKED with blocked_reason "possible hang — exceeded tool-call budget".

## Return Value

End your reply with a single fenced ```json block with exactly these fields (per your persona's Return Value section): status ("DONE" or "BLOCKED"), files_changed (array, from git status/diff — never a guess), verify_command, verify_exit_code, verify_stdout_tail, spec_questions (array, empty if none), blocked_reason (required iff BLOCKED), and cursor_task_json/cursor_response_json (strings, only if you delegated to Cursor via cursor_bridge.py). This must be the literal final thing in your reply.
