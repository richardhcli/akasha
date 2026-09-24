"""Integration tests for `akasha up` / `akasha down` (task T18.3, ruling M18-C).

These spawn a REAL detached daemon subprocess, so each test runs against its own
``tmp_path`` config (own port, own DB, own lock + pid file) and redirects
``HOME``/``APPDATA`` there too: nothing may touch the real ``tm-daemon`` dir.
Every test tears its daemon down, even on failure.
"""

from __future__ import annotations

import os
import signal
import socket
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from akasha import daemon as daemon_module
from akasha.cli.main import app as cli_app
from akasha.config import load_config

runner = CliRunner()

# `os.kill(pid, 0)` is a liveness probe on POSIX but TERMINATES the process on Windows, and
# the daemon's SIGTERM path is unverified there; CI runs these on Linux only.
pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX process semantics (kill(pid, 0), SIGTERM)"
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _alive(pid: int) -> bool:
    # The CLI runs in-process here, so the detached daemon is OUR child: reap it, or a
    # finished daemon lingers as a zombie that `kill(pid, 0)` still reports as alive.
    if hasattr(os, "WNOHANG"):  # POSIX only; on Windows a finished process is simply gone
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass
    try:
        os.kill(pid, 0)
    except OSError:  # ProcessLookupError on POSIX; a generic OSError on Windows
        return False
    return True


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("APPDATA", str(home))
    conf_dir = tmp_path / "conf"
    conf_dir.mkdir()
    port = _free_port()
    config_path = conf_dir / "config.toml"
    config_path.write_text(
        f'port = {port}\nbind = "127.0.0.1"\ndb_path = "{(conf_dir / "store.db").as_posix()}"\n',
        encoding="utf-8",
    )
    info = {
        "home": home,
        "conf_dir": conf_dir,
        "config": str(config_path),
        "port": port,
        "pid_file": conf_dir / daemon_module.PID_FILE_NAME,
        "lock_file": conf_dir / daemon_module.LOCK_FILE_NAME,
    }
    try:
        yield info
    finally:
        # Never leave a daemon behind, whatever the test did.
        runner.invoke(cli_app, ["down", "--config", info["config"]])
        pid = daemon_module._read_pid(conf_dir)  # pyright: ignore[reportPrivateUsage]
        if pid is not None and _alive(pid):
            os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
        assert not (home / ".config" / "tm-daemon").exists(), "touched the real config dir"


def _up(env: dict[str, Any]) -> Any:
    return runner.invoke(cli_app, ["up", "--config", env["config"]])


def _down(env: dict[str, Any]) -> Any:
    return runner.invoke(cli_app, ["down", "--config", env["config"]])


def test_up_starts_a_healthy_detached_daemon_and_a_second_up_is_a_no_op(env):
    first = _up(env)
    assert first.exit_code == 0, first.output
    assert "started daemon" in first.output and str(env["conf_dir"] / "daemon.log") in first.output
    assert daemon_module.is_healthy(load_config(env["config"]))
    pid = int(env["pid_file"].read_text().strip())
    assert _alive(pid)
    assert daemon_module.lock_is_held(env["lock_file"])

    second = _up(env)
    assert second.exit_code == 0, second.output
    assert "already running" in second.output
    assert int(env["pid_file"].read_text().strip()) == pid  # no second spawn


def test_down_stops_it_and_leaves_no_stale_pid_or_lock(env):
    assert _up(env).exit_code == 0
    pid = int(env["pid_file"].read_text().strip())

    result = _down(env)

    assert result.exit_code == 0, result.output
    assert "stopped daemon" in result.output
    deadline = time.monotonic() + 5
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(pid)
    assert not env["pid_file"].exists()
    assert not daemon_module.lock_is_held(env["lock_file"])
    assert not daemon_module.is_healthy(load_config(env["config"]))

    again = _down(env)  # idempotent
    assert again.exit_code == 0 and "not running" in again.output


def test_stale_pid_file_is_removed_and_never_signalled(env):
    # The pid is OUR OWN: if `down` trusted the file it would SIGTERM this test run.
    env["pid_file"].write_text(f"{os.getpid()}\n", encoding="utf-8")

    result = _down(env)

    assert result.exit_code == 0 and "not running" in result.output
    assert not env["pid_file"].exists()


def test_a_second_foreground_daemon_after_up_still_exits_4(env):
    assert _up(env).exit_code == 0
    second = runner.invoke(cli_app, ["daemon", "--config", env["config"]])
    assert second.exit_code == 4, second.output
    # ... and it did not disturb the running one's pid file
    assert daemon_module.is_healthy(load_config(env["config"]))
    assert _alive(int(env["pid_file"].read_text().strip()))


def test_up_reports_a_held_lock_with_a_silent_health_endpoint_as_exit_4(env):
    with daemon_module.single_instance_lock(env["lock_file"]):
        result = _up(env)
    assert result.exit_code == 4, result.output
    assert "lock is held" in result.output


def test_up_fails_cleanly_when_the_port_is_taken_by_something_else(env):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as blocker:
        blocker.bind(("127.0.0.1", env["port"]))
        blocker.listen(1)  # accepts connections, never speaks HTTP
        result = _up(env)
    assert result.exit_code == 1, result.output
    assert "did not become healthy" in result.output
    assert str(env["conf_dir"] / "daemon.log") in result.output
