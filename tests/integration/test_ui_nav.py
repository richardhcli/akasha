"""Integration test: every UI view's nav bar links to every other view (debug-plan D9).

Each of the six views (``/``, ``/node``, ``/review``, ``/search``, ``/sync``,
``/dashboard``) is served from its own standalone template file (no shared
Jinja base/`{% extends %}` -- see ``src/akasha/api/app.py``'s six
``ui_*`` route handlers, each reading a different
``src/akasha/ui/templates/*.html`` file directly). That means the `<nav>`
block is physically duplicated six times; this test guards against the
copies drifting out of sync with each other again (debug-plan D9: `/dashboard`
was added, in its own template, with a correct 5-link nav, but the other
five templates' nav blocks were never updated to add the new link).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from akasha.api.app import create_app
from akasha.config import Config
from akasha.kernel import store

_ROUTES = ["/", "/node", "/review", "/search", "/sync", "/dashboard"]

_EXPECTED_NAV_LINKS = {
    "/node": "Node",
    "/review": "Review",
    "/search": "Search",
    "/sync": "Sync",
    "/dashboard": "Dashboard",
}


def _app():
    """Build an app with an injected in-memory DB so tests never touch $HOME."""
    conn = store.connect(":memory:", check_same_thread=False)
    store.run_migrations(conn)
    return create_app(Config(), conn=conn)


def test_every_view_nav_links_to_every_view():
    client = TestClient(_app())
    for route in _ROUTES:
        resp = client.get(route)
        assert resp.status_code == 200, route
        body = resp.text
        for href, label in _EXPECTED_NAV_LINKS.items():
            assert f'<a href="{href}">{label}</a>' in body, (
                f"{route}'s nav is missing the {label!r} link to {href!r}"
            )
