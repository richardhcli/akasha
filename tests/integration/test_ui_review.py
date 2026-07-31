"""Integration tests for the review view route + shell (task T8.3, spec §4.13).

Mirrors ``test_ui_node.py``: only proves the ``GET /review`` route serves the
static shell (containers, static assets, nav). The dynamic rendering (queue
items, one-click resolutions, daily-cap banner) is client-side JS exercised
in the browser/T8.5, not here.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from akasha.api.app import create_app
from akasha.config import Config
from akasha.kernel import store


def _app(config: Config | None = None):
    """Build an app with an injected in-memory DB so tests never touch $HOME."""
    conn = store.connect(":memory:", check_same_thread=False)
    store.run_migrations(conn)
    return create_app(config, conn=conn)


def test_review_route_serves_shell():
    client = TestClient(_app())
    resp = client.get("/review")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    body = resp.text
    for container_id in ("review-banner", "review-queue"):
        assert f'id="{container_id}"' in body
    assert "/static/app.js" in body
    assert "/static/htmx.min.js" in body
    assert 'id="tm-auth-bar"' in body  # debug-plan D5: token-entry affordance
    for label in ("Node", "Review", "Search", "Sync"):
        assert f">{label}<" in body
