"""CORS policy tests (debug-plan D4, spec-questions.md D4).

Empirically found via a live Obsidian-vault dogfood run: the daemon sent no
CORS headers at all, so every fetch from the Obsidian plugin's renderer
origin (``app://obsidian.md``) failed the browser's preflight check before
reaching a route. Narrowest reading (logged in ``docs/spec-questions.md``):
allow exactly that one fixed origin, never a wildcard -- this daemon carries
bearer tokens, and spec §3's localhost-only posture is never documented as
extending to "any web origin may call in".
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from akasha.api.app import create_app
from akasha.kernel import store

_OBSIDIAN_ORIGIN = "app://obsidian.md"


def _app():
    """Build an app with an injected in-memory DB, same pattern as test_health.py."""
    conn = store.connect(":memory:", check_same_thread=False)
    store.run_migrations(conn)
    return create_app(conn=conn)


def test_preflight_allows_the_obsidian_plugin_origin():
    client = TestClient(_app())
    resp = client.options(
        "/v1/sync/status",
        headers={
            "Origin": _OBSIDIAN_ORIGIN,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["access-control-allow-origin"] == _OBSIDIAN_ORIGIN


def test_actual_response_carries_the_allow_origin_header():
    client = TestClient(_app())
    resp = client.get("/health", headers={"Origin": _OBSIDIAN_ORIGIN})
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == _OBSIDIAN_ORIGIN


def test_preflight_rejects_an_untrusted_origin():
    client = TestClient(_app())
    resp = client.options(
        "/v1/sync/status",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    # starlette's CORSMiddleware still answers the preflight (200) but omits
    # Access-Control-Allow-Origin for a disallowed origin -- the browser is
    # what actually blocks the real request client-side on a missing header.
    assert "access-control-allow-origin" not in resp.headers


def test_no_wildcard_origin_is_ever_configured():
    """Guard against silently loosening D4's narrow allow-list to `*`."""
    from akasha.api.app import _CORS_ALLOWED_ORIGINS

    assert "*" not in _CORS_ALLOWED_ORIGINS
    assert _CORS_ALLOWED_ORIGINS == [_OBSIDIAN_ORIGIN]
