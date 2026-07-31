"""Playwright integration test: /search?q= hydrates + auto-runs (debug-plan D5b).

Found via a dogfood pass: navigating directly to a URL like
``/search?q=weather`` rendered the bare, empty search form -- the query
param was never read on page load, only on a real form ``submit`` event.
That meant a bookmarked or shared search URL silently did nothing until the
user retyped the same query and hit Search again. ``initSearchView`` now
reads ``?q=`` via ``URLSearchParams`` on load, hydrates the input, and runs
the same search immediately if present.
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
from playwright.sync_api import Page, expect

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
def daemon(tmp_path: Any) -> Iterator[dict[str, Any]]:
    db_file = tmp_path / "ui-search-deep-link.db"
    setup_conn = store.connect(db_file, check_same_thread=False)
    store.run_migrations(setup_conn)
    human_secret = auth.mint_secret()
    _insert_token(setup_conn, "humantoken", human_secret, "human")
    setup_conn.close()
    human_bearer = auth.format_bearer_token("humantoken", human_secret)

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
        yield {"base_url": f"http://127.0.0.1:{port}", "token": human_bearer}
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_search_query_param_hydrates_and_auto_runs(
    daemon: dict[str, Any], page: Page
) -> None:
    base_url = daemon["base_url"]
    token = daemon["token"]

    with httpx.Client(
        base_url=base_url, headers={"Authorization": f"Bearer {token}"}, timeout=10.0
    ) as client:
        resp = client.post(
            "/v1/nodes",
            json={"node_type": "claim", "body": "a distinctive zzyzx marker claim"},
        )
        assert resp.status_code == 201, resp.text

    page.context.add_init_script(
        f"window.localStorage.setItem('tm_token', {json.dumps(token)});"
    )
    page.goto(f"{base_url}/search?q=zzyzx")

    expect(page.locator("#search-input")).to_have_value("zzyzx")
    expect(page.locator("#search-results")).to_contain_text("zzyzx marker claim")


def test_search_route_without_query_param_stays_empty(
    daemon: dict[str, Any], page: Page
) -> None:
    """No `?q=` at all -- unchanged behavior, no auto-search of an empty string."""
    token = daemon["token"]
    page.context.add_init_script(
        f"window.localStorage.setItem('tm_token', {json.dumps(token)});"
    )
    page.goto(f"{daemon['base_url']}/search")

    expect(page.locator("#search-input")).to_have_value("")
    expect(page.locator("#search-results")).to_be_empty()
