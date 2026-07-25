#!/usr/bin/env bash
# Overnight unattended fleet-dispatch loop.
#
# Repeatedly invokes headless `claude -p` against overnight_prompt.md until
# either all build-plan work is done (fleet writes OVERNIGHT_HALT.md) or the
# stop file appears. On any failed invocation (including the account's
# rolling usage-window limit — there is no documented pre-flight way to
# query that, so this treats "the call failed" as the signal) it backs off:
# a short retry for what might be a transient blip, escalating to a
# full-window sleep if failures keep repeating. Between successful
# invocations the loop only pauses BETWEEN_RUNS_SECS (default 15s) — it
# keeps chaining cohorts as fast as it can and only waits out a full ~5h
# window when the API actually says the usage window is exhausted.
#
# Top-level driving model defaults to Sonnet (cheap, fast loop driver);
# the fleet-orchestrator scanner subagent it dispatches stays pinned to
# Opus regardless (see .claude/agents/fleet-orchestrator.md), and
# overnight_prompt.md additionally has the Sonnet session consult the
# `advisor` tool (an Opus-backed reviewer of its own transcript) at
# judgment-call checkpoints — cohort sanity-check, stuck/contradiction
# handling, and before concluding no work remains. Override the driving
# model with OVERNIGHT_MODEL=opus if you want the old all-Opus behavior.
#
# Tier-2 worker mode also defaults to pure Claude: OVERNIGHT_WORKER_MODE
# (default "claude-only") dispatches fleet-worker-claude, which never
# shells out to Cursor. Set OVERNIGHT_WORKER_MODE=hybrid to let
# fleet-worker decide per-task whether to delegate to Cursor instead.
#
# Usage:
#   scripts/fleet/start_overnight.sh          # tmux, recommended
#   # or manually:
#   nohup scripts/fleet/overnight_runner.sh > docs/agents/logs/overnight-runner.log 2>&1 &
#   disown
#   # or: tmux new -d -s fleet-overnight scripts/fleet/overnight_runner.sh
#
# Stop:
#   scripts/fleet/stop_overnight.sh    # or: touch scripts/fleet/.stop
#
# Root/container note (confirmed against claude-code 2.1.214, 2026-07-18):
# `--dangerously-skip-permissions` hard-exits (code 1, before any API call)
# when the process is running as root/sudo, unless the undocumented escape
# hatch `IS_SANDBOX=1` is set — this script sets it automatically when it
# detects UID 0, since a disposable unattended VM (this script's documented
# deployment target) commonly runs as root. If you're running as root,
# also consider `apt-get install -y bubblewrap socat` first: without them,
# Claude Code's own internal command-sandboxing silently disables itself
# ("Commands will run WITHOUT sandboxing"), leaving no containment layer
# under `--dangerously-skip-permissions` besides whatever the *outer* host/VM
# provides — this script does not install them itself (a one-time host setup
# concern, not a per-run one).
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_DIR"

PROMPT_FILE="scripts/fleet/overnight_prompt.md"
STOP_FILE="scripts/fleet/.stop"
HALT_FILE="docs/agents/logs/OVERNIGHT_HALT.md"
LOG_DIR="docs/agents/logs"
PID_FILE="scripts/fleet/.overnight_runner.pid"

MODEL="${OVERNIGHT_MODEL:-sonnet}"
RESET_SLEEP_SECS="${OVERNIGHT_RESET_SECS:-$((5 * 3600))}"   # ~5h rolling usage window
SHORT_BACKOFF_SECS="${OVERNIGHT_SHORT_BACKOFF_SECS:-60}"
BETWEEN_RUNS_SECS="${OVERNIGHT_BETWEEN_RUNS_SECS:-15}"
CONSEC_FAIL_THRESHOLD="${OVERNIGHT_FAIL_THRESHOLD:-2}"       # fails in a row before assuming it's the usage window, not a blip

# Worker mode: "claude-only" (default) dispatches fleet-worker-claude for
# every task (pure Claude, never delegates to Cursor); any other value
# (e.g. "hybrid") leaves the choice to fleet-worker's own Cursor-vs-direct
# decision tree. Exported so the top-level `claude -p` session can read it
# via Bash and fold it into the fleet-orchestrator spawn prompt as the
# primary signal (overnight_prompt.md step 1) — see
# docs/agents/fleet-architecture.md §"Worker Mode Selection" for why the
# env var alone, three subagent hops down, is best-effort only.
WORKER_MODE="${OVERNIGHT_WORKER_MODE:-claude-only}"
export AKASHA_FLEET_WORKER_MODE="$WORKER_MODE"

echo $$ > "$PID_FILE"
mkdir -p "$LOG_DIR"

log() {
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1"
}

# Plain `sleep N` for a multi-hour backoff would make `stop_overnight.sh` /
# `touch .stop` sit ignored for up to ~5h (only checked at the top of the
# `while true` loop). Chunk it so the stop file is re-checked every 30s.
interruptible_sleep() {
  local remaining="$1"
  while (( remaining > 0 )); do
    if [[ -f "$STOP_FILE" ]]; then
      return 0
    fi
    local chunk=$(( remaining < 30 ? remaining : 30 ))
    sleep "$chunk"
    remaining=$(( remaining - chunk ))
  done
}

# Best-effort: a real rate-limit hit (confirmed live 2026-07-18) returns a
# result string containing a precise reset time, e.g. "You've hit your
# session limit · resets 1:30am (America/Indiana/Indianapolis)". When
# present, sleeping exactly until then (+ a small buffer) beats guessing
# $RESET_SLEEP_SECS. This scrapes a human-facing UI string, not a documented
# API contract, so any parse failure below must fall back silently (exit 1,
# no output) rather than error the loop.
parse_reset_sleep_secs() {
  local out_file="$1" line time_str tz target_epoch now_epoch
  line="$(grep -oE 'resets [0-9]{1,2}:[0-9]{2}(am|pm) \([^)]+\)' "$out_file" 2>/dev/null | tail -1)" || return 1
  [[ -z "$line" ]] && return 1
  time_str="$(sed -E 's/resets ([0-9]{1,2}:[0-9]{2}(am|pm)) \(([^)]+)\)/\1/' <<<"$line")"
  tz="$(sed -E 's/resets ([0-9]{1,2}:[0-9]{2}(am|pm)) \(([^)]+)\)/\3/' <<<"$line")"
  [[ -z "$time_str" || -z "$tz" ]] && return 1
  target_epoch="$(TZ="$tz" date -d "$time_str" +%s 2>/dev/null)" || return 1
  [[ -z "$target_epoch" ]] && return 1
  now_epoch="$(date +%s)"
  [[ "$target_epoch" -le "$now_epoch" ]] && target_epoch=$((target_epoch + 86400))
  echo $((target_epoch - now_epoch + 120))
}

if [[ "$(id -u)" == "0" ]] && [[ -z "${IS_SANDBOX:-}" ]]; then
  log "running as root — exporting IS_SANDBOX=1 (see header comment) so --dangerously-skip-permissions doesn't hard-fail"
  export IS_SANDBOX=1
fi

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

  log "invoking claude -p (model=$MODEL, worker_mode=$WORKER_MODE) — output -> $out_file"
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
    log "invocation failed (exit $exit_code) — see $out_file.stderr"

    if reset_wait="$(parse_reset_sleep_secs "$out_file")" && [[ "$reset_wait" -gt 0 ]]; then
      log "parsed an exact usage-window reset time from the output — sleeping ${reset_wait}s (skips the consecutive-failure guess entirely)"
      interruptible_sleep "$reset_wait"
      consec_fails=0
      continue
    fi

    consec_fails=$((consec_fails + 1))
    log "no exact reset time found (consecutive=$consec_fails)"

    if [[ $consec_fails -ge $CONSEC_FAIL_THRESHOLD ]]; then
      log "treating repeated failure as the usage-window limit — sleeping ${RESET_SLEEP_SECS}s"
      interruptible_sleep "$RESET_SLEEP_SECS"
      consec_fails=0
    else
      log "single failure — short backoff ${SHORT_BACKOFF_SECS}s in case it's transient"
      interruptible_sleep "$SHORT_BACKOFF_SECS"
    fi
    continue
  fi

  if [[ -f "$HALT_FILE" ]]; then
    log "fleet wrote $HALT_FILE — no eligible work, stopping loop"
    break
  fi

  interruptible_sleep "$BETWEEN_RUNS_SECS"
done

rm -f "$PID_FILE"
log "overnight runner exited"
