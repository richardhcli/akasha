You are being invoked headlessly (`claude -p`) as a ONE-OFF MECHANISM TEST of
the fleet-dispatch Path B pipeline (direct `Agent`-tool dispatch) described in
`docs/agents/runbook.md` — this is NOT a real build-plan task. A prior attempt
in this same test session confirmed the `Workflow` tool is NOT available in
this headless session, so this test specifically exercises the fallback:
direct dispatch via the `Agent` tool. The user's usage limits are tight right
now, so stay as small and fast as possible: no exploration, no extra reading
beyond what's needed, minimal tool calls.

Do exactly this, in order:

1. Skip the `fleet-orchestrator` scan step (the task below is synthetic, not
   scanned from `docs/build-plan.md`).

2. Generate a `run_id` of the form `<YYYYMMDD-HHMMSS>-path-b-smoketest` using
   the current UTC time.

3. Spawn exactly one subagent via the `Agent` tool with `subagent_type:
   "fleet-worker"` (foreground, not backgrounded — wait for its real result
   before proceeding) with this prompt:

   ```
   You are a fleet-worker executing build-plan task TEST-PATH-B-SMOKETEST.

   Goal: Create a new file at docs/agents/logs/.smoketest-marker containing
   exactly one line: fleet-smoketest-ok
   Depends on: none
   Files you may create or edit: docs/agents/logs/.smoketest-marker
   Spec reference: N/A -- synthetic mechanism test, not a real build-plan
   task (see docs/agents/runbook.md Path B)
   Steps: Write the file with exactly the content 'fleet-smoketest-ok\n'
   (one line, trailing newline, no other content).
   Verify command: test -f docs/agents/logs/.smoketest-marker && grep -qx
   fleet-smoketest-ok docs/agents/logs/.smoketest-marker && echo VERIFY_OK
   Definition of done: verify_cmd prints VERIFY_OK and exits 0

   Run the Verify command yourself via Bash and report its REAL exit code
   and output tail. If you have not reached a terminal status within
   roughly 15 tool calls, stop and report BLOCKED: possible hang instead.

   End your reply with a fenced ```json block matching this shape:
   {"status": "DONE|BLOCKED", "files_changed": [...], "verify_command": "...",
    "verify_exit_code": <int>, "verify_stdout_tail": "...", "spec_questions": []}
   ```

4. Once you have the worker's real result (not before), spawn exactly one
   more subagent via the `Agent` tool with `subagent_type: "fleet-verifier"`
   (if that exact string is rejected as an invalid `subagent_type`, fall back
   to `subagent_type: "general-purpose"` with the same content inlined) to
   independently re-check the claim, per `.claude/agents/fleet-verifier.md`'s
   Steps/Return Value sections (re-run the verify command itself, check the
   file exists/non-empty, cross-check `git status`, end with a fenced
   ```json block matching `{"files_exist": [...], "verify_exit_code": <int>,
   "verify_stdout_tail": "...", "git_status_matches_claim": bool, "verdict":
   "CONFIRMED_DONE|CONTRADICTS_CLAIM|CONFIRMED_BLOCKED", "notes": "..."}`).

5. Once you have the verifier's real result, persist the durable log under
   `docs/agents/logs/<run_id>/` via `scripts/fleet/log_run.py` exactly per
   `docs/agents/runbook.md` "Logging" / Path B (save each exact prompt you
   sent to a temp file, pipe each exact returned JSON block through
   `python scripts/fleet/log_run.py task --run-id <run_id> --task-id
   TEST-PATH-B-SMOKETEST --kind worker|verify --prompt <tmpfile> --result -`),
   then `python scripts/fleet/log_run.py manifest --run-id <run_id> --cohort
   TEST-PATH-B-SMOKETEST --status COMPLETE --notes "Path B mechanism
   smoketest via claude -p, synthetic task, not a real build-plan task"`.

6. Do NOT modify `docs/agents/task-status.md`. Do NOT run any `git
   add`/`commit`/`push`. Do NOT touch any file outside
   `docs/agents/logs/<run_id>/**` and `docs/agents/logs/.smoketest-marker`.

7. Reply with a short final summary (under 200 words): the `run_id`, whether
   `subagent_type: "fleet-worker"` and `"fleet-verifier"` resolved correctly
   (or which fell back to general-purpose), the worker's `status`, the
   verifier's `verdict`, and the exact paths you wrote under
   `docs/agents/logs/`.
