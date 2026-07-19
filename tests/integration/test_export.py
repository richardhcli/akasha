"""Integration tests for the export command (task T10.2).

Spec §4.11 ``GET /sync/export`` and §4.12 ``export`` verb (both added by
the 2026-07-18 T10.2 fable rulings -- see ``docs/spec-questions.md`` for
the full transport + scope resolutions). Follows the same "round-trip
against a *live* daemon via a real uvicorn server + the CLI's own typer
entrypoint" pattern as ``tests/integration/test_cli.py`` -- ``export`` is,
like every non-``daemon`` verb, a pure HTTP client (it must not import
``kernel/store.py`` and must not touch SQLite directly); these tests
exercise the real HTTP path end to end, no ASGI shortcuts. The daemon
fixture's ``conn`` is used only for test-side setup (seeding base
snapshots via ``sync.base_store.put``, the same pattern
``tests/integration/test_api.py``'s sync tests use) and for asserting the
read-only invariant directly against the review queue -- never as a
shortcut around the CLI's own HTTP path.
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
from akasha.contract.parser import parse
from akasha.contract.render import render
from akasha.kernel import store
from akasha.kernel.canonical import canonicalize_text
from akasha.kernel.ids import contract_anchor
from akasha.sync import base_store
from akasha.sync.reconcile import hub_state_for

runner = CliRunner()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _insert_token(conn: Any, token_id: str, secret: str, cls: str) -> None:
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
def daemon(tmp_path: Path) -> Iterator[dict[str, Any]]:
    conn = store.connect(tmp_path / "export.db", check_same_thread=False)
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
            "human": human_bearer,
            "agent": agent_bearer,
        }
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def _run(daemon: dict[str, Any], *args: str, token: str | None = None) -> Any:
    tok = daemon["human"] if token is None else token
    full_args = ["--base-url", daemon["base_url"]]
    if tok:
        full_args += ["--token", tok]
    full_args += list(args)
    return runner.invoke(cli_app, full_args)


def _managed(body: str) -> str:
    return canonicalize_text(f"---\ntm: 1\n---\n{body}")


def _register_root(daemon: dict[str, Any], name: str, root_path: Path) -> dict[str, Any]:
    resp = httpx.post(
        f"{daemon['base_url']}/v1/sync/roots",
        json={"name": name, "root_path": str(root_path)},
        headers={"Authorization": f"Bearer {daemon['human']}"},
    )
    assert resp.status_code == 201, resp.text
    result: dict[str, Any] = resp.json()
    return result


def _create_node(daemon: dict[str, Any], body: str = "hello", node_type: str = "claim") -> Any:
    resp = httpx.post(
        f"{daemon['base_url']}/v1/nodes",
        json={"node_type": node_type, "body": body},
        headers={"Authorization": f"Bearer {daemon['human']}"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _patch_node(daemon: dict[str, Any], node_id: str, body: str) -> None:
    resp = httpx.patch(
        f"{daemon['base_url']}/v1/nodes/{node_id}",
        json={"body": body, "change_class": "patch"},
        headers={"Authorization": f"Bearer {daemon['human']}"},
    )
    assert resp.status_code == 200, resp.text


# --- DoD: writes canonical markdown for every managed projection -----------


def test_export_writes_canonical_markdown_for_every_managed_projection(
    daemon: dict[str, Any], tmp_path: Path
) -> None:
    conn = daemon["conn"]
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    root = _register_root(daemon, "myvault", vault_dir)

    node_a = _create_node(daemon, body="alpha body")
    node_b = _create_node(daemon, body="beta body")

    path_a = str(vault_dir / "sub" / "a.md")
    path_b = str(vault_dir / "b.md")
    base_a = render(parse(_managed(f"alpha body {contract_anchor(node_a['id'])}\n")))
    base_b = render(parse(_managed(f"beta body {contract_anchor(node_b['id'])}\n")))
    base_store.put(conn, root["id"], path_a, base_a)
    base_store.put(conn, root["id"], path_b, base_b)

    out_dir = tmp_path / "export_out"
    result = _run(daemon, "export", "--md", str(out_dir))
    assert result.exit_code == 0, result.output

    file_a = out_dir / "myvault" / "sub" / "a.md"
    file_b = out_dir / "myvault" / "b.md"
    assert file_a.read_bytes().decode("utf-8") == base_a
    assert file_b.read_bytes().decode("utf-8") == base_b


def test_export_projects_current_hub_body_not_stale_base_text(
    daemon: dict[str, Any], tmp_path: Path
) -> None:
    """The exported text is the hub's CURRENT state, not the (possibly
    stale) last-agreed base snapshot -- a paused/out-of-contract vault file
    still exports the hub's view (spec §4.11 amendment)."""
    conn = daemon["conn"]
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    root = _register_root(daemon, "vault", vault_dir)

    node = _create_node(daemon, body="original body")
    node_id = node["id"]
    path = str(vault_dir / "note.md")
    base_text = render(parse(_managed(f"original body {contract_anchor(node_id)}\n")))
    base_store.put(conn, root["id"], path, base_text)

    # Advance the hub's head WITHOUT touching the base snapshot -- exactly
    # a "hub changed, vault/base stale" scenario.
    _patch_node(daemon, node_id, "updated body")

    out_dir = tmp_path / "export_out"
    result = _run(daemon, "export", "--md", str(out_dir))
    assert result.exit_code == 0, result.output

    written = (out_dir / "vault" / "note.md").read_bytes().decode("utf-8")
    assert "updated body" in written
    assert "original body" not in written
    assert written != base_text


# --- DoD: re-export is byte-stable ------------------------------------------


def test_export_is_byte_stable_across_re_export(daemon: dict[str, Any], tmp_path: Path) -> None:
    conn = daemon["conn"]
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    root = _register_root(daemon, "vault", vault_dir)
    node = _create_node(daemon, body="stable body")
    path = str(vault_dir / "note.md")
    base_text = render(parse(_managed(f"stable body {contract_anchor(node['id'])}\n")))
    base_store.put(conn, root["id"], path, base_text)

    out1 = tmp_path / "out1"
    out2 = tmp_path / "out2"
    result1 = _run(daemon, "export", "--md", str(out1))
    result2 = _run(daemon, "export", "--md", str(out2))
    assert result1.exit_code == 0, result1.output
    assert result2.exit_code == 0, result2.output

    bytes1 = (out1 / "vault" / "note.md").read_bytes()
    bytes2 = (out2 / "vault" / "note.md").read_bytes()
    assert bytes1 == bytes2 == base_text.encode("utf-8")


# --- DoD: GET /sync/export mutates nothing, not even review items ----------


def test_export_mutates_nothing_including_unprojectable_body_scenario(
    daemon: dict[str, Any], tmp_path: Path
) -> None:
    """Proves the read-only mode is not dead code: the exact same
    unprojectable-body scenario DOES enqueue a review when ``hub_state_for``
    is called without ``read_only=True`` (the control), but does NOT when
    driven through the export endpoint/CLI (the read-only path)."""
    conn = daemon["conn"]
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    root = _register_root(daemon, "vault", vault_dir)

    node = _create_node(daemon, body="line one")
    node_id = node["id"]
    # A hub body with an internal newline is unprojectable by the
    # line-oriented grammar (reconcile.hub_state_for's E_UNPROJECTABLE_BODY
    # case).
    _patch_node(daemon, node_id, "line one\nline two")

    path = str(vault_dir / "note.md")
    base_text = render(parse(_managed(f"line one {contract_anchor(node_id)}\n")))
    base_store.put(conn, root["id"], path, base_text)

    open_before = store.find_open_reviews(conn)
    assert open_before == []

    result = _run(daemon, "export", "--md", str(tmp_path / "out"))
    assert result.exit_code == 0, result.output

    open_after_export = store.find_open_reviews(conn)
    assert open_after_export == open_before  # identical before/after the call

    # The exported text keeps the ORIGINAL skeleton line for the
    # unprojectable block -- render() output is unaffected by read_only,
    # only the DB write is suppressed.
    written = (tmp_path / "out" / "vault" / "note.md").read_bytes().decode("utf-8")
    assert written == base_text

    # Control: the SAME scenario, driven through hub_state_for WITHOUT
    # read_only, DOES enqueue a review -- proving read_only actually
    # suppresses a real side effect, not a no-op.
    skeleton = parse(base_text)
    hub_state_for(conn, skeleton, path=path)
    open_after_control = store.find_open_reviews(conn)
    assert len(open_after_control) == 1
    assert open_after_control[0]["cause_kind"] == "violation"


def test_export_get_endpoint_allows_agent_tokens_and_mutates_nothing(
    daemon: dict[str, Any],
) -> None:
    """``/sync/*`` carries no ``∅`` marker in the §4.11 table -- agents may
    call export too (it's a read)."""
    resp = httpx.get(
        f"{daemon['base_url']}/v1/sync/export",
        headers={"Authorization": f"Bearer {daemon['agent']}"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "unfiled_node_count": 0}


def test_export_requires_auth_missing_token_401(daemon: dict[str, Any]) -> None:
    resp = httpx.get(f"{daemon['base_url']}/v1/sync/export")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "E_AUTH"


# --- response shape: ordering + unfiled_node_count --------------------------


def test_export_endpoint_orders_items_and_reports_unfiled_node_count(
    daemon: dict[str, Any], tmp_path: Path
) -> None:
    conn = daemon["conn"]
    zephyr_dir = tmp_path / "zephyr_vault"
    alpha_dir = tmp_path / "alpha_vault"
    zephyr_dir.mkdir()
    alpha_dir.mkdir()
    zephyr_root = _register_root(daemon, "zephyr", zephyr_dir)
    alpha_root = _register_root(daemon, "alpha", alpha_dir)

    filed_1 = _create_node(daemon, body="one")
    filed_2 = _create_node(daemon, body="two")
    unfiled = _create_node(daemon, body="never filed")

    zephyr_path = str(zephyr_dir / "z.md")
    alpha_path_b = str(alpha_dir / "bbb.md")
    alpha_path_a = str(alpha_dir / "aaa.md")
    base_store.put(
        conn,
        zephyr_root["id"],
        zephyr_path,
        render(parse(_managed(f"one {contract_anchor(filed_1['id'])}\n"))),
    )
    base_store.put(
        conn,
        alpha_root["id"],
        alpha_path_b,
        render(parse(_managed(f"two {contract_anchor(filed_2['id'])}\n"))),
    )
    # A second alpha-root file with no blocks at all (still a valid managed
    # base snapshot -- just contributes no anchor ids to `filed_ids`).
    base_store.put(conn, alpha_root["id"], alpha_path_a, _managed(""))

    resp = httpx.get(
        f"{daemon['base_url']}/v1/sync/export",
        headers={"Authorization": f"Bearer {daemon['human']}"},
    )
    assert resp.status_code == 200
    payload = resp.json()

    # Ordered by (sync_root name, POSIX root-relative path): "alpha" sorts
    # before "zephyr" regardless of registration order.
    assert [(item["sync_root"], item["relative_path"]) for item in payload["items"]] == [
        ("alpha", "aaa.md"),
        ("alpha", "bbb.md"),
        ("zephyr", "z.md"),
    ]

    assert payload["unfiled_node_count"] == 1  # only `unfiled`
    del unfiled  # referenced only for clarity of the scenario above


# --- CLI --json summary ------------------------------------------------------


def test_export_json_summary_reports_files_written_and_unfiled_node_count(
    daemon: dict[str, Any], tmp_path: Path
) -> None:
    conn = daemon["conn"]
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    root = _register_root(daemon, "vault", vault_dir)
    filed = _create_node(daemon, body="filed body")
    _create_node(daemon, body="unfiled body")  # never put into a base snapshot
    path = str(vault_dir / "note.md")
    base_text = render(parse(_managed(f"filed body {contract_anchor(filed['id'])}\n")))
    base_store.put(conn, root["id"], path, base_text)

    out_dir = tmp_path / "out"
    result = _run(daemon, "--json", "export", "--md", str(out_dir))
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema"] == "cli/v1"
    assert payload["ok"] is True
    assert payload["data"]["unfiled_node_count"] == 1
    assert payload["data"]["files_written"] == [str(out_dir / "vault" / "note.md")]


def test_export_writes_no_files_and_zero_unfiled_when_store_is_empty(
    daemon: dict[str, Any], tmp_path: Path
) -> None:
    out_dir = tmp_path / "out"
    result = _run(daemon, "--json", "export", "--md", str(out_dir))
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["data"]["files_written"] == []
    assert payload["data"]["unfiled_node_count"] == 0
