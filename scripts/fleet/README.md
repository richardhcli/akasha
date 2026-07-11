# Agent Fleet Orchestration Utilities

Utilities for the 3-tier agent fleet that orchestrates akasha build-plan execution, optimizing token cost by matching task complexity to model tier.

## Files

- **`cursor_bridge.py`** — Executor subprocess that bridges fleet-worker agents to the Cursor Agent CLI. Implements the abstract JSON contract: task_json in → diff_stat/usage JSON out. Designed to be swappable with other edit executors (same contract, different implementation).

## Architecture

See `docs/agents/fleet-architecture.md` for the full design document.

**Tier 1 (Opus):** orchestrator — decision logic, parallelization, result reconciliation.
**Tier 2 (Sonnet):** worker — owns one task, decides whether to delegate to Tier 3, verifies.
**Tier 3 (Cursor Grok 4.5 High):** editor — called as subprocess for large-context/mechanical edits.

## Usage

### For fleet-worker agents

```bash
# Build task JSON
task_json='{"task_id":"T2.4","goal":"...","files":["..."],"constraints":"..."}'

# Pipe to bridge (uses Cursor Grok 4.5 High by default)
echo "$task_json" | python3 scripts/fleet/cursor_bridge.py

# Inspect output
{
  "status": "completed" | "unavailable" | "error" | "timeout",
  "files_changed": [...],
  "diff_stat": "...",
  "cursor_result_text": "...",
  "usage": {...}
}
```

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
echo '{"task_id":"TEST","goal":"Test","files":[],"constraints":""}' | \
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
