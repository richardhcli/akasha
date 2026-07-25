#!/usr/bin/env bash
# Starts the overnight fleet-dispatch loop (scripts/fleet/overnight_runner.sh)
# detached inside a tmux session, so it survives the terminal/SSH session
# closing. Idempotent: refuses to start a second copy if the session is
# already running.
#
# Usage:
#   scripts/fleet/start_overnight.sh
#   OVERNIGHT_MODEL=opus scripts/fleet/start_overnight.sh              # override driving model
#   OVERNIGHT_WORKER_MODE=hybrid scripts/fleet/start_overnight.sh      # allow Cursor delegation (default: claude-only)
#   OVERNIGHT_MAX_RUNTIME_SECS=21600 scripts/fleet/start_overnight.sh  # 6h ceiling instead of the 12h default
#   OVERNIGHT_END_TIME=09:00 scripts/fleet/start_overnight.sh          # also stop by 9am, whichever ceiling hits first
# See scripts/fleet/overnight_runner.sh's header comment for the full list
# of tuning env vars (halt-retry behavior, backoff timing, etc.).
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
MAX_RUNTIME_SECS="${OVERNIGHT_MAX_RUNTIME_SECS:-$((12 * 3600))}"
END_TIME="${OVERNIGHT_END_TIME:-}"
END_TIME_TZ="${OVERNIGHT_END_TIME_TZ:-}"

# Pass tuning env vars explicitly into the tmux command rather than relying
# on env inheritance (tmux only inherits the *client's* environment into a
# new session, which gets fuzzy if a server is already running from a
# different shell). No `tee` here: overnight_runner.sh writes its own
# durable log directly (reopening docs/agents/logs/overnight-runner.log by
# path on every line — see its header comment) precisely because a `tee -a`
# held open for the tmux pane's lifetime silently lost two invocations'
# worth of log lines on 2026-07-25 when an in-run git operation replaced
# that tracked file's inode out from under tee's fd. tmux still shows the
# runner's stdout live in the pane; only the durability guarantee moved
# inside the script.
tmux new-session -d -s "$SESSION" -c "$REPO_DIR" \
  "OVERNIGHT_MODEL='$DRIVING_MODEL' OVERNIGHT_WORKER_MODE='$WORKER_MODE' OVERNIGHT_MAX_RUNTIME_SECS='$MAX_RUNTIME_SECS' OVERNIGHT_END_TIME='$END_TIME' OVERNIGHT_END_TIME_TZ='$END_TIME_TZ' scripts/fleet/overnight_runner.sh"

echo "Started overnight fleet-dispatch loop in tmux session '$SESSION'."
echo "  model:       $DRIVING_MODEL (driving loop; fleet-orchestrator scanner stays Opus regardless)"
echo "  worker mode: $WORKER_MODE ($( [[ "$WORKER_MODE" == "claude-only" ]] && echo "pure Claude, no Cursor" || echo "fleet-worker may delegate to Cursor" ))"
echo "  ceiling:     ${MAX_RUNTIME_SECS}s (~$(awk -v s="$MAX_RUNTIME_SECS" 'BEGIN { printf "%.1f", s / 3600 }')h) duration — override with OVERNIGHT_MAX_RUNTIME_SECS"
if [[ -n "$END_TIME" ]]; then
  echo "  end time:    $END_TIME${END_TIME_TZ:+ ($END_TIME_TZ)} — whichever of this and the duration ceiling hits first wins"
fi
echo "  attach:      tmux attach -t $SESSION"
echo "  logs:        $LOG_FILE (or: tmux capture-pane -t $SESSION -p)"
echo "  stop:        scripts/fleet/stop_overnight.sh"
