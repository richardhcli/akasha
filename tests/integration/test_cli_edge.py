"""Integration tests for `akasha edge add`/`akasha edge rm` (task T14.2, spec §4.11/§4.12).

Both wrap already-shipped, already-tested endpoints (``POST /v1/edges`` /
``DELETE /v1/edges/{id}``) the same way every other mutating verb wraps its
endpoint -- pure HTTP client via ``_mutate`` (so ``--dry-run`` coverage is
exercised in ``tests/integration/test_cli_dry_run.py``, not duplicated
here). Reuses the live-daemon fixture pattern from
``tests/integration/test_cli_graph.py`` (a real ``uvicorn`` server on an
ephemeral localhost port, driven through the CLI's own typer entrypoint via
``typer.testing.CliRunner``).

Deliberately does NOT assert a client-side copy of the facet-binding
validation rule (spec §4.2) -- ``test_edge_add_justification_without_binding_surfaces_server_400``
below asserts the server's own ``E_INVALID`` message reaches the caller
verbatim, and the real (not assumed) exit code the shared
``_exit_code_for`` mapping already produces for every other ``E_INVALID``
response in this CLI -- see the ``# SPEC-QUESTION`` comment next to
``_exit_code_for`` in ``cli/main.py`` for why that is exit 1, not exit 4.
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


def _new(daemon: dict[str, Any], node_type: str, body: str) -> dict[str, Any]:
    result = _run(daemon, "new", node_type, body)
    assert result.exit_code == 0, result.output
    payload: dict[str, Any] = json.loads(result.output)
    return payload


# --- edge add ----------------------------------------------------------------


def test_edge_add_creates_real_edge_visible_in_neighborhood(daemon):
    src = _new(daemon, "claim", "source claim")
    dst = _new(daemon, "claim", "target claim")

    result = _run(daemon, "edge", "add", src["id"], dst["id"], "supports", "--facet-binding", "*")
    assert result.exit_code == 0, result.output
    edge = json.loads(result.output)
    assert edge["src"] == src["id"]
    assert edge["dst"] == dst["id"]
    assert edge["edge_type"] == "supports"
    assert edge["facet_binding"] == "*"

    neighborhood = _run(daemon, "neighborhood", src["id"])
    assert neighborhood.exit_code == 0, neighborhood.output
    assert f"{src['id']} -supports-> {dst['id']} (facet: *)" in neighborhood.output


def test_edge_add_facet_span_creates_real_facet_on_target(daemon):
    src = _new(daemon, "claim", "source claim")
    dst = _new(daemon, "definition", "a term with a highlighted span")

    span_text = "highlighted span"
    result = _run(
        daemon,
        "edge",
        "add",
        src["id"],
        dst["id"],
        "depends_on",
        "--facet-span",
        span_text,
    )
    assert result.exit_code == 0, result.output
    edge = json.loads(result.output)
    facet_id = edge["facet_binding"]
    assert facet_id is not None
    assert facet_id != "*"

    got = _run(daemon, "get", dst["id"])
    assert got.exit_code == 0, got.output
    dst_after = json.loads(got.output)
    assert len(dst_after["facets"]) == 1, dst_after["facets"]  # dst started with zero facets
    matching = [f for f in dst_after["facets"] if f["facet_id"] == facet_id]
    assert len(matching) == 1, dst_after["facets"]
    assert matching[0]["span"] == span_text


def test_edge_add_justification_without_binding_surfaces_server_400(daemon):
    """No client-side copy of the facet-binding rule (spec §4.2): the
    server's own 400 E_INVALID message must reach the caller verbatim.

    The exit code asserted here is the real, observed exit code produced
    by the CLI's existing, unmodified ``_exit_code_for`` mapping for a
    400/``E_INVALID`` response (the same mapping every other verb's own
    validation errors already go through) -- see the ``# SPEC-QUESTION``
    next to ``_exit_code_for`` in ``cli/main.py``.
    """
    src = _new(daemon, "claim", "source claim")
    dst = _new(daemon, "claim", "target claim")

    result = _run(daemon, "edge", "add", src["id"], dst["id"], "supports")
    assert result.exit_code == 1, result.output
    assert "facet_binding" in result.output, result.output

    # No edge was actually created.
    neighborhood = _run(daemon, "neighborhood", src["id"])
    assert neighborhood.exit_code == 0, neighborhood.output
    assert neighborhood.output.strip() == ""


def test_edge_add_composes_without_binding_succeeds(daemon):
    """`composes`/`redirects_to` allow a None facet_binding (spec §4.2)."""
    src = _new(daemon, "definition", "parent")
    dst = _new(daemon, "definition", "child")

    result = _run(daemon, "edge", "add", src["id"], dst["id"], "composes")
    assert result.exit_code == 0, result.output
    edge = json.loads(result.output)
    assert edge["facet_binding"] is None


def test_edge_add_json_flag_emits_cli_v1_schema(daemon):
    src = _new(daemon, "claim", "source claim")
    dst = _new(daemon, "claim", "target claim")

    result = runner.invoke(
        cli_app,
        [
            "--base-url",
            daemon["base_url"],
            "--token",
            daemon["token"],
            "--json",
            "edge",
            "add",
            src["id"],
            dst["id"],
            "supports",
            "--facet-binding",
            "*",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema"] == "cli/v1"
    assert payload["ok"] is True
    assert payload["data"]["src"] == src["id"]
    assert payload["data"]["dst"] == dst["id"]


# --- edge rm -------------------------------------------------------------


def test_edge_rm_retracts_edge_and_leaves_nodes_live(daemon):
    src = _new(daemon, "claim", "source claim")
    dst = _new(daemon, "claim", "target claim")
    add_result = _run(
        daemon, "edge", "add", src["id"], dst["id"], "supports", "--facet-binding", "*"
    )
    assert add_result.exit_code == 0, add_result.output
    edge_id = json.loads(add_result.output)["id"]

    before = _run(daemon, "neighborhood", src["id"])
    assert f"{src['id']} -supports-> {dst['id']}" in before.output

    rm_result = _run(daemon, "edge", "rm", edge_id)
    assert rm_result.exit_code == 0, rm_result.output
    rm_payload = json.loads(rm_result.output)
    assert rm_payload["id"] == edge_id
    assert rm_payload["retracted"] is True

    after = _run(daemon, "neighborhood", src["id"])
    assert after.exit_code == 0, after.output
    assert after.output.strip() == ""

    src_after = json.loads(_run(daemon, "get", src["id"]).output)
    dst_after = json.loads(_run(daemon, "get", dst["id"]).output)
    assert src_after["status"] == "live"
    assert dst_after["status"] == "live"


def test_edge_rm_missing_edge_returns_exit_3(daemon):
    result = _run(daemon, "edge", "rm", "nope2345")
    assert result.exit_code == 3, result.output
