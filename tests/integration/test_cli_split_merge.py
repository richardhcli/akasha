"""Integration tests for `akasha split` / `akasha merge` (task T14.4, spec §4.11/§4.12).

Both wrap the already-shipped ``POST /v1/nodes/{id}/split`` / ``/merge``
endpoints as pure HTTP clients through ``_mutate`` (``--dry-run`` coverage
lives in ``test_cli_dry_run.py``). Reuses the live-daemon fixture pattern from
``test_cli_edge.py``. The invariant under test is PRD §7.4: no refactor leaves
a dangling id -- inbound edges follow the redirect, and a split additionally
queues one reassignment review per inbound edge.
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
    return runner.invoke(cli_app, ["--base-url", daemon["base_url"], "--token", tok, *args])


def _node(daemon: dict[str, Any], body: str, node_type: str = "definition") -> str:
    return store.create_node(daemon["conn"], node_type, body).id


def _status(daemon: dict[str, Any], node_id: str) -> str:
    row = daemon["conn"].execute("SELECT status FROM nodes WHERE id=?", (node_id,)).fetchone()
    return str(row[0])


def _inbound(daemon: dict[str, Any], node_id: str) -> list[str]:
    return sorted(e.src for e in store.find_live_edges(daemon["conn"], dst=node_id))


def _open_reassignments(daemon: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in store.find_open_reviews(daemon["conn"]) if r["cause_kind"] == "reassignment"]


def test_split_creates_successors_a_redirect_and_one_review_per_inbound_edge(daemon):
    old = _node(daemon, "Tree: a plant, and also a data structure.")
    p1 = _node(daemon, "parent one")
    p2 = _node(daemon, "parent two")
    for parent in (p1, p2):
        store.create_edge(
            daemon["conn"],
            src=parent,
            dst=old,
            edge_type="composes",
            facet_binding=None,
            provenance="human",
        )

    result = _run(
        daemon, "split", old,
        "--part", "definition=Tree (botany): a plant.",
        "--part", "definition=Tree (CS): an acyclic graph.",
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    redirect = json.loads(
        daemon["conn"]
        .execute("SELECT successors FROM redirects WHERE old_id=?", (old,))
        .fetchone()[0]
    )
    successors = [
        r[0]
        for r in daemon["conn"].execute("SELECT id FROM nodes WHERE status='live' ORDER BY id")
        if r[0] not in (p1, p2)
    ]
    assert len(successors) == 2
    assert all(s in result.output for s in successors)
    assert old in result.output and "redirect" in result.output
    assert "2 reassignment review item(s)" in result.output
    assert redirect == successors or sorted(redirect) == sorted(successors)
    # the count printed matches what the server actually queued
    assert len(_open_reassignments(daemon)) == 2
    listing = _run(daemon, "review", "list")
    assert listing.exit_code == 0 and "reassignment" in listing.output
    # zero dangling: no live edge points at the tombstoned id
    assert _inbound(daemon, old) == []
    assert sorted(_inbound(daemon, successors[0]) + _inbound(daemon, successors[1])) == sorted(
        [p1, p2]
    )
    tomb = _status(daemon, old)
    assert tomb == "tombstone"


def test_split_reassignment_review_is_resolvable_via_the_cli(daemon):
    old = _node(daemon, "Two things in one.")
    parent = _node(daemon, "parent")
    store.create_edge(
        daemon["conn"], src=parent, dst=old, edge_type="composes",
        facet_binding=None, provenance="human",
    )  # fmt: skip
    assert _run(daemon, "split", old, "--part", "claim=one", "--part", "claim=two").exit_code == 0
    (review,) = _open_reassignments(daemon)

    resolved = _run(daemon, "review", "resolve", review["id"], "still_holds")

    assert resolved.exit_code == 0, resolved.output
    assert _open_reassignments(daemon) == []


def test_merge_keeps_the_path_id_and_redirects_the_rest(daemon):
    survivor = _node(daemon, "Tree.")
    dup1 = _node(daemon, "Tree, again.")
    dup2 = _node(daemon, "Tree, thrice.")
    parent = _node(daemon, "parent")
    store.create_edge(
        daemon["conn"], src=parent, dst=dup1, edge_type="composes",
        facet_binding=None, provenance="human",
    )  # fmt: skip

    result = _run(daemon, "merge", survivor, dup1, dup2)

    assert result.exit_code == 0, result.output
    assert f"merged {dup1}, {dup2} into {survivor}" in result.output
    assert _inbound(daemon, survivor) == [parent]  # the edge followed the redirect
    assert _inbound(daemon, dup1) == []
    status = {nid: _status(daemon, nid) for nid in (survivor, dup1, dup2)}
    assert status == {survivor: "live", dup1: "tombstone", dup2: "tombstone"}
    assert _open_reassignments(daemon) == []  # a merge has one survivor


def test_split_json_mode_returns_the_api_response_in_the_cli_envelope(daemon):
    old = _node(daemon, "x")
    result = runner.invoke(
        cli_app,
        ["--base-url", daemon["base_url"], "--token", daemon["token"], "--json",
         "split", old, "--part", "claim=a"],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True and list(payload["data"]["redirect"]) == [old]


def test_split_rejects_a_malformed_part_and_a_missing_part(daemon):
    old = _node(daemon, "x")
    bad = _run(daemon, "split", old, "--part", "no-equals-sign")
    assert bad.exit_code == 2
    none = _run(daemon, "split", old)
    assert none.exit_code == 2
    assert _status(daemon, old) == "live"


def test_split_unknown_node_is_exit_3(daemon):
    result = _run(daemon, "split", "zzzzzzzz", "--part", "claim=a")
    assert result.exit_code == 3


def test_agent_split_is_proposed_not_applied(daemon):
    old = _node(daemon, "x")
    result = _run(daemon, "split", old, "--part", "claim=a", token=daemon["agent_token"])
    assert result.exit_code == 0, result.output
    assert "proposed for human review" in result.output
    assert _status(daemon, old) == "live"
