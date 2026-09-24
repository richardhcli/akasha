"""Integration tests for `akasha status` (task T18.6).

Read-only diagnosis against a real scratch daemon (a live ``uvicorn`` app on an
ephemeral port). The three classic first-run failures -- no credential, no vault,
a vault with nothing tracked -- each produce their named hint, asserted against
REAL state (a real sync root, a real reconcile), not just strings; a healthy vault
produces none; and the verb issues only GETs.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from typer.testing import CliRunner

from akasha.api import auth
from akasha.api.app import create_app
from akasha.cli.main import app as cli_app
from akasha.kernel import store
from akasha.sync import reconcile

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
    agent_secret = auth.mint_secret()
    _insert_token(conn, "agenttoken", agent_secret, "agent")
    agent_bearer = auth.format_bearer_token("agenttoken", agent_secret)

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
            "agent_token": agent_bearer,
        }
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def _run(
    daemon: dict[str, Any], *args: str, token: str | None = None, base: str | None = None
) -> Any:
    tok = daemon["token"] if token is None else token
    argv = ["--base-url", base or daemon["base_url"]]
    if tok:
        argv += ["--token", tok]
    return runner.invoke(cli_app, [*argv, *args])


def _root(daemon: dict[str, Any], tmp_path: Path, files: dict[str, str]) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    for rel, text in files.items():
        (vault / rel).write_text(text, encoding="utf-8")
    store.register_sync_root(daemon["conn"], "vault", str(vault))
    reconcile.reconcile_all(daemon["conn"])
    return vault


def test_no_credential_names_the_fix_and_exits_1(daemon):
    result = runner.invoke(cli_app, ["--base-url", daemon["base_url"], "status"])
    assert result.exit_code == 1, result.output
    assert "daemon:  ok" in result.output and "not supplied" in result.output
    assert "hint:" in result.output and "akasha setup" in result.output


def test_no_vault_registered_says_so(daemon):
    result = _run(daemon, "status")
    assert result.exit_code == 0, result.output
    assert "sync roots: 0" in result.output
    assert "no vault is registered" in result.output and "akasha setup" in result.output


def test_registered_vault_with_nothing_tracked_names_the_cause(daemon, tmp_path):
    _root(daemon, tmp_path, {"prose.md": "just words, nothing to sync\n"})
    result = _run(daemon, "status")
    assert result.exit_code == 0, result.output
    assert "files tracked: 0" in result.output
    assert "nothing in it is tracked yet" in result.output and ".tmignore" in result.output
    assert store.list_sync_files(daemon["conn"]) == []  # the real state the hint describes


def test_a_healthy_vault_produces_no_hint(daemon, tmp_path):
    _root(daemon, tmp_path, {"todo.md": "- [ ] ship it ^tm-new\n"})
    result = _run(daemon, "status")
    assert result.exit_code == 0, result.output
    assert "files tracked: 1" in result.output
    assert "hint:" not in result.output
    assert "open reviews: 0" in result.output


def test_violations_are_grouped_by_code(daemon, tmp_path):
    # M20-G: the sync engine now resolves a duplicate or unknown anchor by itself, so only a
    # deleted S1+ node (or a legacy review) is left to group: seed the review queue directly.
    vault = _root(daemon, tmp_path, {"a.md": "- [ ] one ^tm-new\n"})
    path = str(vault / "a.md")
    for code in ("E_DELETED_S1", "E_DELETED_S1", "E_UNPROJECTABLE_BODY"):
        store.enqueue_review(
            daemon["conn"],
            None,
            "violation",
            cause_ref=json.dumps({"path": path, "code": code, "line_nos": [1], "message": "x"}),
        )
    result = _run(daemon, "--json", "status")
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)["data"]
    (root,) = data["sync_roots"]
    assert root["violations"] == {"E_DELETED_S1": 2, "E_UNPROJECTABLE_BODY": 1}
    assert data["open_reviews"] == 3
    plain = _run(daemon, "status")
    assert "violation E_DELETED_S1: 2" in plain.output


def test_a_duplicate_anchor_is_resolved_not_queued(daemon, tmp_path):
    dup = "- [ ] one ^tm-4cgfdxpi\n- [ ] two ^tm-4cgfdxpi\n"
    _root(daemon, tmp_path, {"dup.md": dup})
    result = _run(daemon, "--json", "status")
    data = json.loads(result.output)["data"]
    (root,) = data["sync_roots"]
    assert root["violations"] == {} and data["open_reviews"] == 0


def test_bad_credential_is_the_shared_unauthorized_mapping(daemon):
    result = _run(daemon, "status", token="bogus.token")
    assert result.exit_code == 1 and "E_AUTH" in result.output


def test_unreachable_daemon_is_exit_1_and_never_autostarts(daemon, monkeypatch):
    from akasha import daemon as daemon_module

    def boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("status must never spawn a daemon")

    monkeypatch.setattr(daemon_module, "spawn_detached", boom)
    result = _run(daemon, "status", base="http://127.0.0.1:9")
    assert result.exit_code == 1 and "E_CONNECTION" in result.output


def test_status_issues_only_gets(daemon, tmp_path, monkeypatch):
    _root(daemon, tmp_path, {"todo.md": "- [ ] ship it ^tm-new\n"})
    methods: list[str] = []
    real = httpx.request

    def spy(method: str, *a: Any, **k: Any) -> Any:
        methods.append(method)
        return real(method, *a, **k)

    monkeypatch.setattr(httpx, "request", spy)
    assert _run(daemon, "status").exit_code == 0
    assert methods and set(methods) == {"GET"}


def test_status_output_is_ascii_only(daemon, tmp_path):
    _root(daemon, tmp_path, {"prose.md": "words\n"})
    result = _run(daemon, "status")
    assert result.output.isascii(), result.output
