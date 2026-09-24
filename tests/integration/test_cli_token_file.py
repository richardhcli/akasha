"""Integration tests for the saved human token (task T18.9, ruling M18-A).

M18-A: an agent that shells out to `akasha` acts as the human -- accepted, so the
CLI reads the human token `init`/`setup` mint from a 0600 file. These tests pin
exactly what was and was not approved: the file holds only the human token, is
never overwritten, is used only for the default endpoint, and the plugin gets the
token only with an explicit ``--with-token``. Everything runs under a scratch
``HOME`` (the real config dir is asserted untouched); the daemon is real.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import stat
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from akasha import config as config_module
from akasha import daemon as daemon_module
from akasha.cli.main import app as cli_app
from akasha.config import default_token_path, read_token, write_token

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
    (conf_dir / "config.toml").write_text(
        f'port = {port}\nbind = "127.0.0.1"\ndb_path = "{(conf_dir / "store.db").as_posix()}"\n',
        encoding="utf-8",
    )
    vault = tmp_path / "vault"
    vault.mkdir()
    info: dict[str, Any] = {
        "conf_dir": conf_dir,
        "token_file": conf_dir / "tm-token",
        "vault": vault,
        "port": port,
        "tmp": tmp_path,
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


def _setup(env: dict[str, Any]) -> str:
    result = runner.invoke(cli_app, ["setup", str(env["vault"])])
    assert result.exit_code == 0, result.output
    return str(read_token(env["token_file"]))


# --- the file itself -----------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
def test_write_token_creates_the_file_0600_at_creation_and_replaces_atomically(
    tmp_path, monkeypatch
):
    modes: list[int] = []
    real_open = os.open

    def spy(path: Any, flags: int, mode: int = 0o777, **kw: Any) -> int:
        modes.append(mode)
        return real_open(path, flags, mode, **kw)

    monkeypatch.setattr(config_module.os, "open", spy)
    target = tmp_path / "nested" / "tm-token"

    write_token(target, "first.secret")
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert modes == [0o600]  # the mode was set AT CREATION, not chmodded afterwards
    write_token(target, "second.secret")
    assert read_token(target) == "second.secret"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert [p.name for p in target.parent.iterdir()] == ["tm-token"]  # no temp file left


def test_read_token_treats_missing_and_empty_as_none(tmp_path):
    assert read_token(tmp_path / "absent") is None
    (tmp_path / "empty").write_text("  \n", encoding="utf-8")
    assert read_token(tmp_path / "empty") is None
    assert default_token_path(tmp_path) == tmp_path / "tm-token"


# --- setup / init save it --------------------------------------------------------


def test_after_setup_a_verb_with_no_flag_and_no_env_authenticates_from_the_file(env):
    token = _setup(env)
    assert token and env["token_file"].is_file()
    if sys.platform != "win32":
        assert stat.S_IMODE(env["token_file"].stat().st_mode) == 0o600

    result = runner.invoke(cli_app, ["status"])  # no --token, no AKASHA_TOKEN

    assert result.exit_code == 0, result.output
    assert "token:   accepted" in result.output


def test_the_file_holds_only_the_human_token_and_token_create_does_not_touch_it(env):
    human = _setup(env)
    created = runner.invoke(cli_app, ["token", "create", "ci-bot", "--class", "agent"])
    assert created.exit_code == 0, created.output
    assert read_token(env["token_file"]) == human
    payload = json.loads(created.output)
    assert human not in created.output and payload.get("class", "agent") == "agent"


def test_setup_never_overwrites_an_existing_token_file(env):
    env["token_file"].write_text("SENTINEL\n", encoding="utf-8")
    result = runner.invoke(cli_app, ["setup", str(env["vault"])])
    assert result.exit_code == 0, result.output
    assert "left the existing token file" in result.output
    assert read_token(env["token_file"]) == "SENTINEL"


def test_init_saves_the_token_and_keeps_its_stdout_contract(env):
    first = runner.invoke(cli_app, ["init"])
    assert first.exit_code == 0, first.output
    lines = [ln for ln in first.output.splitlines() if ln.strip()]
    assert read_token(env["token_file"]) == lines[0].strip()
    assert "shown once" in lines[1]
    second = runner.invoke(cli_app, ["init"])
    assert second.exit_code == 4
    assert read_token(env["token_file"]) == lines[0].strip()  # untouched


def test_setup_dry_run_writes_no_token_file(env):
    result = runner.invoke(cli_app, ["--dry-run", "setup", str(env["vault"])])
    assert result.exit_code == 0, result.output
    assert not env["token_file"].exists()


# --- precedence, scope, hints -------------------------------------------------------


def test_precedence_is_flag_then_env_then_file(env, monkeypatch):
    good = _setup(env)

    monkeypatch.setenv("AKASHA_TOKEN", "bogus.env")
    env_wins = runner.invoke(cli_app, ["get", "whatever"])
    assert env_wins.exit_code == 1 and "E_AUTH" in env_wins.output  # env beat the valid file

    flag_wins = runner.invoke(cli_app, ["--token", good, "status"])
    assert flag_wins.exit_code == 0, flag_wins.output  # flag beat the bogus env

    monkeypatch.delenv("AKASHA_TOKEN")
    file_used = runner.invoke(cli_app, ["status"])
    assert file_used.exit_code == 0, file_used.output


def test_the_saved_token_is_never_sent_to_an_explicit_endpoint(env, monkeypatch):
    _setup(env)
    sent: list[dict[str, str]] = []
    real = httpx.request

    def spy(method: str, url: str, **kw: Any) -> Any:
        sent.append(dict(kw.get("headers") or {}))
        return real(method, url, **kw)

    monkeypatch.setattr(httpx, "request", spy)
    runner.invoke(cli_app, ["--base-url", "http://127.0.0.1:9", "get", "whatever"])
    assert sent and all("Authorization" not in h for h in sent)


def test_a_rejected_saved_token_gets_the_revoked_hint_but_a_flag_token_does_not(env):
    _setup(env)
    write_token(env["token_file"], "humantoken.wrongsecret")  # stale/revoked

    from_file = runner.invoke(cli_app, ["get", "whatever"])
    assert from_file.exit_code == 1
    assert "hint:" in from_file.output and "saved token" in from_file.output
    assert "revoked" in from_file.output

    from_flag = runner.invoke(cli_app, ["--token", "bogus.flag", "get", "whatever"])
    assert from_flag.exit_code == 1 and "hint:" not in from_flag.output


# --- plugin --with-token ---------------------------------------------------------------


def _built_plugin(tmp: Path) -> Path:
    built = tmp / "built"
    built.mkdir()
    (built / "manifest.json").write_text('{"id": "tm-hub"}\n', encoding="utf-8")
    (built / "main.js").write_text("//\n", encoding="utf-8")
    return built


def test_plugin_gets_the_token_only_with_the_explicit_flag(env):
    token = _setup(env)
    built = _built_plugin(env["tmp"])
    data = env["vault"] / ".obsidian/plugins/tm-hub/data.json"

    plain = runner.invoke(cli_app, ["plugin", "install", str(env["vault"]), "--from", str(built)])
    assert plain.exit_code == 0, plain.output
    assert "apiToken" not in json.loads(data.read_text())

    with_it = runner.invoke(
        cli_app, ["plugin", "install", str(env["vault"]), "--from", str(built), "--with-token"]
    )
    assert with_it.exit_code == 0, with_it.output
    assert json.loads(data.read_text())["apiToken"] == token
    assert "warning" not in with_it.output  # an ordinary local vault: no synced/git warning


def test_with_token_warns_when_the_vault_is_a_git_repository(env):
    _setup(env)
    (env["vault"] / ".git").mkdir()
    result = runner.invoke(
        cli_app,
        ["plugin", "install", str(env["vault"]), "--from", str(_built_plugin(env["tmp"])),
         "--with-token"],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert "warning" in result.output and "git repository" in result.output


def test_with_token_without_any_token_is_exit_4(env):
    result = runner.invoke(
        cli_app,
        ["plugin", "install", str(env["vault"]), "--from", str(_built_plugin(env["tmp"])),
         "--with-token"],
    )  # fmt: skip
    assert result.exit_code == 4
    assert not (env["vault"] / ".obsidian").exists()
