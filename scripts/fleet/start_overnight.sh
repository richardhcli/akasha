#!/usr/bin/env bash
# Starts the overnight fleet-dispatch loop (scripts/fleet/overnight_runner.sh)
# detached inside a tmux session, so it survives the terminal/SSH session
# closing. Idempotent: refuses to start a second copy if the session is
# already running.
#
# Usage:
#   scripts/fleet/start_overnight.sh
#   OVERNIGHT_MODEL=opus scripts/fleet/start_overnight.sh          # override driving model
#   OVERNIGHT_WORKER_MODE=hybrid scripts/fleet/start_overnight.sh  # allow Cursor delegation (default: claude-only)
#
# Attach to watch it live:
#   tmux attach -t akasha-overnight
#   (detach again with Ctrl-b d — do not close the tmux window itself)
#
# Stop it:
#   scripts/fleet/stop_overnight.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_DIR"

SESSION="${OVERNIGHT_TMUX_SESSION:-akasha-overnight}"

if ! command -v tmux >/dev/null 2>&1; then
  echo "tmux is not installed — install it (e.g. apt-get install -y tmux) or run" >&2
  echo "scripts/fleet/overnight_runner.sh directly via nohup instead." >&2
  exit 1
fi

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "tmux session '$SESSION' is already running — not starting a second copy."
  echo "Attach with: tmux attach -t $SESSION"
  exit 0
fi

# Catches a runner started outside this tmux session (e.g. via nohup, or a
# differently-named tmux session) — without this, a second concurrent
# runner would double-commit and double-push each cohort.
PID_FILE="scripts/fleet/.overnight_runner.pid"
if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "overnight_runner.sh is already running (pid $(cat "$PID_FILE"), per $PID_FILE)" >&2
  echo "— not starting a second copy. If that pid is stale, remove $PID_FILE and retry." >&2
  exit 1
fi

mkdir -p docs/agents/logs
LOG_FILE="docs/agents/logs/overnight-runner.log"
DRIVING_MODEL="${OVERNIGHT_MODEL:-sonnet}"
WORKER_MODE="${OVERNIGHT_WORKER_MODE:-claude-only}"

# Pass OVERNIGHT_MODEL/OVERNIGHT_WORKER_MODE explicitly into the tmux
# command rather than relying on env inheritance (tmux only inherits the
# *client's* environment into a new session, which gets fuzzy if a server
# is already running from a different shell). `tee -a` makes output both
# visible live in the tmux pane and durably appended to the log file for
# morning review — the runner's own log() calls only go to stdout, nothing
# else persists them.
tmux new-session -d -s "$SESSION" -c "$REPO_DIR" \
  "OVERNIGHT_MODEL='$DRIVING_MODEL' OVERNIGHT_WORKER_MODE='$WORKER_MODE' scripts/fleet/overnight_runner.sh 2>&1 | tee -a $LOG_FILE"

echo "Started overnight fleet-dispatch loop in tmux session '$SESSION'."
echo "  model:       $DRIVING_MODEL (driving loop; fleet-orchestrator scanner stays Opus regardless)"
echo "  worker mode: $WORKER_MODE ($( [[ "$WORKER_MODE" == "claude-only" ]] && echo "pure Claude, no Cursor" || echo "fleet-worker may delegate to Cursor" ))"
echo "  attach:      tmux attach -t $SESSION"
echo "  logs:        $LOG_FILE (or: tmux capture-pane -t $SESSION -p)"
echo "  stop:        scripts/fleet/stop_overnight.sh"
