"""Playwright integration tests: search results + review items link to the node view.

Debug-plan D8.

Found via a holistic dogfood pass (post-D4/D5/D6): after D5 gave the web UI
a real way to authenticate, the next natural action -- clicking a search hit
or a review item to see the full node -- had no affordance at all. Both
``renderSearchResults`` and ``renderReviewItem`` (``app.js``) rendered a
node's id as plain text; the only way to reach ``/node?id=<id>`` was to edit
the URL by hand. Both now wrap the id in a plain ``<a href="/node?id=...">``
(``nodeLink`` helper) -- no new data, no new endpoint, just linking a value
the existing `/v1/search` and `/v1/review` responses already carry.

Same live-daemon-via-Playwright pattern as ``test_ui_search_deep_link.py``.
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
    db_file = tmp_path / "ui-node-links.db"
    setup_conn = store.connect(db_file, check_same_thread=False)
    store.run_migrations(setup_conn)
    human_secret = auth.mint_secret()
    _insert_token(setup_conn, "humantoken", human_secret, "human")
    agent_secret = auth.mint_secret()
    _insert_token(setup_conn, "agenttoken", agent_secret, "agent")
    setup_conn.close()
    human_bearer = auth.format_bearer_token("humantoken", human_secret)
    agent_bearer = auth.format_bearer_token("agenttoken", agent_secret)

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
        yield {
            "base_url": f"http://127.0.0.1:{port}",
            "token": human_bearer,
            "agent_token": agent_bearer,
        }
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_search_result_links_to_node_view(daemon: dict[str, Any], page: Page) -> None:
    base_url = daemon["base_url"]
    token = daemon["token"]

    with httpx.Client(
        base_url=base_url, headers={"Authorization": f"Bearer {token}"}, timeout=10.0
    ) as client:
        resp = client.post(
            "/v1/nodes",
            json={"node_type": "claim", "body": "a distinctive qwyxor marker claim"},
        )
        assert resp.status_code == 201, resp.text
        node_id = resp.json()["id"]

    page.context.add_init_script(
        f"window.localStorage.setItem('tm_token', {json.dumps(token)});"
    )
    page.goto(f"{base_url}/search?q=qwyxor")

    link = page.locator("#search-results a", has_text=node_id)
    expect(link).to_be_visible()
    expect(link).to_have_attribute("href", f"/node?id={node_id}")

    link.click()
    expect(page).to_have_url(f"{base_url}/node?id={node_id}")
    expect(page.locator("#node-body")).to_contain_text("qwyxor marker claim")


def test_review_item_links_to_node_view(daemon: dict[str, Any], page: Page) -> None:
    base_url = daemon["base_url"]
    token = daemon["token"]
    agent_token = daemon["agent_token"]

    with httpx.Client(base_url=base_url, timeout=10.0) as client:
        resp = client.post(
            "/v1/nodes",
            json={"node_type": "claim", "body": "original body before proposal"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201, resp.text
        node_id = resp.json()["id"]

        # An agent-class token's mutating call is proposal-rewritten (spec
        # §4.11) into a review item with cause_kind=proposal, node_id set to
        # the existing node -- exactly the "node_id present" case D8 covers.
        resp = client.patch(
            f"/v1/nodes/{node_id}",
            json={"body": "agent-proposed revision", "change_class": "minor", "facets_touched": []},
            headers={"Authorization": f"Bearer {agent_token}"},
        )
        assert resp.status_code == 202, resp.text

    page.context.add_init_script(
        f"window.localStorage.setItem('tm_token', {json.dumps(token)});"
    )
    page.goto(f"{base_url}/review")

    link = page.locator("#review-queue a", has_text=node_id)
    expect(link).to_be_visible()
    expect(link).to_have_attribute("href", f"/node?id={node_id}")

    link.click()
    expect(page).to_have_url(f"{base_url}/node?id={node_id}")
    expect(page.locator("#node-body")).to_contain_text("original body before proposal")


def test_node_view_neighborhood_is_navigable(daemon: dict[str, Any], page: Page) -> None:
    """T14.5: the 1-hop neighborhood is direction/type-grouped, shows each
    neighbor's node_type + body, and every neighbor is a working link
    (spec §4.13, PRD §7.5) -- finishing the D8 id-as-plain-text class of fix
    for the one view D8 never touched.
    """
    base_url = daemon["base_url"]
    token = daemon["token"]

    with httpx.Client(
        base_url=base_url, headers={"Authorization": f"Bearer {token}"}, timeout=10.0
    ) as client:
        center_resp = client.post(
            "/v1/nodes",
            json={"node_type": "claim", "body": "a distinctive zqorvix center claim"},
        )
        assert center_resp.status_code == 201, center_resp.text
        center_id = center_resp.json()["id"]

        # Inbound supports edge: supporter -supports-> center.
        supporter_resp = client.post(
            "/v1/nodes",
            json={"node_type": "evidence", "body": "a distinctive fyplenk supporting note"},
        )
        assert supporter_resp.status_code == 201, supporter_resp.text
        supporter_id = supporter_resp.json()["id"]
        supports_resp = client.post(
            "/v1/edges",
            json={
                "src": supporter_id,
                "dst": center_id,
                "edge_type": "supports",
                "facet_binding": "*",
                "provenance": "human",
            },
        )
        assert supports_resp.status_code == 201, supports_resp.text

        # Outbound composes edge: center -composes-> child.
        child_resp = client.post(
            "/v1/nodes",
            json={"node_type": "claim", "body": "a distinctive gwarnux child claim"},
        )
        assert child_resp.status_code == 201, child_resp.text
        child_id = child_resp.json()["id"]
        composes_resp = client.post(
            "/v1/edges",
            json={
                "src": center_id,
                "dst": child_id,
                "edge_type": "composes",
                "provenance": "human",
            },
        )
        assert composes_resp.status_code == 201, composes_resp.text

    page.context.add_init_script(
        f"window.localStorage.setItem('tm_token', {json.dumps(token)});"
    )
    page.goto(f"{base_url}/node?id={center_id}")

    neighborhood = page.locator("#node-neighborhood")
    inbound = neighborhood.locator(".neighborhood-inbound")
    outbound = neighborhood.locator(".neighborhood-outbound")
    expect(inbound).to_be_visible()
    expect(outbound).to_be_visible()

    supports_group = inbound.locator(".neighborhood-type-group", has_text="supports")
    expect(supports_group).to_contain_text("evidence")
    expect(supports_group).to_contain_text("fyplenk supporting note")
    expect(supports_group).to_contain_text("facet: *")
    supporter_link = supports_group.locator("a", has_text=supporter_id)
    expect(supporter_link).to_have_attribute("href", f"/node?id={supporter_id}")

    composes_group = outbound.locator(".neighborhood-type-group", has_text="composes")
    expect(composes_group).to_contain_text("claim")
    expect(composes_group).to_contain_text("gwarnux child claim")
    child_link = composes_group.locator("a", has_text=child_id)
    expect(child_link).to_have_attribute("href", f"/node?id={child_id}")

    child_link.click()
    expect(page).to_have_url(f"{base_url}/node?id={child_id}")
    expect(page.locator("#node-body")).to_contain_text("gwarnux child claim")


def test_node_view_neighborhood_degrades_on_failed_neighbor_fetch(
    daemon: dict[str, Any], page: Page
) -> None:
    """Step 4: a neighbor node fetch that 404s must still render that
    neighbor as a plain id link, never blank the whole section. Simulated by
    retracting the neighbor's only path to itself is not available via the
    API, so instead this asserts the degrade path directly: a neighbor id
    that GET /v1/nodes/{id} can't resolve (intercepted to fail) still shows
    up as a link.
    """
    base_url = daemon["base_url"]
    token = daemon["token"]

    with httpx.Client(
        base_url=base_url, headers={"Authorization": f"Bearer {token}"}, timeout=10.0
    ) as client:
        center_resp = client.post(
            "/v1/nodes",
            json={"node_type": "claim", "body": "center for degrade test wxyzqu"},
        )
        assert center_resp.status_code == 201, center_resp.text
        center_id = center_resp.json()["id"]
        neighbor_resp = client.post(
            "/v1/nodes",
            json={"node_type": "claim", "body": "neighbor for degrade test"},
        )
        assert neighbor_resp.status_code == 201, neighbor_resp.text
        neighbor_id = neighbor_resp.json()["id"]
        edge_resp = client.post(
            "/v1/edges",
            json={
                "src": center_id,
                "dst": neighbor_id,
                "edge_type": "composes",
                "provenance": "human",
            },
        )
        assert edge_resp.status_code == 201, edge_resp.text

    page.context.add_init_script(
        f"window.localStorage.setItem('tm_token', {json.dumps(token)});"
    )

    def fail_neighbor_fetch(route: Any) -> None:
        route.fulfill(status=500, body="{}")

    page.route(f"**/v1/nodes/{neighbor_id}", fail_neighbor_fetch)
    page.goto(f"{base_url}/node?id={center_id}")

    outbound = page.locator("#node-neighborhood .neighborhood-outbound")
    expect(outbound).to_be_visible()
    neighbor_link = outbound.locator("a", has_text=neighbor_id)
    expect(neighbor_link).to_have_attribute("href", f"/node?id={neighbor_id}")
