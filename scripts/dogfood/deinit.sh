#!/usr/bin/env bash
# Stop a dogfood scratch daemon started by init.sh. Leaves the vault/DB/config
# on disk intact -- use destroy.sh to actually delete the scratch tree.
#
# Usage: scripts/dogfood/deinit.sh <name>
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
source scripts/dogfood/lib.sh

NAME="${1:?usage: deinit.sh <name>}"
SCRATCH="$(scratch_dir "$NAME")"

if [ ! -d "$SCRATCH" ]; then
  echo "'$SCRATCH' does not exist -- nothing to stop" >&2
  exit 0
fi

PIDFILE="$SCRATCH/daemon.pid"
if [ ! -f "$PIDFILE" ]; then
  echo "no $PIDFILE -- daemon was never started via init.sh, or already stopped"
  exit 0
fi

PID="$(cat "$PIDFILE")"
if kill -0 "$PID" 2>/dev/null; then
  kill "$PID" 2>/dev/null || taskkill //F //PID "$PID" >/dev/null 2>&1 || true
  for _ in $(seq 1 20); do
    kill -0 "$PID" 2>/dev/null || break
    sleep 0.25
  done
  if kill -0 "$PID" 2>/dev/null; then
    echo "pid $PID did not stop gracefully, force-killing" >&2
    taskkill //F //PID "$PID" >/dev/null 2>&1 || true
  fi
  echo "stopped daemon pid $PID"
else
  echo "pid $PID from $PIDFILE was not running (already stopped)"
fi
rm -f "$PIDFILE"
