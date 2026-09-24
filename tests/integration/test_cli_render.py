"""Integration tests for `akasha render FILE` (task T18.7).

The only headless file-to-file view: a file whose embeds are printed as their
targets' CURRENT text, read-only. Runs against a real scratch daemon; the files'
bytes are hashed before and after every call.
"""

from __future__ import annotations

import hashlib
import json
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

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


def _run(daemon: dict[str, Any], *args: str) -> Any:
    argv = ["--base-url", daemon["base_url"], "--token", daemon["token"]]
    return runner.invoke(cli_app, [*argv, *args])


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _vault(daemon: dict[str, Any], tmp_path: Path) -> tuple[Path, Path, str]:
    """A.md holds an anchored note; B.md embeds it. Returns (A, B, note id)."""
    vault = tmp_path / "vault"
    vault.mkdir()
    a, b = vault / "A.md", vault / "B.md"
    a.write_text("The sky is blue. ^tm-new\n", encoding="utf-8")
    store.register_sync_root(daemon["conn"], "vault", str(vault))
    reconcile.reconcile_all(daemon["conn"])
    node_id = a.read_text(encoding="utf-8").rsplit("^tm-", 1)[1].strip()
    b.write_text(f"# B\n\n![[A.md#^tm-{node_id}]]\n\nafter\n", encoding="utf-8")
    return a, b, node_id


def test_render_shows_the_targets_current_text_and_never_writes(daemon, tmp_path):
    a, b, node_id = _vault(daemon, tmp_path)
    before = (_sha(a), _sha(b))

    first = _run(daemon, "render", str(b))
    assert first.exit_code == 0, first.output
    assert f"> The sky is blue. (^tm-{node_id}, from A.md)" in first.output
    assert "# B" in first.output and "after" in first.output
    assert f"![[A.md#^tm-{node_id}]]" not in first.output  # the link is replaced in the view

    assert _run(daemon, "set", node_id, "--body", "The sky is grey.").exit_code == 0
    after_edit = (_sha(a), _sha(b))  # the hub write re-projects A.md; B.md never changes
    assert after_edit[1] == before[1]
    second = _run(daemon, "render", str(b))
    assert "The sky is grey." in second.output and "The sky is blue." not in second.output
    assert (_sha(a), _sha(b)) == after_edit  # rendering itself changed nothing


def test_render_marks_a_missing_target_visibly(daemon, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    b = vault / "B.md"
    b.write_text("![[A.md#^tm-4cgfdxpi]]\n", encoding="utf-8")
    before = _sha(b)
    result = _run(daemon, "render", str(b))
    assert result.exit_code == 0, result.output
    assert "[unresolved: ^tm-4cgfdxpi (missing)]" in result.output
    assert _sha(b) == before


def test_render_marks_a_tombstoned_target(daemon, tmp_path):
    a, b, node_id = _vault(daemon, tmp_path)
    store.split_node(daemon["conn"], node_id, [{"node_type": "claim", "body": "successor"}])
    result = _run(daemon, "render", str(b))
    assert result.exit_code == 0, result.output
    assert f"[unresolved: ^tm-{node_id} (tombstone)]" in result.output


def test_render_inline_embeds_and_fences(daemon, tmp_path):
    a, b, node_id = _vault(daemon, tmp_path)
    b.write_text(
        f"see ![[A.md#^tm-{node_id}]] inline\n```\n![[A.md#^tm-{node_id}]]\n```\n",
        encoding="utf-8",
    )
    result = _run(daemon, "render", str(b))
    assert 'see "The sky is blue." (^tm-' in result.output
    assert f"```\n![[A.md#^tm-{node_id}]]\n```" in result.output  # a fenced example is left alone


def test_render_json_lists_each_embed(daemon, tmp_path):
    a, b, node_id = _vault(daemon, tmp_path)
    result = _run(daemon, "--json", "render", str(b))
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)["data"]
    (embed,) = data["embeds"]
    assert embed["id"] == node_id and embed["body"] == "The sky is blue."
    assert embed["status"] == "live" and embed["path"] == "A.md" and embed["line"] == 3


def test_render_unreadable_file_is_exit_3(daemon, tmp_path):
    assert _run(daemon, "render", str(tmp_path / "nope.md")).exit_code == 3


def test_render_help_says_it_does_not_modify_files(daemon):
    result = runner.invoke(cli_app, ["render", "--help"])
    assert "does not modify files" in result.output.replace("\n", " ")
