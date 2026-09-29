"""Scratch akasha daemon control: always HOME=scratch, port 7534, via kbio.env.

The daemon runs as the launcher job `daemon` (`agent/runner.py job start`), in tmux session
`kbio-job-daemon` with its own process session. It outlives a headless agent session, its pane
shows the log live, and a pane Ctrl-C does not stop it. `akasha setup` then finds it healthy
("already") and only mints the token and registers the vault.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import requests

from kbio.env import AKASHA_BIN, scratch_env, write_scratch_config
from kbio.paths import BASE_URL, DATA, SCRATCH_HOME

JOB = "daemon"
RUNNER = Path(__file__).resolve().parents[1] / "agent" / "runner.py"


def _job(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["python3", str(RUNNER), "job", *args], capture_output=True, text=True, check=False
    )


def token_path(home: Path = SCRATCH_HOME) -> Path:
    return home / ".config" / "tm-daemon" / "tm-token"


def token(home: Path = SCRATCH_HOME) -> str:
    return token_path(home).read_text().strip()


def healthy() -> bool:
    try:
        return requests.get(f"{BASE_URL}/health", timeout=2).status_code == 200
    except requests.RequestException:
        return False


def start(home: Path = SCRATCH_HOME) -> None:
    """Start the scratch daemon as a launcher job (idempotent)."""
    if healthy():
        return
    write_scratch_config(home)
    env = scratch_env(home)
    keep = ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "PATH")
    log = DATA / "logs" / "daemon.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    _job("stop", JOB)  # a hung (unhealthy) daemon job, if any
    cmd = ["env", "-u", "PURDUE_GENAI_API_KEY", *(f"{k}={env[k]}" for k in keep), str(AKASHA_BIN)]
    r = _job("start", JOB, "--log", str(log), "--cwd", str(DATA), "--", *cmd, "daemon")
    if r.returncode != 0:
        raise RuntimeError(f"could not launch the scratch daemon job: {r.stderr or r.stdout}")
    for _ in range(100):
        if healthy():
            return
        time.sleep(0.2)
    raise RuntimeError(f"scratch daemon did not come up; see {log}")


def stop() -> None:
    _job("stop", JOB)  # SIGTERM to the job's process group; uvicorn shuts down cleanly
    for _ in range(50):
        if not healthy():
            return
        time.sleep(0.2)


def cli(*args: str, home: Path = SCRATCH_HOME, check: bool = True) -> subprocess.CompletedProcess:
    env = scratch_env(home, {"AKASHA_TOKEN": token(home)} if token_path(home).exists() else None)
    return subprocess.run(
        [str(AKASHA_BIN), *args], env=env, capture_output=True, text=True, check=check
    )


def setup(vault: Path, name: str | None = None, home: Path = SCRATCH_HOME) -> dict:
    """`akasha --json setup <vault>` against the scratch daemon; returns the rescan summary
    (the minted token is saved to the scratch token file by akasha itself)."""
    start(home)
    args = ["--json", "setup", str(vault)] + (["--name", name] if name else [])
    r = cli(*args, home=home)
    data = json.loads(r.stdout)["data"]
    data.pop("token", None)
    return data


def api(method: str, path: str, home: Path = SCRATCH_HOME, **kw) -> dict:
    r = requests.request(
        method,
        f"{BASE_URL}/v1{path}",
        headers={"Authorization": f"Bearer {token(home)}"},
        timeout=600,
        **kw,
    )
    r.raise_for_status()
    return r.json()


def status_counts(home: Path = SCRATCH_HOME, root_path: Path | None = None) -> dict:
    """Files, violations, pauses, conflicts and open reviews (optionally for one sync root)."""
    st = api("GET", "/sync/status", home)
    roots = st["sync_roots"]
    if root_path is not None:
        roots = [r for r in roots if Path(r["root_path"]).resolve() == root_path.resolve()]
    reviews = api("GET", "/review", home)
    items = reviews.get("items", reviews.get("reviews", []))
    return {
        "roots": len(roots),
        "files": sum(len(r["files"]) for r in roots),
        "violations": sum(len(r["violations"]) for r in roots),
        "pauses": sum(len(r["pauses"]) for r in roots),
        "conflicts": sum(len(r["conflicts"]) for r in roots),
        "unresolved": len(st["unresolved"]),
        "reviews_open": len(items) if isinstance(items, list) else items,
    }


def rescan(home: Path = SCRATCH_HOME) -> dict:
    return api("POST", "/sync/rescan", home)


STORE_FILES = ("store.db", "store.db-wal", "store.db-shm", "tm-token")


def reset_store(vault: Path, name: str, home: Path = SCRATCH_HOME) -> dict:
    """Fresh store for one snapshot: the daemon's search and mirroring span every registered root,
    so two copies of a vault must never share a store. Stops the daemon, moves the old store and
    token to trash (never deleted), restarts, and `akasha setup`s the vault."""
    from kbio.paths import TRASH

    stop()
    cfg_dir = home / ".config" / "tm-daemon"
    bin_dir = TRASH / "stores" / (time.strftime("%Y%m%d-%H%M%S-") + name)
    for f in STORE_FILES:
        if (cfg_dir / f).exists():
            bin_dir.mkdir(parents=True, exist_ok=True)
            (cfg_dir / f).rename(bin_dir / f)
    t0 = time.monotonic()
    data = setup(vault, name=name, home=home)
    data["setup_seconds"] = round(time.monotonic() - t0, 2)
    return data
