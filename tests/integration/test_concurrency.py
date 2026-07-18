"""Concurrency regression guard for the daemon's DB connection model (task T8.5b).

Pins the root cause behind SPEC-QUESTION T8.5b (amending spec §3): a single
``sqlite3.Connection`` shared across the ASGI threadpool corrupts reads under
concurrent access. The Web UI's node view fires four ``fetch``es in parallel
(``Promise.all``), so this is a real, user-reachable path — before the fix,
concurrent requests intermittently returned 500 (``sqlite3.InterfaceError``),
401 (a valid token rejected from a corrupted ``tokens`` read in
``auth.authenticate``), and 404 (an existing node missing from a corrupted
read). The fix (``deps.get_conn`` opens a fresh WAL connection PER REQUEST)
lets concurrent readers proceed safely; this test drives real concurrent HTTP
requests against a live server and asserts every one succeeds.

This is the cheap, browser-free guard; ``test_ui_smoke.py`` proves the same
fix end-to-end through a real browser. Uses a ``Config(db_path=...)`` (NOT an
injected connection) so it exercises the production per-request path.
"""

from __future__ import annotations

import concurrent.futures
import socket
import threading
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import uvicorn

from akasha.api import auth
from akasha.api.app import create_app
from akasha.config import Config
from akasha.kernel import store


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _insert_token(conn: Any, token_id: str, secret: str, cls: str) -> None:
    conn.execute(
        "INSERT INTO tokens (id, name, class, secret_hash, rate_per_min, created_at, "
        "revoked_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (token_id, token_id, cls, auth.hash_secret(secret), None,
         "2026-01-01T00:00:00.000000+00:00", None),
    )
    conn.commit()


@pytest.fixture
def daemon(tmp_path: Any) -> Iterator[dict[str, Any]]:
    db_file = tmp_path / "concurrency.db"
    setup_conn = store.connect(db_file, check_same_thread=False)
    store.run_migrations(setup_conn)
    secret = auth.mint_secret()
    _insert_token(setup_conn, "humantoken", secret, "human")
    setup_conn.close()
    bearer = auth.format_bearer_token("humantoken", secret)

    # Config, NOT an injected connection: exercises the per-request path.
    fastapi_app = create_app(Config(db_path=db_file))
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
        yield {"base_url": f"http://127.0.0.1:{port}", "token": bearer}
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_concurrent_node_view_fetches_never_corrupt(daemon: dict[str, Any]) -> None:
    base_url = daemon["base_url"]
    headers = {"Authorization": f"Bearer {daemon['token']}"}

    with httpx.Client(base_url=base_url, headers=headers, timeout=10.0) as client:
        resp = client.post("/v1/nodes", json={"node_type": "claim", "body": "concurrency node"})
        assert resp.status_code == 201, resp.text
        node_id = resp.json()["id"]

    # The exact set the node view fetches in parallel, plus the auth read each
    # request performs. Many rounds of 4-way concurrency; a shared-connection
    # daemon returned a mix of 500/401/404 here (~10%). Per-request WAL
    # connections must return 200 for every one.
    paths = [
        f"/v1/nodes/{node_id}",
        f"/v1/nodes/{node_id}/neighborhood",
        f"/v1/nodes/{node_id}/history",
        f"/v1/review?status=open&node={node_id}",
    ]

    def hit(path: str) -> int:
        return httpx.get(base_url + path, headers=headers, timeout=10.0).status_code

    statuses: list[int] = []
    for _ in range(50):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            statuses.extend(ex.map(hit, paths))

    bad = [s for s in statuses if s != 200]
    assert not bad, f"{len(bad)}/{len(statuses)} concurrent requests failed: {sorted(set(bad))}"
