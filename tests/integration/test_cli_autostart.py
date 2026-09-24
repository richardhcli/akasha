"""Integration tests for on-demand daemon start (task T18.4, ruling M18-C).

The default endpoint is whatever the DEFAULT config (under ``HOME``) says, so each
test points ``HOME``/``APPDATA`` at ``tmp_path`` and writes a config with a free
port and its own DB: the real ``tm-daemon`` directory is never touched (asserted).
Anything that must NOT spawn a daemon monkeypatches ``spawn_detached`` to blow up,
so "spawned nothing" is proven by a failing call, not assumed.
"""

from __future__ import annotations

import json
import os
import signal
import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from akasha import daemon as daemon_module
from akasha.api import auth
from akasha.cli.main import app as cli_app
from akasha.kernel import store

runner = CliRunner()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    home = tmp_path / "home"
    conf_dir = home / ".config" / "tm-daemon"
    conf_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("APPDATA", str(home))
    for var in ("AKASHA_TOKEN", "AKASHA_BASE_URL", "AKASHA_NO_AUTOSTART"):
        monkeypatch.delenv(var, raising=False)
    port = _free_port()
    db = conf_dir / "store.db"
    (conf_dir / "config.toml").write_text(
        f'port = {port}\nbind = "127.0.0.1"\ndb_path = "{db.as_posix()}"\n', encoding="utf-8"
    )
    # Seed a human token + a node straight into the DB the daemon will open.
    conn = store.connect(db)
    store.run_migrations(conn)
    secret = auth.mint_secret()
    conn.execute(
        "INSERT INTO tokens (id, name, class, secret_hash, rate_per_min, created_at, "
        "revoked_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("humantoken", "humantoken", "human", auth.hash_secret(secret), None,
         "2026-01-01T00:00:00.000000+00:00", None),
    )  # fmt: skip
    conn.commit()
    node_id = store.create_node(conn, "claim", "hello from a stopped daemon").id
    conn.close()
    info: dict[str, Any] = {
        "conf_dir": conf_dir,
        "port": port,
        "token": auth.format_bearer_token("humantoken", secret),
        "node_id": node_id,
    }
    try:
        yield info
    finally:
        runner.invoke(cli_app, ["down"])
        pid = daemon_module._read_pid(conf_dir)  # pyright: ignore[reportPrivateUsage]
        if pid is not None:
            try:
                os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
            except ProcessLookupError:
                pass


def _forbid_spawn(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def boom(*_a: Any, **_k: Any) -> Any:
        calls.append("spawn")
        raise AssertionError("a daemon must not be spawned here")

    monkeypatch.setattr(daemon_module, "spawn_detached", boom)
    return calls


def test_default_endpoint_verb_starts_the_daemon_once_with_a_visible_notice(env):
    assert not daemon_module.lock_is_held(env["conf_dir"] / daemon_module.LOCK_FILE_NAME)

    result = runner.invoke(cli_app, ["--token", env["token"], "get", env["node_id"]])

    assert result.exit_code == 0, result.output
    assert "started daemon (log: " in result.output
    assert "daemon.log" in result.output
    assert "hello from a stopped daemon" in result.output
    pid = daemon_module._read_pid(env["conf_dir"])  # pyright: ignore[reportPrivateUsage]
    assert pid is not None

    # a second verb finds it already up: no second notice, same daemon
    again = runner.invoke(cli_app, ["--token", env["token"], "get", env["node_id"]])
    assert again.exit_code == 0 and "started daemon" not in again.output
    assert daemon_module._read_pid(env["conf_dir"]) == pid  # pyright: ignore[reportPrivateUsage]


def test_explicit_base_url_to_a_dead_port_spawns_nothing(env, monkeypatch):
    calls = _forbid_spawn(monkeypatch)
    dead = f"http://127.0.0.1:{_free_port()}"

    result = runner.invoke(
        cli_app, ["--base-url", dead, "--token", env["token"], "get", env["node_id"]]
    )

    assert result.exit_code == 1 and "E_CONNECTION" in result.output
    assert calls == []


def test_env_base_url_counts_as_explicit_and_spawns_nothing(env, monkeypatch):
    calls = _forbid_spawn(monkeypatch)
    monkeypatch.setenv("AKASHA_BASE_URL", f"http://127.0.0.1:{_free_port()}")

    result = runner.invoke(cli_app, ["--token", env["token"], "get", env["node_id"]])

    assert result.exit_code == 1 and calls == []


def test_no_autostart_variable_disables_it(env, monkeypatch):
    calls = _forbid_spawn(monkeypatch)
    monkeypatch.setenv("AKASHA_NO_AUTOSTART", "1")

    result = runner.invoke(cli_app, ["--token", env["token"], "get", env["node_id"]])

    assert result.exit_code == 1 and "E_CONNECTION" in result.output
    assert calls == []


def test_dry_run_never_spawns(env, monkeypatch):
    calls = _forbid_spawn(monkeypatch)

    result = runner.invoke(cli_app, ["--dry-run", "--json", "rm", env["node_id"]])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["dry_run"] is True
    assert calls == []


def test_401_hint_appears_only_when_no_credential_was_supplied(env):
    assert runner.invoke(cli_app, ["up"]).exit_code == 0  # daemon is up: no autostart involved

    bare = runner.invoke(cli_app, ["get", env["node_id"]])
    assert bare.exit_code == 1
    assert (
        "hint:" in bare.output and "akasha setup" in bare.output and "AKASHA_TOKEN" in bare.output
    )

    wrong = runner.invoke(cli_app, ["--token", "bogus.token", "get", env["node_id"]])
    assert wrong.exit_code == 1
    assert "hint:" not in wrong.output

    as_json = runner.invoke(cli_app, ["--json", "get", env["node_id"]])
    assert as_json.exit_code == 1
    assert "hint" in json.loads(as_json.output.strip().splitlines()[-1])["error"]["detail"]

    ok = runner.invoke(cli_app, ["--token", env["token"], "get", env["node_id"]])
    assert ok.exit_code == 0 and "hint:" not in ok.output
