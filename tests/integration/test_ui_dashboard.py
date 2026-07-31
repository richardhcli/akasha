"""Playwright integration test: metrics dashboard view (task T10.1, spec M10 / §7 / §9 story 6).

Drives a real headless Chromium browser (``pytest-playwright``'s sync
``page`` fixture) against a live daemon, same pattern as
``test_ui_smoke.py``: a live ``uvicorn.Server`` (background thread) built
from a ``Config(db_path=...)`` -- deliberately NOT an injected connection,
so request handling uses the production per-request-connection path
(``deps.get_conn``, task T8.5b).

Seeds real state over the ``/v1`` HTTP API (create two nodes, link with a
facet span -- the same T7.7 capture flow ``test_ui_smoke.py`` uses -- break
the facet, then resolve the resulting review) so ``GET /v1/metrics``
(T9.2) returns non-trivial ``facet_coverage``/``review_inflow_7d``/
``review_resolved_7d`` values, then asserts the dashboard renders all four
metric groups the build-plan Goal/DoD name (facet coverage, review inflow
vs. resolved + variance, violation rate, crossing rate) with exactly the
values ``GET /v1/metrics`` reports, cross-checked independently via
``httpx`` against the very same live daemon.

``GET /dashboard`` is now a real production route registered in
``src/akasha/api/app.py`` (T10.1 follow-up completion, same class as the
T8.1-T8.4/T9.2 ``app.py`` view-route precedent -- see the resolved
``docs/spec-questions.md`` T10.1 entry), so this test drives the app's
actual route directly via ``page.goto`` rather than a test-local stand-in.
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
from fastapi.testclient import TestClient
from playwright.sync_api import Page, expect

from akasha.api import auth
from akasha.api.app import create_app
from akasha.config import Config
from akasha.kernel import store


def test_dashboard_route_serves_shell() -> None:
    """Mirrors test_ui_node.py/test_ui_review.py/etc: static-shell check only."""
    conn = store.connect(":memory:", check_same_thread=False)
    store.run_migrations(conn)
    client = TestClient(create_app(conn=conn))
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    body = resp.text
    for container_id in (
        "dashboard-facet-coverage",
        "dashboard-review-economy",
        "dashboard-violation-rate",
        "dashboard-crossing-rate",
        "tm-auth-bar",  # debug-plan D5: token-entry affordance
    ):
        assert f'id="{container_id}"' in body
    assert "/static/app.js" in body
    assert "/static/htmx.min.js" in body


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
    db_file = tmp_path / "ui-dashboard.db"
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


def _seed_state(base_url: str, token: str) -> None:
    """Seed nodes/facet-link/break/resolve so /v1/metrics has non-trivial data.

    Mirrors test_ui_smoke.py's create -> link-with-span -> break -> resolve
    loop: gives facet_coverage a covered S2+ definition, review_inflow_7d /
    review_resolved_7d a real event each, and crossing_rate a non-zero
    node-creation count.
    """
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(base_url=base_url, headers=headers, timeout=10.0) as client:
        target_body = "The quick brown fox jumps over the lazy dog."
        span = "quick brown fox"
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
        facet_id = edge_resp.json()["facet_binding"]
        assert facet_id and facet_id != "*"

        target_after_link = client.get(f"/v1/nodes/{target['id']}")
        assert target_after_link.status_code == 200, target_after_link.text
        minted = next(
            f for f in target_after_link.json()["facets"] if f["facet_id"] == facet_id
        )

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

        reviews_resp = client.get("/v1/review", params={"status": "open", "node": sub["id"]})
        assert reviews_resp.status_code == 200, reviews_resp.text
        open_reviews = [
            r for r in reviews_resp.json()["reviews"] if r["cause_kind"] == "facet_break"
        ]
        assert len(open_reviews) == 1, open_reviews
        resolve_resp = client.post(
            f"/v1/review/{open_reviews[0]['id']}/resolve",
            json={"resolution": "still_holds"},
        )
        assert resolve_resp.status_code == 200, resolve_resp.text


def test_dashboard_renders_all_four_metric_groups(
    daemon: dict[str, Any], page: Page
) -> None:
    base_url = daemon["base_url"]
    token = daemon["token"]

    _seed_state(base_url, token)

    with httpx.Client(
        base_url=base_url, headers={"Authorization": f"Bearer {token}"}, timeout=10.0
    ) as client:
        metrics_resp = client.get("/v1/metrics")
        assert metrics_resp.status_code == 200, metrics_resp.text
        metrics = metrics_resp.json()

    # Guard the seeded premise before ever touching the browser: the four
    # groups this task must render should reflect real seeded activity, not
    # an all-zero fresh daemon.
    assert metrics["facet_coverage"] > 0.0, metrics
    assert metrics["review_inflow_7d"] >= 1, metrics
    assert metrics["review_resolved_7d"] >= 1, metrics
    assert metrics["crossing_rate"] > 0.0, metrics

    page.context.add_init_script(
        f"window.localStorage.setItem('tm_token', {json.dumps(token)});"
    )
    page.goto(f"{base_url}/dashboard")

    expect(page.locator("#dashboard-facet-coverage")).to_contain_text(
        f"{metrics['facet_coverage'] * 100:.1f}%"
    )

    review_economy = page.locator("#dashboard-review-economy")
    expect(review_economy).to_contain_text(f"Inflow (7d): {metrics['review_inflow_7d']}")
    expect(review_economy).to_contain_text(
        f"Resolved (7d): {metrics['review_resolved_7d']}"
    )
    expect(review_economy).to_contain_text(
        f"Inflow variance (30d): {metrics['inflow_variance_30d']:.3f}"
    )

    expect(page.locator("#dashboard-violation-rate")).to_contain_text(
        f"{metrics['violation_rate']:.3f}"
    )

    expect(page.locator("#dashboard-crossing-rate")).to_contain_text(
        f"{metrics['crossing_rate']:.3f} nodes/day"
    )
