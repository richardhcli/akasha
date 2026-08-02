"""Integration tests for the `akasha sync add` CLI verb (task T12.2, spec §4.11/§4.12).

Wraps the existing ``POST /v1/sync/roots`` (task T4.10) the same way every
other verb wraps its endpoint -- pure HTTP client, no new server-side logic.
Reuses the live-daemon fixture pattern from ``tests/integration/test_cli.py``
(a real ``uvicorn`` server on an ephemeral localhost port, driven through the
CLI's own typer entrypoint via ``typer.testing.CliRunner``).
"""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import uvicorn
from typer.testing import CliRunner

from akasha.api import auth
from akasha.api.app import create_app
from akasha.cli.main import app as cli_app
from akasha.kernel import store

runner = CliRunner()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _insert_token(conn, token_id: str, secret: str, cls: str) -> None:  # type: ignore[no-untyped-def]
    conn.execute(
        "INSERT INTO tokens (id, name, class, secret_hash, rate_per_min, created_at, "
        "revoked_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            token_id,
            token_id,
            cls,
            auth.hash_secret(secret),
            None,
            "2026-01-01T00:00:00.000000+00:00",
            None,
        ),
    )
    conn.commit()


@pytest.fixture
def daemon(tmp_path) -> Iterator[dict[str, Any]]:  # type: ignore[no-untyped-def]
    conn = store.connect(tmp_path / "cli.db", check_same_thread=False)
    store.run_migrations(conn)
    human_secret = auth.mint_secret()
    _insert_token(conn, "humantoken", human_secret, "human")
    human_bearer = auth.format_bearer_token("humantoken", human_secret)

    fastapi_app = create_app(conn=conn)
    port = _free_port()
    config = uvicorn.Config(fastapi_app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10.0
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started, "uvicorn test server failed to start"

    try:
        yield {
            "base_url": f"http://127.0.0.1:{port}",
            "conn": conn,
            "token": human_bearer,
        }
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def _run(daemon: dict[str, Any], *args: str, token: str | None = None) -> Any:
    tok = daemon["token"] if token is None else token
    full_args = ["--base-url", daemon["base_url"]]
    if tok:
        full_args += ["--token", tok]
    full_args += list(args)
    return runner.invoke(cli_app, full_args)


def test_sync_add_round_trip(daemon, tmp_path):
    vault = tmp_path / "my-vault"
    vault.mkdir()
    result = _run(daemon, "sync", "add", str(vault), "--name", "foo")
    assert result.exit_code == 0, result.output
    created = json.loads(result.output)
    assert created["name"] == "foo"
    assert created["root_path"] == str(vault)


def test_sync_add_defaults_name_to_basename(daemon, tmp_path):
    vault = tmp_path / "another-vault"
    vault.mkdir()
    result = _run(daemon, "sync", "add", str(vault))
    assert result.exit_code == 0, result.output
    created = json.loads(result.output)
    assert created["name"] == "another-vault"
    assert created["root_path"] == str(vault)


def test_sync_add_registers_visible_via_get_roots(daemon, tmp_path):
    vault = tmp_path / "visible-vault"
    vault.mkdir()
    result = _run(daemon, "sync", "add", str(vault), "--name", "visible")
    assert result.exit_code == 0, result.output

    resp = httpx.get(
        f"{daemon['base_url']}/v1/sync/roots",
        headers={"Authorization": f"Bearer {daemon['token']}"},
    )
    assert resp.status_code == 200, resp.text
    roots = resp.json()["sync_roots"]
    names = [r["name"] for r in roots]
    assert "visible" in names
    matching = [r for r in roots if r["name"] == "visible"]
    assert matching[0]["root_path"] == str(vault)


def test_sync_add_dry_run_mutates_nothing(daemon, tmp_path):
    vault = tmp_path / "dry-run-vault"
    vault.mkdir()
    result = runner.invoke(
        cli_app,
        [
            "--base-url",
            daemon["base_url"],
            "--token",
            daemon["token"],
            "--dry-run",
            "sync",
            "add",
            str(vault),
            "--name",
            "should-not-exist",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["method"] == "POST"
    assert payload["path"] == "/v1/sync/roots"
    assert payload["body"]["name"] == "should-not-exist"
    assert payload["body"]["root_path"] == str(vault)

    conn = daemon["conn"]
    count = conn.execute(
        "SELECT count(*) FROM sync_roots WHERE name = ?", ("should-not-exist",)
    ).fetchone()[0]
    assert count == 0


def test_sync_add_json_flag_emits_cli_v1_schema(daemon, tmp_path):
    vault = tmp_path / "json-vault"
    vault.mkdir()
    result = runner.invoke(
        cli_app,
        [
            "--base-url",
            daemon["base_url"],
            "--token",
            daemon["token"],
            "--json",
            "sync",
            "add",
            str(vault),
            "--name",
            "json-vault",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema"] == "cli/v1"
    assert payload["ok"] is True
    assert payload["data"]["name"] == "json-vault"
