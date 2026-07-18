#!/usr/bin/env bash
# Overnight unattended fleet-dispatch loop.
#
# Repeatedly invokes headless `claude -p` against overnight_prompt.md until
# either all build-plan work is done (fleet writes OVERNIGHT_HALT.md) or the
# stop file appears. On any failed invocation (including the account's
# rolling usage-window limit — there is no documented pre-flight way to
# query that, so this treats "the call failed" as the signal) it backs off:
# a short retry for what might be a transient blip, escalating to a
# full-window sleep if failures keep repeating.
#
# Usage:
#   nohup scripts/fleet/overnight_runner.sh > docs/agents/logs/overnight-runner.log 2>&1 &
#   disown
#   # or: tmux new -d -s fleet-overnight scripts/fleet/overnight_runner.sh
#
# Stop:
#   touch scripts/fleet/.stop
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_DIR"

PROMPT_FILE="scripts/fleet/overnight_prompt.md"
STOP_FILE="scripts/fleet/.stop"
HALT_FILE="docs/agents/logs/OVERNIGHT_HALT.md"
LOG_DIR="docs/agents/logs"
PID_FILE="scripts/fleet/.overnight_runner.pid"

MODEL="${OVERNIGHT_MODEL:-opus}"
RESET_SLEEP_SECS="${OVERNIGHT_RESET_SECS:-$((5 * 3600))}"   # ~5h rolling usage window
SHORT_BACKOFF_SECS="${OVERNIGHT_SHORT_BACKOFF_SECS:-60}"
BETWEEN_RUNS_SECS="${OVERNIGHT_BETWEEN_RUNS_SECS:-15}"
CONSEC_FAIL_THRESHOLD="${OVERNIGHT_FAIL_THRESHOLD:-2}"       # fails in a row before assuming it's the usage window, not a blip

echo $$ > "$PID_FILE"
mkdir -p "$LOG_DIR"

log() {
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1"
}

# Stale halt file from a previous night blocks a fresh run from progressing
# silently — archive it so the loop starts clean, but never delete it (the
# human should still see it in the morning if they never cleared it).
if [[ -f "$HALT_FILE" ]]; then
  archived="$LOG_DIR/OVERNIGHT_HALT.$(date -u +%Y%m%dT%H%M%SZ).md"
  mv "$HALT_FILE" "$archived"
  log "archived stale halt file from a previous run to $archived"
fi

rm -f "$STOP_FILE"

consec_fails=0

while true; do
  if [[ -f "$STOP_FILE" ]]; then
    log "stop file present ($STOP_FILE) — exiting"
    rm -f "$STOP_FILE"
    break
  fi

  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  out_file="$LOG_DIR/overnight-invocation-$ts.json"

  log "invoking claude -p (model=$MODEL) — output -> $out_file"
  if claude -p "$(cat "$PROMPT_FILE")" \
      --model "$MODEL" \
      --output-format json \
      --dangerously-skip-permissions \
      --disallowedTools "Bash(git push --force*)" "Bash(git push -f*)" \
                         "Bash(git reset --hard*)" "Bash(git rebase*)" \
                         "Bash(git filter-branch*)" "Bash(git commit --amend*)" \
                         "Bash(git branch -D*)" "Bash(git clean -f*)" \
      > "$out_file" 2> "$out_file.stderr"; then
    log "invocation ok"
    consec_fails=0
  else
    exit_code=$?
    consec_fails=$((consec_fails + 1))
    log "invocation failed (exit $exit_code, consecutive=$consec_fails) — see $out_file.stderr"

    if [[ $consec_fails -ge $CONSEC_FAIL_THRESHOLD ]]; then
      log "treating repeated failure as the usage-window limit — sleeping ${RESET_SLEEP_SECS}s"
      sleep "$RESET_SLEEP_SECS"
      consec_fails=0
    else
      log "single failure — short backoff ${SHORT_BACKOFF_SECS}s in case it's transient"
      sleep "$SHORT_BACKOFF_SECS"
    fi
    continue
  fi

  if [[ -f "$HALT_FILE" ]]; then
    log "fleet wrote $HALT_FILE — no eligible work, stopping loop"
    break
  fi

  sleep "$BETWEEN_RUNS_SECS"
done

rm -f "$PID_FILE"
log "overnight runner exited"
