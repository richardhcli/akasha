"""Playwright UI smoke test: full loop, headless (task T8.5, spec §4.13).

Drives a real Chromium browser (``pytest-playwright``'s sync ``page``
fixture, headless by default) against a live daemon serving both the
``/v1`` JSON API and the static HTML UI (T8.1-T8.4). Exercises the full
loop the milestone's DoD names: create -> link with span -> break facet ->
see badge -> resolve.

Setup (create/link/break) is driven entirely over the HTTP API with the
minted human bearer token. The ``daemon`` fixture serves a live
``uvicorn.Server`` (background thread) built from a ``Config(db_path=...)``
-- deliberately NOT an injected connection -- so request handling uses the
production per-request-connection path (``deps.get_conn``). Only the
"see badge" / "resolve" steps touch the browser, reading through the same
live HTTP API app.js itself calls.

This test drives four *concurrent* ``fetch``es (the node view's
``Promise.all``) against the daemon; it passes because the daemon opens a
fresh WAL connection per request (concurrent readers + one writer) rather
than sharing one ``sqlite3.Connection`` across the ASGI threadpool, which
corrupts reads under concurrency. That fix (``api/deps.py`` /
``api/app.py`` / ``kernel/store.py`` ``busy_timeout``) is task T8.5b; see
SPEC-QUESTION T8.5b (amending spec §3) and ``tests/integration/test_concurrency.py``.
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
    # Seed the token into the file DB, then hand the daemon only the PATH (a
    # Config, NOT an injected connection) so it serves each request from a
    # FRESH per-request connection — the production model. This is what makes
    # the browser's concurrent Promise.all fetches safe (SPEC-QUESTION T8.5b);
    # an injected shared connection would route to the sequential-only path and
    # corrupt under the browser's concurrency.
    db_file = tmp_path / "ui-smoke.db"
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


def test_full_loop_create_link_break_badge_resolve(daemon: dict[str, Any], page: Page) -> None:
    base_url = daemon["base_url"]
    token = daemon["token"]
    headers = {"Authorization": f"Bearer {token}"}

    with httpx.Client(base_url=base_url, headers=headers, timeout=10.0) as client:
        # --- create -----------------------------------------------------
        target_body = "The quick brown fox jumps over the lazy dog."
        span = "quick brown fox"
        assert span in target_body

        target_resp = client.post(
            "/v1/nodes", json={"node_type": "definition", "body": target_body}
        )
        assert target_resp.status_code == 201, target_resp.text
        target = target_resp.json()

        sub_resp = client.post(
            "/v1/nodes", json={"node_type": "claim", "body": "subscriber claim"}
        )
        assert sub_resp.status_code == 201, sub_resp.text
        sub = sub_resp.json()

        # --- link with span (T7.7 facets-from-spans capture) ------------
        edge_resp = client.post(
            "/v1/edges",
            json={
                "src": sub["id"],
                "dst": target["id"],
                "edge_type": "supports",
                "provenance": "human",
                "facet_span": span,
            },
        )
        assert edge_resp.status_code == 201, edge_resp.text
        edge = edge_resp.json()
        facet_id = edge["facet_binding"]
        assert facet_id and facet_id != "*"

        target_after_link = client.get(f"/v1/nodes/{target['id']}")
        assert target_after_link.status_code == 200, target_after_link.text
        facets = target_after_link.json()["facets"]
        minted = next(f for f in facets if f["facet_id"] == facet_id)

        # --- break facet (major commit, version bump -> invalidate) -----
        patch_resp = client.patch(
            f"/v1/nodes/{target['id']}",
            json={
                "change_class": "major",
                "facets_touched": [facet_id],
                "facets": [
                    {
                        "facet_id": minted["facet_id"],
                        "name": minted["name"] + "-broken",
                        "span": minted["span"],
                        "version": minted["version"] + 1,
                    }
                ],
                "message": "break",
            },
        )
        assert patch_resp.status_code == 200, patch_resp.text

        # Guard the whole premise: exactly one open facet_break review on
        # the subscriber before we ever touch the browser.
        reviews_resp = client.get("/v1/review", params={"status": "open", "node": sub["id"]})
        assert reviews_resp.status_code == 200, reviews_resp.text
        open_reviews = reviews_resp.json()["reviews"]
        facet_break_reviews = [r for r in open_reviews if r["cause_kind"] == "facet_break"]
        assert len(facet_break_reviews) == 1, (
            f"expected exactly one open facet_break review on {sub['id']!r}, "
            f"got {open_reviews!r}"
        )

    # --- see badge (browser) --------------------------------------------
    # Set localStorage before any page script runs (app.js boots on
    # DOMContentLoaded and reads tm_token synchronously).
    page.context.add_init_script(
        f"window.localStorage.setItem('tm_token', {json.dumps(token)});"
    )
    page.goto(f"{base_url}/node?id={sub['id']}")

    badge = page.locator("#node-badge")
    expect(badge).to_contain_text("Stale -- needs recheck")
    badge_text = badge.inner_text()
    assert "true" not in badge_text.lower(), badge_text  # R9: badge copy never says "true"

    # --- resolve (browser) ------------------------------------------------
    page.goto(f"{base_url}/review")
    expect(page.locator("#review-queue")).to_contain_text(sub["id"])
    page.get_by_role("button", name="still_holds", exact=True).click()
    expect(page.get_by_text("No open reviews.")).to_be_visible()

    with httpx.Client(base_url=base_url, headers=headers, timeout=10.0) as client:
        final_reviews = client.get("/v1/review", params={"status": "open", "node": sub["id"]})
        assert final_reviews.status_code == 200, final_reviews.text
        assert final_reviews.json()["reviews"] == []
