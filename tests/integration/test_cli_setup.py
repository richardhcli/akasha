"""Integration tests for `akasha setup [VAULT]` (task T18.5, rulings M18-A/M18-D).

Everything runs against a scratch ``HOME``/config with its own port and DB, and a
REAL detached daemon; the real ``tm-daemon`` directory is never touched (asserted).
"""

from __future__ import annotations

import json
import os
import re
import signal
import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from akasha import daemon as daemon_module
from akasha.cli.main import app as cli_app
from akasha.config import load_config
from akasha.kernel import store

runner = CliRunner()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("APPDATA", str(home))
    for var in ("AKASHA_TOKEN", "AKASHA_BASE_URL", "AKASHA_NO_AUTOSTART"):
        monkeypatch.delenv(var, raising=False)
    conf_dir = tmp_path / "conf"
    conf_dir.mkdir()
    port = _free_port()
    config = conf_dir / "config.toml"
    config.write_text(
        f'port = {port}\nbind = "127.0.0.1"\ndb_path = "{(conf_dir / "store.db").as_posix()}"\n',
        encoding="utf-8",
    )
    vault = tmp_path / "vault"
    vault.mkdir()
    info: dict[str, Any] = {
        "home": home,
        "conf_dir": conf_dir,
        "config": str(config),
        "port": port,
        "url": f"http://127.0.0.1:{port}",
        "db": conf_dir / "store.db",
        "vault": vault,
    }
    try:
        yield info
    finally:
        runner.invoke(cli_app, ["down", "--config", info["config"]])
        pid = daemon_module._read_pid(conf_dir)  # pyright: ignore[reportPrivateUsage]
        if pid is not None:
            try:
                os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
            except ProcessLookupError:
                pass
        assert not (home / ".config").exists(), "touched the real config dir"


def _setup(env: dict[str, Any], *args: str) -> Any:
    return runner.invoke(cli_app, ["setup", "--config", env["config"], *args])


def _token_count(env: dict[str, Any]) -> int:
    conn = store.connect(env["db"])
    try:
        return len(store.list_tokens(conn))
    finally:
        conn.close()


def _token_from(output: str) -> str:
    m = re.search(r"export AKASHA_TOKEN=(\S+)", output)
    assert m, output
    return m.group(1)


def test_setup_gives_a_live_reconciled_vault_and_a_working_token(env):
    (env["vault"] / "todo.md").write_text("- [ ] ship it ^tm-new\n", encoding="utf-8")  # no tm: 1!

    result = _setup(env, str(env["vault"]))

    assert result.exit_code == 0, result.output
    assert env["url"] in result.output and "1 file(s) reconciled" in result.output
    assert ".tmignore" in result.output
    assert "secrets" in result.output  # the plain warning about the link and token
    token = _token_from(result.output)
    assert f"web UI: {env['url']}/?token={token}" in result.output
    assert _token_count(env) == 1
    assert daemon_module.is_healthy(load_config(env["config"]))
    # a plain Markdown file (no front matter) was adopted and minted on the way through
    text = (env["vault"] / "todo.md").read_text(encoding="utf-8")
    assert text.startswith("---\ntm: 1\n---\n- [ ] ship it ^tm-") and "^tm-new" not in text
    # the printed token really authenticates, and the vault is registered
    status = httpx.get(f"{env['url']}/v1/sync/status", headers={"Authorization": f"Bearer {token}"})
    assert status.status_code == 200
    assert [r["root_path"] for r in status.json()["sync_roots"]] == [str(env["vault"])]
    assert httpx.get(f"{env['url']}/?token={token}").status_code == 200  # the UI link


def test_setup_twice_is_idempotent_and_needs_the_token_the_second_time(env):
    first = _setup(env, str(env["vault"]))
    assert first.exit_code == 0, first.output
    token = _token_from(first.output)

    bare = _setup(env, str(env["vault"]))  # a token exists; none supplied
    assert bare.exit_code == 4 and "cannot be recovered" in bare.output

    again = runner.invoke(
        cli_app, ["--token", token, "setup", "--config", env["config"], str(env["vault"])]
    )
    assert again.exit_code == 0, again.output
    assert "already running" in again.output or "already" in again.output
    assert "your token" not in again.output  # never re-printed
    assert _token_count(env) == 1
    status = httpx.get(
        f"{env['url']}/v1/sync/status", headers={"Authorization": f"Bearer {token}"}
    ).json()
    assert len(status["sync_roots"]) == 1  # the registration is an upsert


def test_setup_without_a_vault_only_bootstraps(env):
    result = _setup(env)
    assert result.exit_code == 0, result.output
    assert _token_count(env) == 1 and "vault:" not in result.output
    assert daemon_module.is_healthy(load_config(env["config"]))


def test_setup_dry_run_mints_spawns_and_registers_nothing(env, monkeypatch):
    def boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("dry-run must not spawn a daemon")

    monkeypatch.setattr(daemon_module, "spawn_detached", boom)

    result = runner.invoke(
        cli_app, ["--dry-run", "--json", "setup", "--config", env["config"], str(env["vault"])]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["dry_run"] is True and payload["plan"]
    assert not env["db"].exists()  # no database, hence no token and no registration
    assert not daemon_module.lock_is_held(env["conf_dir"] / daemon_module.LOCK_FILE_NAME)


def test_setup_with_a_missing_folder_is_exit_3_and_changes_nothing(env):
    result = _setup(env, str(env["vault"] / "nope"))
    assert result.exit_code == 3
    assert not env["db"].exists()


def test_setup_json_mode_returns_the_token_and_daemon_details(env):
    result = runner.invoke(
        cli_app, ["--json", "setup", "--config", env["config"], str(env["vault"])]
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)["data"]
    assert data["token"] and data["daemon"]["url"] == env["url"]
    assert data["vault"]["path"] == str(env["vault"])


def test_init_is_unchanged_a_second_init_still_exits_4(env):
    first = runner.invoke(cli_app, ["init", "--config", env["config"]])
    assert first.exit_code == 0 and "shown once" in first.output
    second = runner.invoke(cli_app, ["init", "--config", env["config"]])
    assert second.exit_code == 4
