See docs/agents/logs/20260725-064544-M11-discovery-wiring/workers/T11.3/prompt.md for the task.
The fleet-verifier was instructed to independently re-run T11.3's Verify command, scrutinize the
worker's pyright-baseline claim via git stash comparison, confirm two distinct idempotency test
functions exist (not folded into one), confirm sync/watcher.py untouched, confirm no third test
file added, and confirm docs/spec-questions.md's Resolution line was appended correctly without
deleting the original T11.1 entry.
