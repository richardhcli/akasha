#!/usr/bin/env bash
# Overnight unattended fleet-dispatch loop.
#
# Repeatedly invokes headless `claude -p` against overnight_prompt.md until
# one of these end conditions is reached (first one wins — they're OR'd):
#   - a stop file appears (scripts/fleet/.stop, see stop_overnight.sh)
#   - a hard wall-clock duration ceiling elapses (default 12h, see
#     OVERNIGHT_MAX_RUNTIME_SECS below)
#   - a specific wall-clock time of day arrives (see OVERNIGHT_END_TIME
#     below) — unset by default, so only the duration ceiling applies
#     unless you opt in
# On any of these, the loop never interrupts a `claude -p` call already in
# flight — it only stops *starting new ones* once its current cohort's
# work is committed, then dispatches exactly one extra "wrap-up" invocation
# (see run_wrap_up_invocation below) that writes a human-readable summary
# of the run to docs instead of dispatching more work. It also stops (with
# no wrap-up call) if the fleet reports no eligible work across several
# rescans in a row with no change to task-status.md in between — see "Halt
# handling" below; a single halt does not end the run, since a human may
# add goals to docs/agents/overnight-goals.md or docs/build-plan.md
# mid-run. On any failed invocation (including the account's rolling
# usage-window limit — there is no documented pre-flight way to query
# that, so this treats "the call failed" as the signal) it backs off: a
# short retry for what might be a transient blip, escalating to a
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
#   nohup scripts/fleet/overnight_runner.sh > /dev/null 2>&1 &
#   disown
#   # (the script writes its own durable log to docs/agents/logs/
#   # overnight-runner.log directly — see "Logging" below — so nothing
#   # needs to redirect its stdout for durability; redirect only matters
#   # if you want the launching shell to stop echoing to your terminal)
#   # or: tmux new -d -s fleet-overnight scripts/fleet/overnight_runner.sh
#
# Stop:
#   scripts/fleet/stop_overnight.sh    # or: touch scripts/fleet/.stop
#
# Tuning env vars (all optional):
#   OVERNIGHT_MODEL              driving loop model (default: sonnet)
#   OVERNIGHT_WORKER_MODE        claude-only | hybrid (default: claude-only)
#   OVERNIGHT_MAX_RUNTIME_SECS   hard wall-clock DURATION ceiling from start
#                                 (default: 43200 = 12h)
#   OVERNIGHT_END_TIME           hard wall-clock TIME-OF-DAY ceiling, 24h
#                                 "HH:MM" (e.g. "09:00") — parsed via
#                                 `date -d`; if that time has already
#                                 passed today it means tomorrow, not today.
#                                 Unset by default (only the duration
#                                 ceiling applies). Whichever of this and
#                                 OVERNIGHT_MAX_RUNTIME_SECS elapses first
#                                 wins.
#   OVERNIGHT_END_TIME_TZ        IANA zone for OVERNIGHT_END_TIME (e.g.
#                                 "America/New_York"); default: system
#                                 local time.
#   OVERNIGHT_MIN_SLOT_SECS      don't start a new invocation with less than
#                                 this much runtime budget left (default: 1800 = 30m)
#   OVERNIGHT_HALT_RECHECK_SECS  sleep between rescans after a "no eligible
#                                 work" halt, in case goals change (default: 1800 = 30m)
#   OVERNIGHT_HALT_RETRY_LIMIT   consecutive halts with an unchanged
#                                 task-status.md before giving up for real (default: 3)
#   OVERNIGHT_RESET_SECS         fallback sleep when a rate-limit failure's
#                                 exact reset time can't be parsed (default: 5h)
#   OVERNIGHT_SHORT_BACKOFF_SECS sleep after a single (possibly transient) failure (default: 60)
#   OVERNIGHT_BETWEEN_RUNS_SECS  pause between back-to-back successful invocations (default: 15)
#   OVERNIGHT_FAIL_THRESHOLD     consecutive failures before assuming the usage window, not a blip (default: 2)
#   OVERNIGHT_WRAPUP_TIMEOUT_SECS hard cap on the final wrap-up invocation
#                                 itself (default: 1200 = 20m) — without
#                                 this, a hung wrap-up call would run past
#                                 any of the three end conditions
#                                 indefinitely, since it's deliberately not
#                                 gated by the deadline/min-slot checks the
#                                 way normal invocations are.
#
# Logging (see docs/agents/overnight-guide.md for the human-facing version):
#   - docs/agents/logs/overnight-runner.log: one line per lifecycle event,
#     written by log() below via a fresh `>>` open-by-path on every call —
#     deliberately NOT held open for the process lifetime and deliberately
#     NOT piped through an external `tee`. Both matter: on 2026-07-25 this
#     file WAS tracked in git (like the rest of docs/agents/logs/ still
#     is), and something mid-run — plausibly a cohort commit's git
#     operations, or an interactive `git checkout`/`stash` — replaced it
#     with a new inode. A long-lived fd (an interior `exec >>` redirect,
#     or the `tee -a` start_overnight.sh used to pipe through) keeps
#     writing into the old, now-unlinked inode once that happens — the
#     writes still "succeed" but silently vanish. The log went dark for
#     two invocations and a halt that night, even though all of it
#     actually completed and committed fine. The fix is two-layered: this
#     file is now gitignored (see .gitignore) so nothing should replace
#     its inode at all, and reopening by path on every log() call means
#     that even if something did, a mid-run inode swap would cost at most
#     the log lines written between swaps, not everything after.
#   - docs/agents/logs/overnight-runner-last-exit.md: overwritten on every
#     exit (normal or abnormal, via the EXIT trap below) with why the loop
#     stopped and basic run stats. Check this first thing in the morning.
#     Caveat confirmed against a real tmux session: bash defers running a
#     trapped signal's handler until the current foreground wait returns —
#     a SIGINT/SIGTERM/SIGHUP (e.g. `tmux kill-session`) arriving while
#     blocked in an interruptible_sleep chunk writes this file within
#     ~30s, but one arriving mid-`claude -p` only writes it once that
#     invocation itself exits. A genuinely hung invocation therefore still
#     won't self-report quickly just because this file exists.
#   - docs/agents/logs/OVERNIGHT_SUMMARY.<timestamp>.md: written by the
#     wrap-up invocation itself (an agent, not this script) when a planned
#     end condition — stop file, duration ceiling, or OVERNIGHT_END_TIME —
#     is reached. Unlike overnight-runner-last-exit.md (mechanical stats)
#     this is a narrative account of what got done, current
#     task-status.md state, and suggested next steps. Not written for a
#     halt-retry-limit exit — that path already has its own agent-authored
#     explanation in OVERNIGHT_HALT.*.md.
#   - docs/agents/logs/OVERNIGHT_HALT.md: written by the driving claude -p
#     session itself (see overnight_prompt.md), not by this script, when a
#     given invocation finds no eligible work. This script archives it
#     with a timestamp each time it's seen (see "Halt handling" below) so
#     it never silently blocks the next rescan from being noticed.
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
WRAPUP_PROMPT_FILE="scripts/fleet/overnight_wrapup_prompt.md"
STOP_FILE="scripts/fleet/.stop"
HALT_FILE="docs/agents/logs/OVERNIGHT_HALT.md"
LOG_DIR="docs/agents/logs"
LOG_FILE="$LOG_DIR/overnight-runner.log"
EXIT_SUMMARY_FILE="$LOG_DIR/overnight-runner-last-exit.md"
PID_FILE="scripts/fleet/.overnight_runner.pid"
TASK_STATUS_FILE="docs/agents/task-status.md"

MODEL="${OVERNIGHT_MODEL:-sonnet}"
RESET_SLEEP_SECS="${OVERNIGHT_RESET_SECS:-$((5 * 3600))}"   # ~5h rolling usage window
SHORT_BACKOFF_SECS="${OVERNIGHT_SHORT_BACKOFF_SECS:-60}"
BETWEEN_RUNS_SECS="${OVERNIGHT_BETWEEN_RUNS_SECS:-15}"
CONSEC_FAIL_THRESHOLD="${OVERNIGHT_FAIL_THRESHOLD:-2}"       # fails in a row before assuming it's the usage window, not a blip
MAX_RUNTIME_SECS="${OVERNIGHT_MAX_RUNTIME_SECS:-$((12 * 3600))}"  # hard duration ceiling regardless of stop file / halt state
MIN_SLOT_SECS="${OVERNIGHT_MIN_SLOT_SECS:-1800}"                  # don't start an invocation we can't let finish
HALT_RECHECK_SECS="${OVERNIGHT_HALT_RECHECK_SECS:-1800}"          # sleep between rescans after "no eligible work"
HALT_RETRY_LIMIT="${OVERNIGHT_HALT_RETRY_LIMIT:-3}"               # consecutive unchanged-state halts before giving up
END_TIME="${OVERNIGHT_END_TIME:-}"                                # optional "HH:MM" wall-clock ceiling
END_TIME_TZ="${OVERNIGHT_END_TIME_TZ:-}"                          # optional IANA zone for END_TIME
WRAPUP_TIMEOUT_SECS="${OVERNIGHT_WRAPUP_TIMEOUT_SECS:-1200}"      # hard cap on the final wrap-up invocation itself

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

mkdir -p "$LOG_DIR"

START_EPOCH=$(date +%s)
INVOCATION_COUNT=0
SUCCESS_COUNT=0
FAIL_COUNT=0
WRAPUP_STATUS=""
EXIT_REASON="unknown — script exited without setting a reason (likely a bug or an unhandled signal)"
# DEADLINE_EPOCH/DEADLINE_KIND are resolved further down (need END_TIME
# parsing, which needs log()/the EXIT trap to already exist so a bad value
# can fail loudly) — interruptible_sleep/deadline_reached below reference
# the variable by name, not its value, so defining them first is fine.

# Reopens the log file by path on every call — see the header comment's
# "Logging" section for why this must not be a long-lived fd or an
# external `tee`. Also echoes to stdout so `tmux attach` shows it live.
log() {
  local line
  line="$(date -u +%Y-%m-%dT%H:%M:%SZ) $1"
  printf '%s\n' "$line"
  printf '%s\n' "$line" >> "$LOG_FILE"
}

write_exit_summary() {
  local now_epoch elapsed_secs elapsed_h ceiling_h
  now_epoch=$(date +%s)
  elapsed_secs=$((now_epoch - START_EPOCH))
  elapsed_h="$(awk -v s="$elapsed_secs" 'BEGIN { printf "%.2f", s / 3600 }')"
  ceiling_h="$(awk -v s="$MAX_RUNTIME_SECS" 'BEGIN { printf "%.1f", s / 3600 }')"
  cat > "$EXIT_SUMMARY_FILE" <<EOF
# Overnight runner: last exit

- **Reason:** $EXIT_REASON
- **Started:** $(date -u -d "@$START_EPOCH" +%Y-%m-%dT%H:%M:%SZ)
- **Exited:** $(date -u -d "@$now_epoch" +%Y-%m-%dT%H:%M:%SZ)
- **Elapsed:** ${elapsed_secs}s (~${elapsed_h}h) of a ${MAX_RUNTIME_SECS}s (~${ceiling_h}h) duration ceiling${END_TIME:+, or end time $END_TIME (whichever came first)}
- **Invocations this run:** $INVOCATION_COUNT ($SUCCESS_COUNT ok, $FAIL_COUNT failed)
- **Wrap-up summary invocation:** ${WRAPUP_STATUS:-not attempted (see reason above)}
- **PID:** $$

This file is overwritten on every exit path (stop file, halt-retry-limit,
runtime ceiling, or an abnormal exit/signal) — check it first. Note: if the
process was killed (Ctrl-C, \`kill\`, \`tmux kill-session\`) while a
\`claude -p\` invocation was still running, this file isn't written until
that invocation itself exits — bash defers signal handling until the
current foreground wait returns, so a genuinely hung invocation delays
this file too. See docs/agents/logs/overnight-runner.log for the full
per-invocation timeline, docs/agents/logs/OVERNIGHT_SUMMARY.*.md for the
agent-authored end-of-run summary (if a wrap-up invocation ran), and
docs/agents/logs/OVERNIGHT_HALT.*.md for the fleet's own reasoning on any
"no eligible work" halt.
EOF
}

# Registered before any fatal-exit-capable check below (including the
# OVERNIGHT_END_TIME validation) so every one of them gets a proper exit
# summary + pid cleanup, not just the two that historically came last.
# Fires on any exit — normal `break`/end-of-script, a bare `exit`, an
# unset-variable error under `set -u`, or a signal. HUP matters
# specifically: `tmux kill-session` (documented in overnight-guide.md as
# the force-kill path) sends SIGHUP to the pane's process, not SIGTERM —
# without trapping it, bash's default HUP disposition terminates the
# script with no EXIT trap run at all, leaving a stale pid file and no
# exit summary.
on_exit() {
  rm -f "$PID_FILE"
  write_exit_summary
  log "overnight runner exited ($EXIT_REASON)"
}
trap on_exit EXIT
trap 'EXIT_REASON="terminated by SIGINT"; exit 130' INT
trap 'EXIT_REASON="terminated by SIGTERM"; exit 143' TERM
trap 'EXIT_REASON="terminated by SIGHUP (e.g. tmux kill-session)"; exit 129' HUP

echo $$ > "$PID_FILE"

# Pre-flight: fail loudly and immediately rather than looping forever on a
# broken invocation. Without the file-existence checks, a missing
# PROMPT_FILE would make `$(cat "$PROMPT_FILE")` silently expand to an
# empty string and the loop would happily call `claude -p ""` every ~15s
# for the full runtime ceiling, "succeeding" each time and doing nothing.
if ! command -v claude >/dev/null 2>&1; then
  log "FATAL: 'claude' not found on PATH — cannot run the loop"
  EXIT_REASON="fatal: claude CLI not on PATH"
  exit 1
fi
if [[ ! -f "$PROMPT_FILE" ]]; then
  log "FATAL: prompt file $PROMPT_FILE does not exist"
  EXIT_REASON="fatal: missing $PROMPT_FILE"
  exit 1
fi
if [[ ! -f "$WRAPUP_PROMPT_FILE" ]]; then
  log "FATAL: wrap-up prompt file $WRAPUP_PROMPT_FILE does not exist"
  EXIT_REASON="fatal: missing $WRAPUP_PROMPT_FILE"
  exit 1
fi

# Resolves OVERNIGHT_END_TIME to an absolute epoch that's always in the
# future relative to $START_EPOCH (rolling forward to tomorrow if that
# time-of-day already passed today). Returns non-zero with empty stdout on
# a parse failure — it deliberately does NOT log/exit itself: it's called
# via `$(...)` command substitution below, and `exit` inside a command
# substitution only kills that subshell, not the script, silently handing
# the caller whatever partial text was on stdout at that point (verified
# the hard way: an earlier version of this function called `log` and
# `exit` here, and on a bad OVERNIGHT_END_TIME the FATAL log line's own
# text — a colon-laden timestamp string — got captured as "the epoch" and
# blew up as garbage input to a later `(( ... ))` comparison instead of
# ever stopping the script). The caller checks the exit status and does
# the actual fatal exit itself, in the main shell, where `exit` works.
resolve_end_time_epoch() {
  local epoch
  if [[ -n "$END_TIME_TZ" ]]; then
    epoch="$(TZ="$END_TIME_TZ" date -d "$END_TIME" +%s 2>/dev/null)" || return 1
  else
    epoch="$(date -d "$END_TIME" +%s 2>/dev/null)" || return 1
  fi
  [[ -z "$epoch" ]] && return 1
  if (( epoch <= START_EPOCH )); then
    epoch=$((epoch + 86400))
  fi
  echo "$epoch"
}

END_TIME_DEADLINE_EPOCH=""
if [[ -n "$END_TIME" ]]; then
  if ! END_TIME_DEADLINE_EPOCH="$(resolve_end_time_epoch)"; then
    log "FATAL: OVERNIGHT_END_TIME='$END_TIME' could not be parsed — expected 24h \"HH:MM\" (e.g. \"09:00\"), optionally with OVERNIGHT_END_TIME_TZ set to an IANA zone"
    EXIT_REASON="fatal: invalid OVERNIGHT_END_TIME"
    exit 1
  fi
fi

DURATION_DEADLINE_EPOCH=$((START_EPOCH + MAX_RUNTIME_SECS))

# Effective ceiling is whichever of the duration ceiling and the
# time-of-day ceiling comes first — DEADLINE_KIND records which one, for
# clear logging.
if [[ -n "$END_TIME_DEADLINE_EPOCH" ]] && (( END_TIME_DEADLINE_EPOCH < DURATION_DEADLINE_EPOCH )); then
  DEADLINE_EPOCH=$END_TIME_DEADLINE_EPOCH
  DEADLINE_KIND="end time ${END_TIME}${END_TIME_TZ:+ ($END_TIME_TZ)} ($(date -u -d "@$END_TIME_DEADLINE_EPOCH" +%Y-%m-%dT%H:%M:%SZ))"
else
  DEADLINE_EPOCH=$DURATION_DEADLINE_EPOCH
  DEADLINE_KIND="max runtime (${MAX_RUNTIME_SECS}s)"
fi

# Plain `sleep N` for a multi-hour backoff would make `stop_overnight.sh` /
# `touch .stop` sit ignored for up to ~5h (only checked at the top of the
# `while true` loop), and would also let a long sleep blow straight through
# the runtime ceiling. Chunk it so both are re-checked every 30s.
interruptible_sleep() {
  local remaining="$1"
  while (( remaining > 0 )); do
    if [[ -f "$STOP_FILE" ]]; then
      return 0
    fi
    if (( $(date +%s) >= DEADLINE_EPOCH )); then
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

deadline_reached() {
  (( $(date +%s) >= DEADLINE_EPOCH ))
}

# Dispatched exactly once, right before the loop actually ends, for the
# three planned end conditions (stop file, duration ceiling, end-of-day
# time) — never for a halt-retry-limit exit (which already has its own
# agent-authored OVERNIGHT_HALT.*.md explanation) and never from inside a
# signal trap (an operator sending SIGINT/TERM/HUP wants the process gone,
# not one more multi-minute claude -p call started on their way out).
# Unlike the normal loop invocation, this one is NOT gated by
# MIN_SLOT_SECS/the deadline — by definition we're already stopping, and
# letting the agent actually write the summary is the whole point, even if
# it runs a little past the nominal ceiling. It IS wrapped in `timeout`,
# though (OVERNIGHT_WRAPUP_TIMEOUT_SECS, default 20m): without a cap, a
# hung wrap-up call would run past OVERNIGHT_END_TIME indefinitely, which
# would make "always done by 9am" a documentation claim rather than a real
# guarantee. A `claude -p` invocation killed by `timeout` here just means
# the exit-summary reports "failed" and the narrative
# OVERNIGHT_SUMMARY.*.md never got written — a worse morning-after than a
# clean summary, but a bounded one.
run_wrap_up_invocation() {
  local reason="$1" ts out_file wrapup_prompt started_iso
  started_iso="$(date -u -d "@$START_EPOCH" +%Y-%m-%dT%H:%M:%SZ)"
  log "end condition reached ($reason) — dispatching one final wrap-up invocation (capped at ${WRAPUP_TIMEOUT_SECS}s) to summarize progress before stopping"
  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  out_file="$LOG_DIR/overnight-invocation-$ts-wrapup.json"
  wrapup_prompt="$(cat "$WRAPUP_PROMPT_FILE")

---
## Run context (from scripts/fleet/overnight_runner.sh)
- End condition: $reason
- Run started: $started_iso
- Invocations this run before this one: $INVOCATION_COUNT ($SUCCESS_COUNT ok, $FAIL_COUNT failed)"
  if timeout "$WRAPUP_TIMEOUT_SECS" claude -p "$wrapup_prompt" \
      --model "$MODEL" \
      --output-format json \
      --dangerously-skip-permissions \
      --disallowedTools "Bash(git push --force*)" "Bash(git push -f*)" \
                         "Bash(git reset --hard*)" "Bash(git rebase*)" \
                         "Bash(git filter-branch*)" "Bash(git commit --amend*)" \
                         "Bash(git branch -D*)" "Bash(git clean -f*)" \
      > "$out_file" 2> "$out_file.stderr"; then
    log "wrap-up invocation ok — see $out_file"
    WRAPUP_STATUS="ok ($out_file)"
  else
    local wc=$?
    local wc_note=""
    [[ "$wc" == "124" ]] && wc_note=" (124 = timed out after ${WRAPUP_TIMEOUT_SECS}s)"
    log "wrap-up invocation failed (exit $wc$wc_note) — see $out_file.stderr (exiting anyway; the mechanical summary at $EXIT_SUMMARY_FILE still gets written)"
    WRAPUP_STATUS="failed, exit $wc$wc_note ($out_file.stderr)"
  fi
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

# A stop file left over from before this process started is either stale
# (a previous run already honored it and exited) or a genuine "don't start"
# signal raised while nothing was running. This script's own invocation is
# an explicit "run now" request, so it wins — but log it either way rather
# than silently discarding what might have been a deliberate stop.
if [[ -f "$STOP_FILE" ]]; then
  log "clearing a pre-existing stop file ($STOP_FILE) before starting — if you didn't mean to start, run stop_overnight.sh again"
  rm -f "$STOP_FILE"
fi

log "starting (deadline: $DEADLINE_KIND, min slot: ${MIN_SLOT_SECS}s, halt recheck: ${HALT_RECHECK_SECS}s x${HALT_RETRY_LIMIT})"

consec_fails=0
consec_halts=0
last_halt_hash=""

while true; do
  if [[ -f "$STOP_FILE" ]]; then
    log "stop file present ($STOP_FILE) — exiting"
    rm -f "$STOP_FILE"
    EXIT_REASON="stop file requested"
    run_wrap_up_invocation "$EXIT_REASON"
    break
  fi

  if deadline_reached; then
    log "$DEADLINE_KIND reached — exiting (no stop file was found; this is an automatic ceiling)"
    EXIT_REASON="$DEADLINE_KIND reached"
    run_wrap_up_invocation "$EXIT_REASON"
    break
  fi

  remaining_budget=$((DEADLINE_EPOCH - $(date +%s)))
  if (( remaining_budget < MIN_SLOT_SECS )); then
    log "only ${remaining_budget}s left before $DEADLINE_KIND (need >=${MIN_SLOT_SECS}s for a new invocation) — exiting cleanly instead of starting one we'd have to cut off"
    EXIT_REASON="$DEADLINE_KIND approaching (${remaining_budget}s left, below OVERNIGHT_MIN_SLOT_SECS=${MIN_SLOT_SECS}s)"
    run_wrap_up_invocation "$EXIT_REASON"
    break
  fi

  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  out_file="$LOG_DIR/overnight-invocation-$ts.json"

  log "invoking claude -p (model=$MODEL, worker_mode=$WORKER_MODE) — output -> $out_file"
  INVOCATION_COUNT=$((INVOCATION_COUNT + 1))
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
    SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
    consec_fails=0
  else
    exit_code=$?
    FAIL_COUNT=$((FAIL_COUNT + 1))
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
    # A halt means "no eligible work as of this scan," not "no work will
    # ever exist again" — a human can add goals to overnight-goals.md or
    # build-plan.md while this loop sleeps. Only give up for real once the
    # thing the scan depends on (task-status.md) has gone stale across
    # several consecutive halts.
    status_hash="$(sha256sum "$TASK_STATUS_FILE" 2>/dev/null | awk '{print $1}')"
    if [[ "$status_hash" == "$last_halt_hash" ]]; then
      consec_halts=$((consec_halts + 1))
    else
      consec_halts=1
      last_halt_hash="$status_hash"
    fi

    archived="$LOG_DIR/OVERNIGHT_HALT.$(date -u +%Y%m%dT%H%M%SZ).md"
    mv "$HALT_FILE" "$archived"

    if (( consec_halts >= HALT_RETRY_LIMIT )); then
      log "fleet found no eligible work $consec_halts times in a row with $TASK_STATUS_FILE unchanged — giving up (see $archived)"
      EXIT_REASON="no eligible work after $consec_halts consecutive rescans (task-status.md unchanged) — see $archived"
      break
    fi

    log "fleet wrote $HALT_FILE (halt #$consec_halts/$HALT_RETRY_LIMIT for this task-status.md state, archived to $archived) — sleeping ${HALT_RECHECK_SECS}s and rescanning in case goals change"
    interruptible_sleep "$HALT_RECHECK_SECS"
    continue
  fi

  interruptible_sleep "$BETWEEN_RUNS_SECS"
done
