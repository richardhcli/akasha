"""Integration tests for `akasha neighborhood`/`akasha history` (task T14.1, spec §4.11/§4.12).

Both wrap already-shipped, already-tested endpoints (``GET
/nodes/{id}/neighborhood?hops=`` / ``GET /nodes/{id}/history``) the same way
every other verb wraps its endpoint -- pure HTTP client, no new server-side
logic, no ``_mutate`` (both are read-only). Reuses the live-daemon fixture
pattern from ``tests/integration/test_cli.py``/``test_cli_sync_add.py`` (a
real ``uvicorn`` server on an ephemeral localhost port, driven through the
CLI's own typer entrypoint via ``typer.testing.CliRunner``).
"""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator
from typing import Any

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


# --- neighborhood ------------------------------------------------------------


def test_neighborhood_round_trip_plain_output(daemon):
    conn = daemon["conn"]
    src = json.loads(_run(daemon, "new", "claim", "source claim").output)
    dst = json.loads(_run(daemon, "new", "claim", "target claim").output)
    store.create_edge(conn, src["id"], dst["id"], "supports", "*", "human")

    result = _run(daemon, "neighborhood", src["id"])
    assert result.exit_code == 0, result.output
    assert result.output.isascii()
    lines = [line for line in result.output.splitlines() if line]
    assert any(
        line == f"{src['id']} -supports-> {dst['id']} (facet: *)" for line in lines
    ), result.output


def test_neighborhood_hops_option_is_forwarded(daemon):
    conn = daemon["conn"]
    a = json.loads(_run(daemon, "new", "claim", "a").output)
    b = json.loads(_run(daemon, "new", "claim", "b").output)
    c = json.loads(_run(daemon, "new", "claim", "c").output)
    store.create_edge(conn, a["id"], b["id"], "supports", "*", "human")
    store.create_edge(conn, b["id"], c["id"], "supports", "*", "human")

    one_hop = _run(daemon, "neighborhood", a["id"], "--hops", "1")
    assert one_hop.exit_code == 0, one_hop.output
    assert c["id"] not in one_hop.output

    two_hop = _run(daemon, "neighborhood", a["id"], "--hops", "2")
    assert two_hop.exit_code == 0, two_hop.output
    assert c["id"] in two_hop.output


def test_neighborhood_json_flag_emits_cli_v1_schema(daemon):
    conn = daemon["conn"]
    src = json.loads(_run(daemon, "new", "claim", "source claim").output)
    dst = json.loads(_run(daemon, "new", "claim", "target claim").output)
    store.create_edge(conn, src["id"], dst["id"], "supports", "*", "human")

    result = runner.invoke(
        cli_app,
        [
            "--base-url",
            daemon["base_url"],
            "--token",
            daemon["token"],
            "--json",
            "neighborhood",
            src["id"],
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema"] == "cli/v1"
    assert payload["ok"] is True
    assert src["id"] in payload["data"]["node_ids"]
    assert dst["id"] in payload["data"]["node_ids"]
    edge_types = [edge["edge_type"] for edge in payload["data"]["edges"]]
    assert "supports" in edge_types


def test_neighborhood_missing_node_returns_exit_3(daemon):
    result = _run(daemon, "neighborhood", "nope2345")
    assert result.exit_code == 3, result.output


def test_neighborhood_with_no_edges_prints_nothing(daemon):
    node = json.loads(_run(daemon, "new", "claim", "lonely node").output)
    result = _run(daemon, "neighborhood", node["id"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == ""


# --- history -------------------------------------------------------------


def test_history_round_trip_plain_output(daemon):
    node = json.loads(_run(daemon, "new", "claim", "v1 body").output)
    _run(daemon, "set", node["id"], "--body", "v2 body", "--class", "minor")

    result = _run(daemon, "history", node["id"])
    assert result.exit_code == 0, result.output
    assert result.output.isascii()
    lines = [line for line in result.output.splitlines() if line]
    assert len(lines) == 2
    fields_genesis = lines[0].split(" ")
    assert fields_genesis[1] == "major"  # genesis commit (spec §4.5)
    fields_second = lines[1].split(" ")
    assert fields_second[1] == "minor"


def test_history_json_flag_emits_cli_v1_schema(daemon):
    node = json.loads(_run(daemon, "new", "claim", "v1 body").output)
    result = runner.invoke(
        cli_app,
        [
            "--base-url",
            daemon["base_url"],
            "--token",
            daemon["token"],
            "--json",
            "history",
            node["id"],
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema"] == "cli/v1"
    assert payload["ok"] is True
    assert len(payload["data"]["history"]) == 1
    assert payload["data"]["history"][0]["change_class"] == "major"  # genesis commit


def test_history_missing_node_returns_exit_3(daemon):
    result = _run(daemon, "history", "nope2345")
    assert result.exit_code == 3, result.output
