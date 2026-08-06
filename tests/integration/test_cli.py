"""Integration tests for the CLI verbs (task T4.8, spec §4.12).

Each verb round-trips against a *live* daemon: a real ``uvicorn`` server
bound to an ephemeral localhost port, invoked through the CLI's own typer
entrypoint via ``typer.testing.CliRunner`` (spec-blessed "the CLI is a
client, it must NOT import kernel/store.py or touch SQLite directly" —
these tests exercise the real HTTP path end to end, no ASGI shortcuts).

Tokens are seeded directly into the daemon's SQLite connection (same
pattern as ``tests/integration/test_api.py::_insert_token``) rather than
via the API, so these tests are independent of T4.6 (agent-token proposal
rewriting, landing in parallel) — every mutating round-trip here uses a
**human** token.
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


def _node_count(conn) -> int:  # type: ignore[no-untyped-def]
    return int(conn.execute("SELECT count(*) FROM nodes").fetchone()[0])


# --- new / get -----------------------------------------------------------


def test_new_and_get_round_trip(daemon):
    result = _run(daemon, "new", "claim", "hello world")
    assert result.exit_code == 0, result.output
    node = json.loads(result.output)
    assert node["body"].strip() == "hello world"
    assert node["node_type"] == "claim"
    assert node["maturity"] == "S0"

    got = _run(daemon, "get", node["id"])
    assert got.exit_code == 0, got.output
    fetched = json.loads(got.output)
    assert fetched["id"] == node["id"]
    assert fetched["body"].strip() == "hello world"


def test_new_with_task_flag_sets_task_state(daemon):
    result = _run(daemon, "new", "task", "do the thing", "--task")
    assert result.exit_code == 0, result.output
    node = json.loads(result.output)
    assert node["task_state"] == "open"


def test_new_with_facet_flag_creates_facets(daemon):
    result = _run(daemon, "new", "definition", "a term", "--facet", "term=a term")
    assert result.exit_code == 0, result.output
    node = json.loads(result.output)
    assert len(node["facets"]) == 1
    assert node["facets"][0]["name"] == "term"
    assert node["facets"][0]["span"] == "a term"
    assert node["facets"][0]["version"] == 1


def test_new_with_malformed_facet_is_usage_error(daemon):
    result = _run(daemon, "new", "claim", "body", "--facet", "no-equals-sign")
    assert result.exit_code == 2, result.output


def test_get_missing_node_returns_exit_3(daemon):
    result = _run(daemon, "get", "nope2345")
    assert result.exit_code == 3, result.output


def test_get_as_of_returns_earlier_body(daemon):
    node = json.loads(_run(daemon, "new", "claim", "v1 body").output)
    # fetch history via the raw API to find the genesis commit timestamp
    # (the CLI has no "history" verb — not in spec §4.12's verb list).
    history = httpx.get(
        f"{daemon['base_url']}/v1/nodes/{node['id']}/history",
        headers={"Authorization": f"Bearer {daemon['token']}"},
    ).json()["history"]
    genesis_ts = history[0]["ts"]

    _run(daemon, "set", node["id"], "--body", "v2 body")
    as_of = _run(daemon, "get", node["id"], "--as-of", genesis_ts)
    assert as_of.exit_code == 0, as_of.output
    assert json.loads(as_of.output)["body"].strip() == "v1 body"


# --- set -------------------------------------------------------------------


def test_set_round_trip_changes_body(daemon):
    node = json.loads(_run(daemon, "new", "claim", "original").output)
    result = _run(daemon, "set", node["id"], "--body", "revised", "--class", "minor")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["body"].strip() == "revised"

    got = json.loads(_run(daemon, "get", node["id"]).output)
    assert got["body"].strip() == "revised"


def test_set_touch_facet_is_forwarded(daemon):
    node = json.loads(_run(daemon, "new", "definition", "term", "--facet", "name=span text").output)
    result = _run(
        daemon, "set", node["id"], "--body", "term v2", "--class", "major", "--touch", "name"
    )
    assert result.exit_code == 0, result.output


def test_set_missing_node_returns_exit_3(daemon):
    result = _run(daemon, "set", "nope2345", "--body", "x")
    assert result.exit_code == 3, result.output


def test_set_task_state_done_round_trip(daemon):
    """T13.4 DoD: `akasha set <id> --task-state done` closes a real task
    against a live daemon, and a subsequent `akasha get <id>` reflects it."""
    node = json.loads(_run(daemon, "new", "task", "do the thing", "--task").output)
    assert node["task_state"] == "open"

    result = _run(daemon, "set", node["id"], "--task-state", "done")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["task_state"] == "done"

    got = json.loads(_run(daemon, "get", node["id"]).output)
    assert got["task_state"] == "done"

    # re-opening round-trips too.
    reopened = _run(daemon, "set", node["id"], "--task-state", "open")
    assert reopened.exit_code == 0, reopened.output
    assert json.loads(reopened.output)["task_state"] == "open"


def test_set_omitting_task_state_leaves_it_unchanged(daemon):
    """Omitted --task-state must send today's exact body (no `task_state`
    key at all) -- the server's `model_fields_set` presence check (T13.1)
    then leaves the existing task_state untouched."""
    node = json.loads(_run(daemon, "new", "task", "do the thing", "--task").output)
    assert node["task_state"] == "open"

    result = _run(daemon, "set", node["id"], "--body", "revised body")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["task_state"] == "open"

    got = json.loads(_run(daemon, "get", node["id"]).output)
    assert got["task_state"] == "open"


def test_set_task_state_invalid_value_is_usage_error(daemon):
    node = json.loads(_run(daemon, "new", "task", "do the thing", "--task").output)
    result = _run(daemon, "set", node["id"], "--task-state", "bogus")
    assert result.exit_code == 2, result.output


def test_set_dry_run_mutates_nothing(daemon):
    node = json.loads(_run(daemon, "new", "claim", "original").output)
    result = runner.invoke(
        cli_app,
        [
            "--base-url",
            daemon["base_url"],
            "--token",
            daemon["token"],
            "--dry-run",
            "set",
            node["id"],
            "--body",
            "should not persist",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["method"] == "PATCH"
    assert payload["path"] == f"/v1/nodes/{node['id']}"
    assert payload["body"]["body"] == "should not persist"

    got = json.loads(_run(daemon, "get", node["id"]).output)
    assert got["body"].strip() == "original"  # unchanged


# --- rm ----------------------------------------------------------------


def test_rm_s0_node_then_get_returns_exit_3(daemon):
    node = json.loads(_run(daemon, "new", "claim", "throwaway").output)
    result = _run(daemon, "rm", node["id"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["deleted"] is True

    got = _run(daemon, "get", node["id"])
    assert got.exit_code == 3, got.output


def test_rm_missing_node_returns_exit_3(daemon):
    result = _run(daemon, "rm", "nope2345")
    assert result.exit_code == 3, result.output


def test_rm_s1_without_redirect_returns_exit_4(daemon):
    conn = daemon["conn"]
    target = json.loads(_run(daemon, "new", "claim", "target").output)
    source = json.loads(_run(daemon, "new", "claim", "source").output)
    store.create_edge(conn, source["id"], target["id"], "supports", "*", "human")

    result = _run(daemon, "rm", target["id"])
    assert result.exit_code == 4, result.output


def test_rm_s1_with_redirect_succeeds(daemon):
    conn = daemon["conn"]
    target = json.loads(_run(daemon, "new", "claim", "target").output)
    successor = json.loads(_run(daemon, "new", "claim", "successor").output)
    source = json.loads(_run(daemon, "new", "claim", "source").output)
    store.create_edge(conn, source["id"], target["id"], "supports", "*", "human")

    result = _run(daemon, "rm", target["id"], "--redirect-to", successor["id"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["deleted"] is True


def test_rm_dry_run_mutates_nothing_and_never_hits_network(daemon):
    node = json.loads(_run(daemon, "new", "claim", "keep me").output)
    before = _node_count(daemon["conn"])
    result = runner.invoke(
        cli_app,
        [
            # deliberately unreachable base-url: proves dry-run never sends
            # a request at all, not just "sends but server ignores it"
            "--base-url",
            "http://127.0.0.1:1",
            "--token",
            daemon["token"],
            "--dry-run",
            "rm",
            node["id"],
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["method"] == "DELETE"
    assert payload["path"] == f"/v1/nodes/{node['id']}"

    after = _node_count(daemon["conn"])
    assert after == before


# --- search --------------------------------------------------------------


def test_search_round_trip(daemon):
    _run(daemon, "new", "claim", "the quick brown fox jumps")
    _run(daemon, "new", "claim", "an unrelated sentence about oceans")
    result = _run(daemon, "search", "fox")
    assert result.exit_code == 0, result.output
    results = json.loads(result.output)["results"]
    assert len(results) == 1
    assert "fox" in results[0]["body"]


def test_search_no_match_returns_empty_results(daemon):
    _run(daemon, "new", "claim", "the quick brown fox jumps")
    result = _run(daemon, "search", "zzznomatchzzz")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["results"] == []


# --- token -----------------------------------------------------------------


def test_token_create_list_revoke_round_trip(daemon):
    created = _run(daemon, "token", "create", "ci-bot", "--class", "agent")
    assert created.exit_code == 0, created.output
    token = json.loads(created.output)
    assert token["name"] == "ci-bot"
    assert token["class"] == "agent"
    assert "bearer_token" in token
    assert "secret_hash" not in token

    listed = _run(daemon, "token", "list")
    assert listed.exit_code == 0, listed.output
    ids_ = [t["id"] for t in json.loads(listed.output)["tokens"]]
    assert token["id"] in ids_

    revoked = _run(daemon, "token", "revoke", token["id"])
    assert revoked.exit_code == 0, revoked.output
    assert json.loads(revoked.output)["revoked"] is True


def test_token_create_bad_class_is_usage_error(daemon):
    result = _run(daemon, "token", "create", "x", "--class", "not-a-class")
    assert result.exit_code == 2, result.output


def test_token_create_dry_run_mutates_nothing(daemon):
    before = json.loads(_run(daemon, "token", "list").output)["tokens"]
    result = runner.invoke(
        cli_app,
        [
            "--base-url",
            daemon["base_url"],
            "--token",
            daemon["token"],
            "--dry-run",
            "token",
            "create",
            "should-not-exist",
            "--class",
            "agent",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["method"] == "POST"
    assert payload["path"] == "/v1/tokens"

    after = json.loads(_run(daemon, "token", "list").output)["tokens"]
    assert len(after) == len(before)


def test_token_revoke_missing_returns_exit_3(daemon):
    result = _run(daemon, "token", "revoke", "nope2345")
    assert result.exit_code == 3, result.output


# --- review (SPEC-QUESTION T4.8: /v1/review does not exist until T7.5) -----


def test_review_list_round_trips(daemon):
    # The /v1/review route now exists (landed by T8.0, the review-API task
    # inserted in M8). This previously asserted graceful FAILURE while the
    # route was unimplemented ("until T7.5 lands the route"); now the verb
    # round-trips against the live endpoint. An empty open queue is a valid
    # success (exit 0), and the CLI still never crashes with a traceback.
    result = _run(daemon, "review", "list")
    assert result.exit_code == 0, result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "reviews" in result.output


# --- global flags ------------------------------------------------------------


def test_json_flag_emits_cli_v1_schema(daemon):
    result = runner.invoke(
        cli_app,
        [
            "--base-url",
            daemon["base_url"],
            "--token",
            daemon["token"],
            "--json",
            "new",
            "claim",
            "json mode body",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema"] == "cli/v1"
    assert payload["ok"] is True
    assert payload["data"]["body"].strip() == "json mode body"


def test_json_flag_emits_cli_v1_schema_on_error(daemon):
    result = runner.invoke(
        cli_app,
        [
            "--base-url",
            daemon["base_url"],
            "--token",
            daemon["token"],
            "--json",
            "get",
            "nope2345",
        ],
    )
    assert result.exit_code == 3, result.output
    payload = json.loads(result.output)
    assert payload["schema"] == "cli/v1"
    assert payload["ok"] is False
    assert payload["error"]["code"] == "E_NOT_FOUND"


def test_missing_required_args_is_usage_error(daemon):
    result = runner.invoke(
        cli_app, ["--base-url", daemon["base_url"], "--token", daemon["token"], "new"]
    )
    assert result.exit_code == 2, result.output


def test_missing_token_maps_to_exit_1(daemon):
    result = _run(daemon, "get", "whatever1", token="")
    assert result.exit_code == 1, result.output
