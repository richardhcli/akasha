#!/usr/bin/env bash
# Arm the kb-io-bench runner and its viewer (idempotent).
# usage: agent/start.sh [now|<unix-epoch>] [max-attempts]
# watch:  tmux attach -r -t '=kbio-view'     (read-only: keys, including Ctrl-C, are ignored)
# state:  python3 agent/runner.py status
# stop:   touch data/experiments/kb-io-bench/agent-logs/STOP   (or Ctrl-C in the `kbio` pane)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
START="${1:-now}"; MAX="${2:-30}"
if tmux has-session -t '=kbio' 2>/dev/null; then
  echo "runner session 'kbio' already exists"; else
  tmux new-session -d -s kbio "python3 '$HERE/runner.py' run --start '$START' --max-attempts '$MAX'"
  echo "runner armed in tmux session 'kbio' (start: $START)"; fi
if ! tmux has-session -t '=kbio-view' 2>/dev/null; then
  tmux new-session -d -s kbio-view "python3 '$HERE/runner.py' view"; fi
echo "watch: tmux attach -r -t '=kbio-view'   status: python3 $HERE/runner.py status"
