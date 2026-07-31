"""Playwright integration tests: the shared token-entry affordance (debug-plan D5).

Found via a real dogfood pass: every view's ``getToken()`` silently checked
``localStorage.tm_token`` and rendered "Set tm_token in localStorage to use
this view." if absent -- with **no in-page way to ever set it**. A real user
(as opposed to a developer with DevTools open) had no path to authenticate
at all. ``app.js``'s ``initAuthBar()`` (called from ``boot()`` on every page)
now renders an inline form when no token is set, and a masked-token status +
Change/Clear controls once one is. Storage is unchanged -- still
``localStorage.tm_token``, still read by every existing view's ``getToken()``
-- this only adds a UI affordance around what already existed.

Same live-daemon-via-Playwright pattern as ``test_ui_smoke.py`` /
``test_ui_dashboard.py``: a real ``uvicorn.Server`` background thread built
from ``Config(db_path=...)``, never an injected connection, so the daemon
serves the production per-request-connection path.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator
from typing import Any

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
    db_file = tmp_path / "ui-auth-bar.db"
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


def test_auth_bar_shows_form_when_no_token(daemon: dict[str, Any], page: Page) -> None:
    """No localStorage seeding at all -- this is what a brand-new user sees."""
    page.goto(f"{daemon['base_url']}/dashboard")
    auth_bar = page.locator("#tm-auth-bar")
    expect(auth_bar.locator("input.tm-auth-input")).to_be_visible()
    expect(auth_bar.get_by_role("button", name="Save token")).to_be_visible()


def test_saving_token_through_the_ui_actually_authenticates(
    daemon: dict[str, Any], page: Page
) -> None:
    """The whole point of D5: no DevTools/console needed, ever."""
    page.goto(f"{daemon['base_url']}/dashboard")
    page.locator("#tm-auth-bar input.tm-auth-input").fill(daemon["token"])
    page.get_by_role("button", name="Save token").click()

    # Saving reloads the page; the dashboard should now load real data (not
    # the "Set tm_token..." notice) and the bar should show the set state.
    expect(page.locator("#dashboard-facet-coverage")).to_contain_text("Facet coverage")
    expect(page.locator("#tm-auth-bar")).to_contain_text("Token set")


def test_auth_bar_shows_masked_token_and_change_clear_controls(
    daemon: dict[str, Any], page: Page
) -> None:
    token = daemon["token"]
    page.context.add_init_script(
        f"window.localStorage.setItem('tm_token', {json.dumps(token)});"
    )
    page.goto(f"{daemon['base_url']}/dashboard")

    auth_bar = page.locator("#tm-auth-bar")
    expect(auth_bar).to_contain_text("Token set")
    # Never render the raw token back to the page.
    assert token not in auth_bar.inner_text()
    expect(auth_bar.get_by_role("button", name="Change token")).to_be_visible()
    expect(auth_bar.get_by_role("button", name="Clear token")).to_be_visible()


def test_clear_token_button_logs_out(daemon: dict[str, Any], page: Page) -> None:
    # Deliberately NOT seeded via add_init_script: that script re-runs on
    # every navigation (including the reload Clear triggers) and would just
    # re-set the token right back, masking the very thing this test checks.
    # Save through the UI instead (same path a real user takes), then clear.
    page.goto(f"{daemon['base_url']}/dashboard")
    page.locator("#tm-auth-bar input.tm-auth-input").fill(daemon["token"])
    page.get_by_role("button", name="Save token").click()
    expect(page.locator("#tm-auth-bar")).to_contain_text("Token set")

    page.get_by_role("button", name="Clear token").click()

    expect(page.locator("#tm-auth-bar input.tm-auth-input")).to_be_visible()
    stored = page.evaluate("window.localStorage.getItem('tm_token')")
    assert stored is None
