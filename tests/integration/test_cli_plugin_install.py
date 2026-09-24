"""Integration tests for `akasha plugin install VAULT` (task T18.8, ruling M18-A).

Pure filesystem work against a ``tmp_path`` vault and a fake *built* plugin
directory -- no daemon needed. The token is never written by this verb.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from akasha.cli.main import app as cli_app

runner = CliRunner()


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "home"))
    built = tmp_path / "built"
    built.mkdir()
    (built / "manifest.json").write_text('{"id": "tm-hub", "name": "TM Hub"}\n', encoding="utf-8")
    (built / "main.js").write_text("/* built plugin */\n", encoding="utf-8")
    vault = tmp_path / "vault"
    vault.mkdir()
    return {"built": built, "vault": vault, "plugin": vault / ".obsidian/plugins/tm-hub"}


def _install(env: dict[str, Path], *extra: str, global_flags: tuple[str, ...] = ()) -> Any:
    return runner.invoke(
        cli_app,
        [
            *global_flags,
            "plugin",
            "install",
            str(env["vault"]),
            "--from",
            str(env["built"]),
            *extra,
        ],
    )


def _snapshot(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_the_three_files_land_where_obsidian_expects_them(env):
    result = _install(env)

    assert result.exit_code == 0, result.output
    assert (env["plugin"] / "manifest.json").read_text() == '{"id": "tm-hub", "name": "TM Hub"}\n'
    assert (env["plugin"] / "main.js").read_text() == "/* built plugin */\n"
    data = json.loads((env["plugin"] / "data.json").read_text())
    assert data == {"daemonUrl": "http://127.0.0.1:7433"}  # the address only: never a token
    listing = json.loads((env["vault"] / ".obsidian/community-plugins.json").read_text())
    assert listing == ["tm-hub"]
    assert "Restricted mode" in result.output and "API token" in result.output


def test_it_never_writes_a_token(env):
    _install(env)
    text = (env["plugin"] / "data.json").read_text().lower()
    assert "token" not in text


def test_existing_settings_and_plugins_are_merged_never_clobbered(env):
    env["plugin"].mkdir(parents=True)
    (env["plugin"] / "data.json").write_text(
        json.dumps({"daemonUrl": "http://10.0.0.5:9", "apiToken": "keep-me", "x": 1}),
        encoding="utf-8",
    )
    obs = env["vault"] / ".obsidian"
    (obs / "community-plugins.json").write_text('["dataview", "calendar"]', encoding="utf-8")

    assert _install(env).exit_code == 0

    data = json.loads((env["plugin"] / "data.json").read_text())
    assert data == {"daemonUrl": "http://10.0.0.5:9", "apiToken": "keep-me", "x": 1}
    assert json.loads((obs / "community-plugins.json").read_text()) == [
        "dataview",
        "calendar",
        "tm-hub",
    ]


def test_a_second_run_is_a_byte_identical_no_op(env):
    assert _install(env).exit_code == 0
    before = _snapshot(env["vault"])
    mtimes = {p: p.stat().st_mtime_ns for p in env["vault"].rglob("*") if p.is_file()}

    second = _install(env)

    assert second.exit_code == 0
    assert _snapshot(env["vault"]) == before
    assert {p: p.stat().st_mtime_ns for p in env["vault"].rglob("*") if p.is_file()} == mtimes
    assert "wrote" not in second.output and "unchanged" in second.output
    assert json.loads((env["vault"] / ".obsidian/community-plugins.json").read_text()) == ["tm-hub"]


def test_dry_run_writes_nothing(env):
    result = _install(env, global_flags=("--dry-run",))

    assert result.exit_code == 0, result.output
    assert "would write" in result.output and "nothing was written" in result.output
    assert not (env["vault"] / ".obsidian").exists()


def test_an_unbuilt_plugin_directory_fails_clearly(env):
    (env["built"] / "main.js").unlink()
    result = _install(env)
    assert result.exit_code == 3
    assert "npm ci && npm run build" in result.output
    assert not (env["vault"] / ".obsidian").exists()


def test_a_missing_vault_is_exit_3(env, tmp_path):
    result = runner.invoke(
        cli_app,
        ["plugin", "install", str(tmp_path / "nope"), "--from", str(env["built"])],
    )
    assert result.exit_code == 3


def test_invalid_existing_json_is_left_alone(env):
    env["plugin"].mkdir(parents=True)
    (env["plugin"] / "data.json").write_text("{not json", encoding="utf-8")
    result = _install(env)
    assert result.exit_code == 1
    assert (env["plugin"] / "data.json").read_text() == "{not json"


def test_the_daemon_address_comes_from_the_config(env, tmp_path):
    config = tmp_path / "config.toml"
    config.write_text("port = 8123\n", encoding="utf-8")
    assert _install(env, "--config", str(config)).exit_code == 0
    data = json.loads((env["plugin"] / "data.json").read_text())
    assert data["daemonUrl"] == "http://127.0.0.1:8123"


def test_the_checkout_default_is_used_when_from_is_omitted(env):
    from akasha.cli.main import _default_plugin_dir  # pyright: ignore[reportPrivateUsage]

    built = _default_plugin_dir()
    if built is None or not (built / "main.js").is_file():
        pytest.skip("plugin-obsidian/ is not built in this checkout")
    result = runner.invoke(cli_app, ["plugin", "install", str(env["vault"])])
    assert result.exit_code == 0, result.output
    assert (env["plugin"] / "main.js").read_bytes() == (built / "main.js").read_bytes()
