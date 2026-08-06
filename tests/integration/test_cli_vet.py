"""Integration tests for `akasha vet ID` (task T14.3, spec §4.11/§4.6/§4.12).

Wraps the already-shipped, already-tested ``POST /v1/nodes/{id}/vet`` --
the one endpoint spec §4.6/PRD §6 calls a *user act*: ``require_human``/∅
(never proposalized for an agent-class token, unlike every other mutating
endpoint's ``mutation_gate`` rewrite). Reuses the live-daemon fixture
pattern from ``tests/integration/test_cli_edge.py`` (a real ``uvicorn``
server on an ephemeral localhost port, driven through the CLI's own typer
entrypoint via ``typer.testing.CliRunner``), extended with a second,
agent-class token to exercise the 403 path for real.
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


def _run(daemon: dict[str, Any], *args: str, token: str | None = None) -> Any:
    tok = daemon["token"] if token is None else token
    full_args = ["--base-url", daemon["base_url"]]
    if tok:
        full_args += ["--token", tok]
    full_args += list(args)
    return runner.invoke(cli_app, full_args)


def _new(daemon: dict[str, Any], node_type: str, body: str) -> dict[str, Any]:
    result = _run(daemon, "new", node_type, body)
    assert result.exit_code == 0, result.output
    payload: dict[str, Any] = json.loads(result.output)
    return payload


def _get(daemon: dict[str, Any], node_id: str) -> dict[str, Any]:
    result = _run(daemon, "get", node_id)
    assert result.exit_code == 0, result.output
    payload: dict[str, Any] = json.loads(result.output)
    return payload


# --- vet with a human token -------------------------------------------------


def test_vet_with_human_token_sets_maturity_s4(daemon):
    node = _new(daemon, "claim", "a claim worth vetting")
    before = _get(daemon, node["id"])
    assert before["maturity"] != "S4"

    result = _run(daemon, "vet", node["id"])
    assert result.exit_code == 0, result.output

    after = _get(daemon, node["id"])
    assert after["maturity"] == "S4", after


def test_vet_plain_output_says_vetted_by_you_never_true(daemon):
    """PRD R9: 'system language says "vetted by you," never "true."'"""
    node = _new(daemon, "claim", "another claim")

    result = _run(daemon, "vet", node["id"])
    assert result.exit_code == 0, result.output
    assert "vetted by you" in result.output
    assert "true" not in result.output.lower()


def test_vet_json_flag_emits_cli_v1_envelope_with_real_data(daemon):
    node = _new(daemon, "claim", "a json-mode claim")

    result = runner.invoke(
        cli_app,
        [
            "--base-url",
            daemon["base_url"],
            "--token",
            daemon["token"],
            "--json",
            "vet",
            node["id"],
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema"] == "cli/v1"
    assert payload["ok"] is True
    assert payload["data"]["id"] == node["id"]
    assert payload["data"]["vetted"] is True
    assert payload["data"]["maturity"] == "S4"


def test_vet_missing_node_returns_exit_3(daemon):
    result = _run(daemon, "vet", "nope2345")
    assert result.exit_code == 3, result.output


# --- vet with an agent token -------------------------------------------------


def test_vet_with_agent_token_gets_real_403_never_proposalized(daemon):
    """``/vet`` is ``require_human``/∅ (spec §4.6/§4.11): an agent-class
    token must get the server's own 403, not a review-queue proposal."""
    node = _new(daemon, "claim", "should stay unvetted")

    result = _run(daemon, "vet", node["id"], token=daemon["agent_token"])
    assert result.exit_code != 0, result.output
    # `== 1` pins the currently observed, real exit code produced by the
    # existing, unmodified `_exit_code_for` mapping for a 403 E_HUMAN_ONLY
    # response (falls through to its default branch). This task leaves that
    # shared mapping untouched (see its own SPEC-QUESTION docstring in
    # cli/main.py) -- a future human ruling on the mapping is not a
    # regression here, just a reason to update this pin.
    assert result.exit_code == 1, result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "Traceback" not in result.output
    assert "proposed" not in result.output.lower()

    after = _get(daemon, node["id"])
    assert after["maturity"] != "S4"
    assert after["vetted"] is False


# --- vet with --dry-run -----------------------------------------------------


def test_vet_dry_run_mutates_nothing(daemon):
    node = _new(daemon, "claim", "should stay unvetted via dry run")

    result = runner.invoke(
        cli_app,
        [
            "--base-url",
            daemon["base_url"],
            "--token",
            daemon["token"],
            "--dry-run",
            "vet",
            node["id"],
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["dry_run"] is True
    assert payload["method"] == "POST"
    assert payload["path"] == f"/v1/nodes/{node['id']}/vet"
    assert payload["body"] is None

    after = _get(daemon, node["id"])
    assert after["maturity"] != "S4"
    assert after["vetted"] is False
