# Agent Fleet Orchestration Utilities

Utilities for the 3-tier agent fleet that orchestrates akasha build-plan execution, optimizing token cost by matching task complexity to model tier.

## Files

- **`cursor_bridge.py`** — Executor subprocess that bridges fleet-worker agents to the Cursor Agent CLI. Implements the abstract JSON contract: task_json (incl. `verify_cmd`) in → edit evidence + independently-run verify result JSON out. Designed to be swappable with other edit executors (same contract, different implementation).
- **`log_run.py`** — Mechanical, schema-checked writer for `docs/agents/logs/<run_id>/`. Required when dispatching via direct `Task`-tool calls instead of the `Workflow` tool (`docs/agents/runbook.md` "Path B"), since that path has no deterministic script enforcing the logging step the way a `Workflow` run does. See "Path B logging" below.
- **`overnight_runner.sh`** / **`overnight_prompt.md`** — unattended overnight loop. See "Overnight unattended runs" below.

## Path B logging (`log_run.py`)

If your session dispatches fleet work via a `Task`-style tool directly
(no `Workflow` tool — see `docs/agents/runbook.md` "Choosing a dispatch
path"), pipe every worker/verifier result through this script the moment
you receive it, before writing anything else about that task:

```bash
# Persist a fleet-worker's result (save its prompt to a file first, then
# paste/pipe its verbatim returned JSON block on stdin):
python scripts/fleet/log_run.py task \
  --run-id 20260718-143000-M9 --task-id T9.1 --kind worker \
  --prompt /tmp/t9.1-worker-prompt.md --result -

# Persist the independent verifier's result the same way:
python scripts/fleet/log_run.py task \
  --run-id 20260718-143000-M9 --task-id T9.1 --kind verify \
  --prompt /tmp/t9.1-verify-prompt.md --result /tmp/t9.1-verify-result.json

# Create/update the run's manifest as tasks land:
python scripts/fleet/log_run.py manifest \
  --run-id 20260718-143000-M9 --cohort T9.1 --status IN_PROGRESS
```

It validates the same required fields `WORKER_SCHEMA`/`VERIFY_SCHEMA` (in
`docs/agents/fleet-workflow.js`) demand for Path A, and refuses to write
anything — no partial files — on a missing/invalid field or a duplicate
`(run_id, task_id, kind)` entry (pass `--force` for a genuine
re-verification). It is a pure, non-LLM file writer: it never talks to a
model and never invents a value, so it's exactly as trustworthy as the JSON
you give it — which is why copying each subagent's returned block verbatim,
rather than retyping/paraphrasing it, is the part that actually matters.

## Overnight unattended runs

`overnight_runner.sh` repeatedly invokes headless Claude Code (`claude -p`,
Opus by default) with `overnight_prompt.md` as the prompt, which instructs
it to run one fleet-dispatch cohort per the primary path in
`docs/agents/runbook.md`, commit + push after each cohort, and write
`docs/agents/logs/OVERNIGHT_HALT.md` instead of guessing once no eligible
work remains.

This is local-first by design (it shells out to local `cursor-agent`, runs
local `pytest`, reads/writes the local repo) — it deliberately does **not**
use cloud routines/`RemoteTrigger`, which run in claude.ai's own sandbox
and can't reach any of that.

**Start it** (survives the terminal closing; pick one):
```bash
# tmux
tmux new -d -s fleet-overnight scripts/fleet/overnight_runner.sh

# or nohup
nohup scripts/fleet/overnight_runner.sh > docs/agents/logs/overnight-runner.log 2>&1 &
disown
```

**Stop it:** `touch scripts/fleet/.stop` (checked once per loop iteration —
not instant). Or `tmux kill-session -t fleet-overnight` / `kill $(cat
scripts/fleet/.overnight_runner.pid)`.

**How it handles the usage-window limit:** there is no documented way to
query remaining usage before a call, so the runner treats a failed
invocation as the signal. One failure gets a short retry (might be a
transient blip); two failures in a row are treated as the account's rolling
usage window being exhausted, and it sleeps `OVERNIGHT_RESET_SECS` (default
5h — set this to what your claude.ai usage page actually shows) before
resuming. Tune via env vars: `OVERNIGHT_MODEL`, `OVERNIGHT_RESET_SECS`,
`OVERNIGHT_SHORT_BACKOFF_SECS`, `OVERNIGHT_BETWEEN_RUNS_SECS`,
`OVERNIGHT_FAIL_THRESHOLD`.

**Safety net:** runs with `--dangerously-skip-permissions` (no human present
to answer prompts) but pairs it with `--disallowedTools` denying
force-push, `reset --hard`, `rebase`, `filter-branch`, `commit --amend`,
`branch -D`, and `clean -f` — spot-checked that a deny pattern does hold
even under skip-permissions (see `permission_denials` in a run's JSON
output). Everything else is allowed, including regular `git commit` +
`git push` after each cohort, per this repo's current choice to run the
fleet unattended on a VM with disposable, revertible history.

**Not yet verified against a real overnight run:** the exact text/exit code
Claude Code returns when the usage window is actually exhausted (the
2-failure heuristic above is a guess at that signal, not a confirmed one),
and the deny-pattern glob syntax for patterns other than the one spot-check
above. Watch the first run's logs under `docs/agents/logs/` and
`docs/agents/logs/overnight-runner.log`.

## Architecture

See `docs/agents/fleet-architecture.md` for the full design document.

**Tier 1 (Opus):** orchestrator — decision logic, parallelization, result reconciliation.
**Tier 2 (Sonnet):** worker — owns one task, decides whether to delegate to Tier 3, verifies.
**Tier 3 (Cursor Grok 4.5 High):** editor — called as subprocess for large-context/mechanical edits.

## Usage

### For fleet-worker agents

```bash
# Build task JSON — verify_cmd is required
task_json='{"task_id":"T2.4","goal":"...","files":["..."],"constraints":"...","verify_cmd":"uv run pytest tests/unit/test_x.py -q"}'

# Pipe to bridge (uses Cursor Grok 4.5 High by default)
echo "$task_json" | python3 scripts/fleet/cursor_bridge.py

# Inspect output
{
  "status": "completed" | "unavailable" | "error" | "timeout",
  "files_changed": [...],
  "diff_stat": "...",
  "cursor_result_text": "...",
  "verify_command": "...",
  "verify_exit_code": 0,
  "verify_stdout_tail": "...",
  "usage": {...}
}
```

`verify_command`/`verify_exit_code`/`verify_stdout_tail` come from the bridge
independently re-running `verify_cmd` as a plain subprocess *after* Cursor's
own edit+fix-loop — real evidence, not Cursor's self-report, and free (no
extra LLM tokens). This is still only Tier-3/advisory evidence: the worker
always re-runs Verify itself before claiming `DONE`, and an independent
verifier agent re-runs it again before the task is flipped `DONE` in
`task-status.md`. See `docs/agents/fleet-architecture.md` "Verification
Model".

### Model Selection (Modular & Refactorable)

**Current default:** Cursor Grok 4.5 High (`grok-4.5-high`)

Override via CLI flag:
```bash
python3 scripts/fleet/cursor_bridge.py --model composer-2.5
```

Or environment variable:
```bash
export AKASHA_FLEET_CURSOR_MODEL=gpt-5.3-codex-high
echo "$task_json" | python3 scripts/fleet/cursor_bridge.py
```

**Available Cursor models** (run `cursor-agent models` for current list):
- `grok-4.5-high` (current default)
- `grok-4.5-medium`, `grok-4.5-xhigh`, etc. (other Grok variants)
- `composer-2.5` (previous default, still available)
- `gpt-5.3-codex-*`, `claude-opus-4-8`, etc. (other providers)

**Rationale for current choice:**
- Grok 4.5 High: Best for large-context code edits, strong spec-following
- Modular via env var + CLI flag: Can swap to different model without code changes
- Only one model active now: Simplifies testing & cost tracking

### Other Options

```bash
# Set longer timeout (default 600s)
python3 scripts/fleet/cursor_bridge.py --timeout 900

# Combine overrides
python3 scripts/fleet/cursor_bridge.py --model composer-2.5 --timeout 300
```

## Testing

```bash
# Smoke test (valid input)
echo '{"task_id":"TEST","goal":"Test","files":[],"constraints":"","verify_cmd":"true"}' | \
  python3 scripts/fleet/cursor_bridge.py | python3 -m json.tool

# Error handling (invalid JSON)
echo "bad json" | python3 scripts/fleet/cursor_bridge.py | python3 -m json.tool

# Unavailable path (if cursor-agent is logged out)
# — will return {"status":"unavailable","reason":"..."}
```

## Future Extensions

1. **Alternative executors:** implement the same JSON contract for non-Cursor editors (e.g., native Claude Code edit agents).
2. **Async executor:** wrap cursor_bridge calls in async pools for true parallel worker invocation.
3. **Cost tracking:** log usage tokens per task/tier for cost reporting and budget enforcement.
