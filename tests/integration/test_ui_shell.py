"""Integration tests for the UI shell + static serving (task T8.1, spec §4.13)."""

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


def test_root_serves_ui_shell():
    client = TestClient(_app())
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    body = resp.text
    assert "/static/htmx.min.js" in body
    assert "/static/app.js" in body
    assert 'id="app"' in body


def test_static_app_js_served():
    client = TestClient(_app())
    resp = client.get("/static/app.js")
    assert resp.status_code == 200


def test_static_htmx_served():
    client = TestClient(_app())
    resp = client.get("/static/htmx.min.js")
    assert resp.status_code == 200
    assert len(resp.content) > 1000
