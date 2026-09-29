#!/usr/bin/env python3
"""Unattended headless-Claude runner and job launcher, built to be watched safely.

The processes that do the work never write to a terminal; they write logs by path. Terminals
only *view* those logs, so closing or Ctrl-C'ing a viewer pane cannot stop a run.

  runner.py run    --start now|<epoch>     the self-resuming `claude -p` loop (tmux session `kbio`)
  runner.py view                           live, human-readable view of the current attempt
  runner.py status                         one screen: runner state, stall warning, every job
  runner.py render FILE.jsonl              render a finished attempt
  runner.py job start NAME [--log P] -- CMD...   long job in tmux session `kbio-job-NAME`
  runner.py job stop NAME | job list

Stop the runner: `touch <logs>/STOP` (checked every 10 s, also while sleeping), or Ctrl-C in
its pane. The first Ctrl-C stops after the current attempt; a second one ends the attempt now.
A job keeps running when its pane gets Ctrl-C or its tmux session is killed; use `job stop`.
Watch read-only: `tmux attach -r -t '=kbio-view'`.

Stdlib only; Python 3.10+.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
EXP = Path(__file__).resolve().parents[1]
LOGS = REPO / "data" / "experiments" / "kb-io-bench" / "agent-logs"
JOBS = REPO / "data" / "experiments" / "kb-io-bench" / "jobs"
CLAUDE = Path.home() / ".local" / "bin" / "claude"
STALL_S = 15 * 60


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def hhmm(ts: float | None) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else "-"


def write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(path)


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:  # a zombie (exited, not yet reaped) is not alive
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return True


# ---------------------------------------------------------------- rendering
def _short(s: object, n: int = 160) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _tool_label(name: str, inp: dict) -> str:
    for key in ("description", "file_path", "pattern", "path", "url", "query", "prompt", "command"):
        if inp.get(key):
            return f"{name}: {_short(inp[key], 140)}"
    return f"{name}: {_short(json.dumps(inp), 140)}"


def render_event(e: dict) -> list[str]:
    """Human lines for one stream-json event (empty list = nothing worth showing)."""
    t = e.get("type")
    if t == "system" and e.get("subtype") == "init":
        return [f"session {e.get('session_id')} model {e.get('model')} cwd {e.get('cwd')}"]
    if t == "assistant":
        out = []
        for c in e.get("message", {}).get("content", []):
            if c.get("type") == "text" and c.get("text", "").strip():
                out.append(f"  says: {_short(c['text'], 300)}")
            elif c.get("type") == "tool_use":
                out.append(f"  -> {_tool_label(c.get('name', '?'), c.get('input') or {})}")
        return out
    if t == "user":
        out = []
        for c in e.get("message", {}).get("content", []):
            if isinstance(c, dict) and c.get("type") == "tool_result":
                body = c.get("content")
                if isinstance(body, list):
                    body = " ".join(x.get("text", "") for x in body if isinstance(x, dict))
                size = len(str(body or ""))
                if c.get("is_error"):
                    out.append(f"     <- ERROR {_short(body, 200)}")
                else:
                    out.append(f"     <- ok ({size} chars) {_short(body, 90)}")
        return out
    if t == "rate_limit_event":
        info = e.get("rate_limit_info", {})
        util = info.get("unifiedWindows", {}).get("five_hour", {}).get("utilization")
        if info.get("status") != "allowed" or (util is not None and util >= 0.8):
            return [
                f"  !! rate limit {info.get('status')} 5h={util} "
                f"resets {hhmm(info.get('resetsAt'))}"
            ]
        return []
    if t == "result":
        return [
            f"== result {e.get('subtype')} error={e.get('is_error')} turns={e.get('num_turns')} "
            f"cost=${e.get('total_cost_usd') or 0:.2f} {int((e.get('duration_ms') or 0) / 1000)}s",
            f"   {_short(e.get('result', ''), 600)}",
        ]
    return []


def iter_events(path: Path):
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue


RESET_TEXT = re.compile(r"resets? (\d{1,2})(?::(\d{2}))?\s*([ap]m)(?:\s*\(([^)]+)\))?", re.I)


def reset_from_text(text: str) -> float | None:
    """'... resets 1:30am (America/Indiana/Indianapolis)' -> the next such moment (epoch)."""
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    m = RESET_TEXT.search(text or "")
    if not m:
        return None
    hour = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "pm" else 0)
    try:
        tz = ZoneInfo(m.group(4)) if m.group(4) else None
    except Exception:
        tz = None
    cur = datetime.now(tz)
    t = cur.replace(hour=hour, minute=int(m.group(2) or 0), second=0, microsecond=0)
    if t <= cur:
        t += timedelta(days=1)
    return t.timestamp()


def summarize(path: Path) -> dict:
    """What the loop needs from a finished attempt: outcome, session, and any rate-limit wall."""
    s: dict = {
        "session_id": None,
        "result": None,
        "is_error": None,
        "limited_until": None,
        "util_5h": None,
        "turns": None,
        "cost": None,
    }
    if not path.exists():
        return s
    for e in iter_events(path):
        s["session_id"] = e.get("session_id") or s["session_id"]
        if e.get("type") == "rate_limit_event":
            info = e.get("rate_limit_info", {})
            s["util_5h"] = info.get("unifiedWindows", {}).get("five_hour", {}).get("utilization")
            if info.get("status") not in (None, "allowed", "allowed_warning"):
                s["limited_until"] = info.get("resetsAt")
        if e.get("type") == "result":
            s.update(
                result=_short(e.get("result", ""), 400),
                is_error=e.get("is_error"),
                turns=e.get("num_turns"),
                cost=e.get("total_cost_usd"),
            )
    if s["is_error"] and not s["limited_until"] and RESET_TEXT.search(s["result"] or ""):
        # limit hit with no structured event: use the stated reset time, else retry in 1 h
        s["limited_until"] = reset_from_text(s["result"]) or time.time() + 3600
    return s


# ---------------------------------------------------------------- runner loop
class Runner:
    def __init__(self, a: argparse.Namespace) -> None:
        self.a = a
        self.logs = Path(a.logs)
        self.logs.mkdir(parents=True, exist_ok=True)
        self.state_path = self.logs / "runner-state.json"
        self.state: dict = {
            "pid": os.getpid(),
            "phase": "starting",
            "attempt": 0,
            "max_attempts": a.max_attempts,
            "started_at": time.time(),
        }
        self.stop_requested = False
        self.child: subprocess.Popen | None = None
        signal.signal(signal.SIGINT, self.on_signal)
        signal.signal(signal.SIGTERM, self.on_signal)
        signal.signal(signal.SIGHUP, self.on_signal)

    def log(self, msg: str) -> None:
        line = f"[{now()}] {msg}"
        with (self.logs / "runner.log").open("a") as f:  # reopened by path on every line
            f.write(line + "\n")
        print(line, flush=True)

    def save(self, **kw: object) -> None:
        self.state.update(kw, updated_at=time.time())
        write_json(self.state_path, self.state)

    def on_signal(self, signum: int, _frame: object) -> None:
        name = signal.Signals(signum).name
        if not self.stop_requested:
            self.stop_requested = True
            self.log(
                f"{name} received: stopping after the current attempt "
                f"(send it again to end the attempt now)"
            )
        elif self.child and self.child.poll() is None:
            self.log(f"second {name}: terminating the running attempt")
            os.killpg(self.child.pid, signal.SIGTERM)

    def should_stop(self) -> str | None:
        if self.stop_requested:
            return "signal"
        stop = self.logs / "STOP"
        if stop.exists():  # consumed, so the next arm does not stop at once
            stop.rename(self.logs / f"STOP.consumed-{datetime.now():%Y%m%d-%H%M%S}")
            return "STOP file (renamed to STOP.consumed-*)"
        status = Path(self.a.status)
        text = status.read_text() if status.exists() else ""
        if any(line.strip() == self.a.done_line for line in text.splitlines()):
            return "done line in status file"
        if any(line.startswith(self.a.blocked_prefix) for line in text.splitlines()):
            return "blocked line in status file"
        return None

    def sleep_until(self, t: float, why: str) -> str | None:
        self.save(phase="sleeping", next_wake_at=t, sleep_reason=why)
        self.log(f"sleeping until {hhmm(t)} ({why})")
        while time.time() < t:
            if r := self.should_stop():
                return r
            time.sleep(min(10, max(0.1, t - time.time())))
        return self.should_stop()

    def attempt(self, n: int) -> dict:
        sid = str(uuid.uuid4())
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        raw = self.logs / f"attempt-{stamp}.jsonl"
        cmd = [
            str(self.a.claude),
            "-p",
            Path(self.a.prompt).read_text(),
            "--dangerously-skip-permissions",
            "--output-format",
            "stream-json",
            "--verbose",
            "--session-id",
            sid,
        ]
        if self.a.model:
            cmd += ["--model", self.a.model]
        self.log(f"attempt {n}/{self.a.max_attempts} session {sid} -> {raw.name}")
        t0 = time.time()
        self.save(
            phase="running",
            attempt=n,
            session_id=sid,
            attempt_log=str(raw),
            attempt_started_at=t0,
            next_wake_at=None,
        )
        with raw.open("w") as out, (self.logs / f"attempt-{stamp}.err").open("w") as err:
            # own session: a Ctrl-C in this pane reaches only the runner, never Claude
            self.child = subprocess.Popen(
                cmd, cwd=self.a.cwd, stdout=out, stderr=err, start_new_session=True
            )
            while self.child.poll() is None:
                self.save(last_event_at=raw.stat().st_mtime, raw_bytes=raw.stat().st_size)
                if time.time() - t0 > self.a.attempt_timeout:
                    self.log(f"attempt exceeded {self.a.attempt_timeout}s; terminating")
                    os.killpg(self.child.pid, signal.SIGTERM)
                time.sleep(5)
        rc = self.child.returncode
        self.child = None
        s = summarize(raw)
        (self.logs / f"attempt-{stamp}.log").write_text(
            "\n".join(line for e in iter_events(raw) for line in render_event(e)) + "\n"
        )
        self.log(
            f"attempt {n} exited rc={rc} in {int(time.time() - t0)}s turns={s['turns']} "
            f"cost={s['cost']} error={s['is_error']}: {s['result']}"
        )
        self.save(
            last_result={**s, "rc": rc, "seconds": int(time.time() - t0)}, util_5h=s["util_5h"]
        )
        return s

    def run(self) -> int:
        import fcntl

        lock = (self.logs / "runner.lock").open("w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("another runner holds the lock", file=sys.stderr)
            return 1
        start = time.time() if self.a.start == "now" else float(self.a.start)
        self.log(
            f"runner armed (pid {os.getpid()}); first attempt at {hhmm(start)}; "
            f"max {self.a.max_attempts} attempts, every {self.a.interval}s"
        )
        if r := self.sleep_until(start, "scheduled start"):
            return self.finish(r)
        for n in range(1, self.a.max_attempts + 1):
            if r := self.should_stop():
                return self.finish(r)
            s = self.attempt(n)
            if s["limited_until"]:
                wake, why = s["limited_until"] + 120, "usage limit; resuming after the reset"
            else:
                wake, why = time.time() + self.a.interval, "normal interval"
            if n < self.a.max_attempts and (r := self.sleep_until(wake, why)):
                return self.finish(r)
        return self.finish("max attempts reached")

    def finish(self, reason: str) -> int:
        self.log(f"runner stopped: {reason}")
        self.save(phase="stopped", stop_reason=reason, next_wake_at=None)
        return 0


# ---------------------------------------------------------------- viewer / status
def cmd_view(a: argparse.Namespace) -> int:
    logs = Path(a.logs)
    signal.signal(
        signal.SIGINT,
        lambda *_: (print("\n(viewer closed; the runner is unaffected)"), sys.exit(0)),
    )
    current: Path | None = None
    fh = None
    buf = ""
    last_hdr = 0.0
    while True:
        files = sorted(logs.glob("attempt-*.jsonl"))
        if files and files[-1] != current:
            current = files[-1]
            fh = current.open()
            buf = ""
            print(f"\n######## {current.name} ########", flush=True)
        if time.time() - last_hdr > 60:
            print(f"[{now()}] {status_line(logs)}", flush=True)
            last_hdr = time.time()
        if fh:
            chunk = fh.read()
            if chunk:
                buf += chunk
                *lines, buf = buf.split("\n")
                for line in lines:
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    for out in render_event(e):
                        print(f"[{datetime.now():%H:%M:%S}] {out}", flush=True)
        time.sleep(1)


def status_line(logs: Path) -> str:
    st = read_json(logs / "runner-state.json")
    if not st:
        return "runner: no state yet"
    phase = st.get("phase")
    if phase != "stopped" and not alive(st.get("pid")):
        phase = f"DEAD (last phase {phase})"
    parts = [f"runner {phase}", f"attempt {st.get('attempt')}/{st.get('max_attempts')}"]
    if st.get("phase") == "running" and st.get("last_event_at"):
        age = time.time() - st["last_event_at"]
        parts.append(f"last event {int(age)}s ago" + ("  ** STALLED? **" if age > STALL_S else ""))
    if st.get("next_wake_at"):
        parts.append(f"next wake {hhmm(st['next_wake_at'])} ({st.get('sleep_reason')})")
    if st.get("util_5h") is not None:
        parts.append(f"5h usage {st['util_5h']:.0%}")
    if st.get("stop_reason"):
        parts.append(f"stopped: {st['stop_reason']}")
    return " | ".join(parts)


def job_rows() -> list[str]:
    rows = []
    for p in sorted(JOBS.glob("*.json")):
        j = read_json(p)
        exit_file = Path(j.get("exit_file", "/nonexistent"))
        if exit_file.exists():
            state = f"exited {exit_file.read_text().strip()}"
        elif alive(j.get("pid")):
            state = "RUNNING"
        else:
            state = "gone (killed?)"
        last = ""
        log = Path(j.get("log", "/nonexistent"))
        if log.exists():
            with log.open("rb") as f:
                f.seek(max(0, log.stat().st_size - 400))
                tail = f.read().decode(errors="replace").strip().splitlines()
                last = _short(tail[-1], 110) if tail else ""
        rows.append(f"  {p.stem:<16} {state:<16} started {hhmm(j.get('started_at'))}  {last}")
    return rows


def cmd_status(a: argparse.Namespace) -> int:
    logs = Path(a.logs)
    print(status_line(logs))
    st = read_json(logs / "runner-state.json")
    if st.get("session_id"):
        print(f"  session {st['session_id']}  (inspect: claude --resume {st['session_id']})")
    if st.get("last_result"):
        print(f"  last result: {_short(st['last_result'].get('result'), 300)}")
    print("jobs:")
    print("\n".join(job_rows()) or "  (none)")
    print(f"logs: {logs}  |  jobs: {JOBS}")
    return 0


def cmd_render(a: argparse.Namespace) -> int:
    for e in iter_events(Path(a.file)):
        for line in render_event(e):
            print(line)
    return 0


# ---------------------------------------------------------------- jobs
def tmux_name(name: str) -> str:
    return f"kbio-job-{name}"


def cmd_job(a: argparse.Namespace) -> int:
    JOBS.mkdir(parents=True, exist_ok=True)
    meta = JOBS / f"{a.name}.json"
    if a.action == "list":
        print("\n".join(job_rows()) or "(no jobs)")
        return 0
    j = read_json(meta)
    if a.action == "stop":
        if not alive(j.get("pid")):
            print(f"job {a.name} is not running")
            return 1
        os.killpg(j["pid"], signal.SIGTERM)
        j["stopped_by_user_at"] = time.time()
        write_json(meta, j)
        for _ in range(100):  # wait (up to 20 s) so a following `job start` sees it gone
            if not alive(j["pid"]):
                break
            time.sleep(0.2)
        print(f"sent SIGTERM to job {a.name} (process group {j['pid']})")
        return 0
    if a.action == "start":
        if alive(j.get("pid")) and not Path(j.get("exit_file", "/x")).exists():
            print(f"job {a.name} is already running (pid {j['pid']})", file=sys.stderr)
            return 1
        cmd = a.cmd
        if not cmd:
            print("usage: job start NAME [--log P] [--cwd D] -- CMD...", file=sys.stderr)
            return 2
        log = Path(
            a.log or REPO / "data" / "experiments" / "kb-io-bench" / "logs" / f"{a.name}.log"
        ).resolve()
        cfg = {
            "name": a.name,
            "cmd": cmd,
            "cwd": str(Path(a.cwd or os.getcwd()).resolve()),
            "log": str(log),
            "exit_file": str(JOBS / f"{a.name}.exit"),
            "requested_at": time.time(),
        }
        Path(cfg["exit_file"]).unlink(missing_ok=True)
        write_json(meta, cfg)
        # The tmux pane (a child of the tmux server, not of this shell or of `claude -p`) spawns the
        # worker, so the job outlives whoever launched it.
        me = shlex.quote(str(Path(__file__).resolve()))
        pane = f"{shlex.quote(sys.executable)} {me} job _pane {a.name}"
        # a lingering viewer pane of the previous (finished) run of this job is only a view
        subprocess.run(["tmux", "kill-session", "-t", f"={tmux_name(a.name)}"], capture_output=True)
        subprocess.run(["tmux", "new-session", "-d", "-s", tmux_name(a.name), pane], check=True)
        print(f"started job {a.name}: tmux session {tmux_name(a.name)}, log {log}")
        print(
            f"watch: tmux attach -r -t '={tmux_name(a.name)}'   stop: runner.py job stop {a.name}"
        )
        return 0
    if a.action == "_pane":
        return job_pane(meta)
    return 2


WORKER = r"""
export PYTHONUNBUFFERED=1
# a handler (not an ignore, which children would inherit): the pipeline dies, the exit is recorded
trap 'printf "[%(%F %T)T] === job %s received SIGTERM\n" -1 "$NAME" >> "$LOG"' TERM
printf '[%(%F %T)T] === job %s started (pid %s): %s\n' -1 "$NAME" "$$" "$CMDLINE" >> "$LOG"
"$@" 2>&1 < /dev/null | while IFS= read -r l; do printf '[%(%F %T)T] %s\n' -1 "$l"; done >> "$LOG"
rc=${PIPESTATUS[0]}
printf '[%(%F %T)T] === job %s exited with code %s\n' -1 "$NAME" "$rc" >> "$LOG"
echo "$rc" > "$EXIT_FILE"
"""


def job_pane(meta: Path) -> int:
    cfg = read_json(meta)
    log = Path(cfg["log"])
    log.parent.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "LOG": str(log),
        "EXIT_FILE": cfg["exit_file"],
        "NAME": cfg["name"],
        "CMDLINE": shlex.join(cfg["cmd"]),
    }
    # own session: pane Ctrl-C and `tmux kill-session` (SIGHUP) do not reach the worker
    p = subprocess.Popen(
        ["bash", "-c", WORKER, "_", *cfg["cmd"]], cwd=cfg["cwd"], env=env, start_new_session=True
    )
    cfg.update(pid=p.pid, started_at=time.time())
    write_json(meta, cfg)

    def on_int(*_: object) -> None:
        print(
            f"\n[{now()}] Ctrl-C only closes this view; job {cfg['name']} keeps running. "
            f"Stop it with: runner.py job stop {cfg['name']}",
            flush=True,
        )
        with log.open("a") as f:
            f.write(f"[{now()}] (viewer Ctrl-C ignored; job still running)\n")

    signal.signal(signal.SIGINT, on_int)
    print(
        f"job {cfg['name']} (pid {p.pid}) in {cfg['cwd']}\n"
        f"  cmd: {shlex.join(cfg['cmd'])}\n  log: {log}\n"
        f"  stop: python3 {Path(__file__).resolve()} job stop {cfg['name']}\n",
        flush=True,
    )
    pos = 0
    while True:
        if log.exists():
            with log.open() as f:
                f.seek(pos)
                chunk = f.read()
                pos = f.tell()
            if chunk:
                print(chunk, end="", flush=True)
        if p.poll() is not None:
            if not Path(cfg["exit_file"]).exists():  # killed before it could record its exit
                Path(cfg["exit_file"]).write_text(
                    f"killed (signal {-p.returncode})\n"
                    if p.returncode < 0
                    else f"{p.returncode}\n"
                )
            print(f"[{now()}] job finished; this pane closes in 30 s", flush=True)
            time.sleep(30)
            return 0
        time.sleep(1)


# ---------------------------------------------------------------- cli
def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--logs", default=str(LOGS))
    sub = ap.add_subparsers(dest="sub", required=True)
    r = sub.add_parser("run")
    r.add_argument("--start", default="now", help="'now' or a unix epoch")
    r.add_argument("--max-attempts", type=int, default=30)
    r.add_argument("--interval", type=int, default=1800, help="seconds between attempts")
    r.add_argument("--attempt-timeout", type=int, default=3 * 3600)
    r.add_argument("--prompt", default=str(EXP / "agent" / "prompt.txt"))
    r.add_argument("--status", default=str(EXP / "STATUS.md"))
    r.add_argument("--done-line", default="ALL MILESTONES DONE")
    r.add_argument("--blocked-prefix", default="BLOCKED:")
    r.add_argument("--cwd", default=str(REPO))
    r.add_argument("--claude", default=str(CLAUDE))
    r.add_argument("--model", default=None)
    sub.add_parser("view")
    sub.add_parser("status")
    rd = sub.add_parser("render")
    rd.add_argument("file")
    j = sub.add_parser("job")
    j.add_argument("action", choices=["start", "stop", "list", "_pane"])
    j.add_argument("name", nargs="?", default="")
    j.add_argument("--log")
    j.add_argument("--cwd")
    argv = sys.argv[1:]
    cmd = argv[argv.index("--") + 1 :] if "--" in argv else []
    a = ap.parse_args(argv[: argv.index("--")] if "--" in argv else argv)
    a.cmd = cmd
    if a.sub == "run":
        return Runner(a).run()
    return {"view": cmd_view, "status": cmd_status, "render": cmd_render, "job": cmd_job}[a.sub](a)


if __name__ == "__main__":
    sys.exit(main())
