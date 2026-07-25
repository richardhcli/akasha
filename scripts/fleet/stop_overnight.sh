#!/usr/bin/env bash
# Requests a clean stop of the overnight fleet-dispatch loop started by
# start_overnight.sh. Soft stop only: touches scripts/fleet/.stop.
# overnight_runner.sh checks it every ~30s, including during its
# multi-hour rate-limit backoff sleeps (interruptible_sleep) — so it stops
# promptly *between* invocations, but never mid-invocation: an in-flight
# `claude -p` call (including that cohort's commit/push) always finishes
# first rather than being killed mid-write.
#
# Usage:
#   scripts/fleet/stop_overnight.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_DIR"

SESSION="${OVERNIGHT_TMUX_SESSION:-akasha-overnight}"
STOP_FILE="scripts/fleet/.stop"

touch "$STOP_FILE"
echo "Wrote $STOP_FILE — the loop will exit after its current invocation finishes"
echo "(checked every ~30s, including during backoff sleeps; never mid-invocation)."

if command -v tmux >/dev/null 2>&1 && tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "tmux session '$SESSION' is still up — watch it exit with: tmux attach -t $SESSION"
  echo "(it will end the tmux pane itself once the runner process exits;"
  echo "this script does not force-kill the session — a hung invocation"
  echo "would otherwise be cut off mid-commit/push)."
fi
