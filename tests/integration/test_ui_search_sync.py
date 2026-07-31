"""Integration tests for the search + sync view routes + shell (task T8.4, spec §4.13).

Mirrors ``test_ui_review.py``/``test_ui_node.py``: only proves ``GET /search``
and ``GET /sync`` serve their static shells (containers, static assets, nav).
The dynamic rendering (search results, per-sync-root violations, the
pause&diff inspector) is client-side JS, exercised in the browser, not here.
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


def test_search_route_serves_shell():
    client = TestClient(_app())
    resp = client.get("/search")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    body = resp.text
    for container_id in ("search-form", "search-input", "search-results"):
        assert f'id="{container_id}"' in body
    assert "/static/app.js" in body
    assert "/static/htmx.min.js" in body
    assert 'id="tm-auth-bar"' in body  # debug-plan D5: token-entry affordance
    for label in ("Node", "Review", "Search", "Sync"):
        assert f">{label}<" in body


def test_sync_route_serves_shell():
    client = TestClient(_app())
    resp = client.get("/sync")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    body = resp.text
    for container_id in ("sync-roots", "sync-unresolved"):
        assert f'id="{container_id}"' in body
    assert "/static/app.js" in body
    assert "/static/htmx.min.js" in body
    assert 'id="tm-auth-bar"' in body  # debug-plan D5: token-entry affordance
    for label in ("Node", "Review", "Search", "Sync"):
        assert f">{label}<" in body
