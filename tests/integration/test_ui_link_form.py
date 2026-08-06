"""Playwright integration test: the node view's "Link this node" form (T14.6).

Closes the never-built UI half of M7's DoD ("facets-from-spans capture flow
in API/UI") -- see docs/spec-questions.md T14.6 for the narrowest-reading
ruling this implements against. The only production POST /v1/edges call site
was, until this task, a hand-written HTTP call; this asserts a human can
create a facet-bound justification edge entirely from the browser, that the
highlighted span becomes a REAL facet on the target (not just an edge
attribute), that the node view's neighborhood section (T14.5) updates in
place without a reload, and that GET /v1/metrics's facet_coverage -- a
gating dogfood metric (PRD §7) -- moves as a result.

Same live-daemon-via-Playwright pattern as test_ui_node_links.py.
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
    db_file = tmp_path / "ui-link-form.db"
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


def test_link_form_creates_facet_bound_edge_and_updates_neighborhood(
    daemon: dict[str, Any], page: Page
) -> None:
    base_url = daemon["base_url"]
    token = daemon["token"]

    with httpx.Client(
        base_url=base_url, headers={"Authorization": f"Bearer {token}"}, timeout=10.0
    ) as client:
        src_resp = client.post(
            "/v1/nodes",
            json={"node_type": "definition", "body": "a distinctive vraknos source definition"},
        )
        assert src_resp.status_code == 201, src_resp.text
        src_id = src_resp.json()["id"]

        dst_resp = client.post(
            "/v1/nodes",
            json={
                "node_type": "definition",
                "body": "a distinctive plombexar target definition body",
            },
        )
        assert dst_resp.status_code == 201, dst_resp.text
        dst_id = dst_resp.json()["id"]

        # facet_coverage's denominator only counts S2+ nodes (spec §7 /
        # store.facet_coverage_counts) -- vet the target so it's eligible
        # for the metric to move at all, independent of this test's own edge.
        vet_resp = client.post(f"/v1/nodes/{dst_id}/vet")
        assert vet_resp.status_code == 200, vet_resp.text

        before_metrics = client.get("/v1/metrics")
        assert before_metrics.status_code == 200, before_metrics.text

    page.context.add_init_script(
        f"window.localStorage.setItem('tm_token', {json.dumps(token)});"
    )
    page.goto(f"{base_url}/node?id={src_id}")

    target_input = page.locator("#link-target-id")
    target_input.fill(dst_id)
    page.locator("#link-preview-btn").click()

    preview = page.locator("#link-target-preview")
    expect(preview).to_contain_text("plombexar target definition body")

    # Select a real span of the rendered TARGET-body preview via the
    # standard Selection API (step 2: no editor library), then let the
    # form's own "Use selection" button copy it into the span field --
    # exactly the flow a real user drags a mouse through.
    page.evaluate(
        """
        () => {
          const el = document.querySelector('#link-target-preview .link-target-body');
          const range = document.createRange();
          range.selectNodeContents(el);
          const selection = window.getSelection();
          selection.removeAllRanges();
          selection.addRange(range);
        }
        """
    )
    page.locator("#link-use-selection-btn").click()

    span_field = page.locator("#link-span")
    expect(span_field).to_have_value("a distinctive plombexar target definition body")

    page.locator("#link-edge-type").select_option("supports")
    page.locator("#link-form button[type=submit]").click()

    neighborhood = page.locator("#node-neighborhood")
    outbound = neighborhood.locator(".neighborhood-outbound")
    expect(outbound).to_be_visible()
    supports_group = outbound.locator(".neighborhood-type-group", has_text="supports")
    expect(supports_group).to_be_visible()
    dst_link = supports_group.locator("a", has_text=dst_id)
    expect(dst_link).to_have_attribute("href", f"/node?id={dst_id}")

    expect(page.locator("#link-error")).to_have_text("")

    with httpx.Client(
        base_url=base_url, headers={"Authorization": f"Bearer {token}"}, timeout=10.0
    ) as client:
        neighborhood_resp = client.get(f"/v1/nodes/{src_id}/neighborhood")
        assert neighborhood_resp.status_code == 200, neighborhood_resp.text
        edges = neighborhood_resp.json()["edges"]
        matches = [
            e
            for e in edges
            if e["src"] == src_id and e["dst"] == dst_id and e["edge_type"] == "supports"
        ]
        assert len(matches) == 1, edges
        edge = matches[0]
        facet_id = edge["facet_binding"]
        assert facet_id not in (None, "*"), edge

        dst_node = client.get(f"/v1/nodes/{dst_id}")
        assert dst_node.status_code == 200, dst_node.text
        facets = dst_node.json()["facets"]
        matching_facets = [f for f in facets if f["facet_id"] == facet_id]
        assert len(matching_facets) == 1, facets
        assert (
            matching_facets[0]["span"] == "a distinctive plombexar target definition body"
        ), matching_facets[0]

        after_metrics = client.get("/v1/metrics")
        assert after_metrics.status_code == 200, after_metrics.text
        assert after_metrics.json()["facet_coverage"] > 0.0, after_metrics.json()


def test_link_form_shows_server_400_verbatim_for_missing_binding(
    daemon: dict[str, Any], page: Page
) -> None:
    """Step 4: a justification edge submitted with neither a binding nor a
    span must surface the server's own 400 message unmodified -- the
    validation rule (spec §4.2) stays server-side only, never duplicated
    client-side.

    The server's message embeds a freshly minted (random) edge id, so it
    cannot be reproduced by a second, separate call -- instead this
    intercepts the REAL response the browser's own POST /v1/edges received
    and asserts the rendered text is exactly that response's error message,
    proving the UI never rewrites/summarizes it.
    """
    base_url = daemon["base_url"]
    token = daemon["token"]

    with httpx.Client(
        base_url=base_url, headers={"Authorization": f"Bearer {token}"}, timeout=10.0
    ) as client:
        src_resp = client.post(
            "/v1/nodes",
            json={"node_type": "claim", "body": "a distinctive zeltroun source claim"},
        )
        assert src_resp.status_code == 201, src_resp.text
        src_id = src_resp.json()["id"]

        dst_resp = client.post(
            "/v1/nodes",
            json={"node_type": "claim", "body": "a distinctive worfanik target claim"},
        )
        assert dst_resp.status_code == 201, dst_resp.text
        dst_id = dst_resp.json()["id"]

    page.context.add_init_script(
        f"window.localStorage.setItem('tm_token', {json.dumps(token)});"
    )
    page.goto(f"{base_url}/node?id={src_id}")

    page.locator("#link-target-id").fill(dst_id)
    page.locator("#link-edge-type").select_option("supports")
    with page.expect_response(
        lambda resp: resp.url.endswith("/v1/edges") and resp.request.method == "POST"
    ) as resp_info:
        page.locator("#link-form button[type=submit]").click()
    server_response = resp_info.value
    assert server_response.status == 400, server_response.text()
    expected_message = server_response.json()["error"]["message"]
    assert "facet_binding" in expected_message  # sanity: this is the real rule text

    expect(page.locator("#link-error")).to_have_text(expected_message)


def test_link_form_shows_server_404_verbatim_for_unknown_target(
    daemon: dict[str, Any], page: Page
) -> None:
    """Step 5: linking to a nonexistent target id is the server's 404, shown
    as-is. ``mint_facet_from_span`` (the only ``create_edge`` path that
    checks the target actually exists, spec §4.11 T7.7) requires a span, so
    this fills one in.
    """
    base_url = daemon["base_url"]
    token = daemon["token"]

    with httpx.Client(
        base_url=base_url, headers={"Authorization": f"Bearer {token}"}, timeout=10.0
    ) as client:
        src_resp = client.post(
            "/v1/nodes",
            json={"node_type": "claim", "body": "a distinctive habrylon source claim"},
        )
        assert src_resp.status_code == 201, src_resp.text
        src_id = src_resp.json()["id"]

    page.context.add_init_script(
        f"window.localStorage.setItem('tm_token', {json.dumps(token)});"
    )
    page.goto(f"{base_url}/node?id={src_id}")

    page.locator("#link-target-id").fill("nonexistent0")
    page.locator("#link-edge-type").select_option("composes")
    page.locator("#link-span").fill("a span pointing at a target that does not exist")
    with page.expect_response(
        lambda resp: resp.url.endswith("/v1/edges") and resp.request.method == "POST"
    ) as resp_info:
        page.locator("#link-form button[type=submit]").click()
    server_response = resp_info.value
    assert server_response.status == 404, server_response.text()
    expected_message = server_response.json()["error"]["message"]

    expect(page.locator("#link-error")).to_have_text(expected_message)
