You are being invoked headlessly (`claude -p`) as a ONE-OFF MECHANISM TEST of
the fleet-dispatch Path A pipeline described in `docs/agents/runbook.md` —
this is NOT a real build-plan task. The user's usage limits are tight right
now, so stay as small and fast as possible: no exploration, no extra reading
beyond what's needed, minimal tool calls.

Do exactly this, in order:

1. Skip the `fleet-orchestrator` scan step entirely — the cohort below is
   synthetic (not scanned from `docs/build-plan.md`), so there is nothing to
   scan for.

2. Generate a `run_id` of the form `<YYYYMMDD-HHMMSS>-path-a-smoketest` using
   the current UTC time.

3. Invoke the `Workflow` tool exactly like this (fill in the real `run_id`):

   ```
   Workflow({
     scriptPath: "docs/agents/fleet-workflow.js",
     args: {
       run_id: "<run_id>",
       cohort: [{
         "task_id": "TEST-PATH-A-SMOKETEST",
         "goal": "Create a new file at docs/agents/logs/.smoketest-marker containing exactly one line: fleet-smoketest-ok",
         "depends_on": [],
         "files": ["docs/agents/logs/.smoketest-marker"],
         "spec_ref": "N/A -- synthetic mechanism test, not a real build-plan task (see docs/agents/runbook.md Path A)",
         "steps": "Write the file with exactly the content 'fleet-smoketest-ok\n' (one line, trailing newline, no other content).",
         "verify_cmd": "test -f docs/agents/logs/.smoketest-marker && grep -qx fleet-smoketest-ok docs/agents/logs/.smoketest-marker && echo VERIFY_OK",
         "dod": "verify_cmd prints VERIFY_OK and exits 0"
       }]
     }
   })
   ```

4. When the Workflow resolves, persist the durable log for this run under
   `docs/agents/logs/<run_id>/` by piping the literal returned worker result
   and verifier result through `scripts/fleet/log_run.py`, per
   `docs/agents/runbook.md` "Logging" (save each prompt to a temp file
   first, then e.g.
   `python scripts/fleet/log_run.py task --run-id <run_id> --task-id TEST-PATH-A-SMOKETEST --kind worker --prompt <tmpfile> --result -`
   piping the exact returned JSON on stdin, and the equivalent `--kind
   verify` call for the verifier's result). Then run
   `python scripts/fleet/log_run.py manifest --run-id <run_id> --cohort TEST-PATH-A-SMOKETEST --status COMPLETE --notes "Path A mechanism smoketest, synthetic task, not a real build-plan task"`.

5. Do NOT modify `docs/agents/task-status.md` (this is not a real task). Do
   NOT run any `git add`/`commit`/`push`. Do NOT touch any file outside
   `docs/agents/logs/<run_id>/**` and `docs/agents/logs/.smoketest-marker`.

6. If you have not reached a terminal state within roughly 15 tool calls
   total, stop and report `BLOCKED: possible hang` instead of continuing.

7. Reply with a short final summary (under 200 words): the `run_id` you
   used, the worker's `status`, the verifier's `verdict`, and the exact
   paths you wrote under `docs/agents/logs/`.
