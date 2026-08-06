"""Playwright integration test: the node view's task state/maturity/subtask
structure and toggle control (T13.5).

Closes the read-and-toggle half of PRD §8 story 8 on the Web UI: until this
task, ``task_state`` (T13.1) and the subtask/supertask ``composes``
structure were only reachable via hand-written HTTP or the CLI (T13.4) --
this asserts a human can see a task's state, maturity, and its
supertask/subtasks as clickable links, and complete it through the real
``PATCH /v1/nodes/{id}`` endpoint from the browser -- and that closing the
last open subtask flags the supertask for review (``subtasks_closed``)
without ever auto-closing the supertask's own ``task_state`` (design
invariant 3).

Same live-daemon-via-Playwright pattern as ``test_ui_node_links.py`` /
``test_ui_link_form.py``.
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
    db_file = tmp_path / "ui-task-view.db"
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


def test_node_view_task_state_maturity_subtasks_and_toggle(
    daemon: dict[str, Any], page: Page
) -> None:
    base_url = daemon["base_url"]
    token = daemon["token"]

    with httpx.Client(
        base_url=base_url, headers={"Authorization": f"Bearer {token}"}, timeout=10.0
    ) as client:
        super_resp = client.post(
            "/v1/nodes",
            json={
                "node_type": "task",
                "body": "a distinctive vromtash supertask",
                "task_state": "open",
            },
        )
        assert super_resp.status_code == 201, super_resp.text
        super_id = super_resp.json()["id"]

        sub1_resp = client.post(
            "/v1/nodes",
            json={
                "node_type": "task",
                "body": "a distinctive plendarox first subtask",
                "task_state": "open",
            },
        )
        assert sub1_resp.status_code == 201, sub1_resp.text
        sub1_id = sub1_resp.json()["id"]

        sub2_resp = client.post(
            "/v1/nodes",
            json={
                "node_type": "task",
                "body": "a distinctive glimwarnu second subtask",
                "task_state": "open",
            },
        )
        assert sub2_resp.status_code == 201, sub2_resp.text
        sub2_id = sub2_resp.json()["id"]

        for child_id in (sub1_id, sub2_id):
            edge_resp = client.post(
                "/v1/edges",
                json={
                    "src": super_id,
                    "dst": child_id,
                    "edge_type": "composes",
                    "provenance": "human",
                },
            )
            assert edge_resp.status_code == 201, edge_resp.text

        # Close the first subtask directly (real API, not the UI) so sub2 is
        # the "last open subtask" the test toggles through the browser.
        close_sub1 = client.patch(
            f"/v1/nodes/{sub1_id}",
            json={"task_state": "done", "change_class": "patch", "facets_touched": []},
        )
        assert close_sub1.status_code == 200, close_sub1.text

    page.context.add_init_script(
        f"window.localStorage.setItem('tm_token', {json.dumps(token)});"
    )

    # --- Supertask's own node view: state, maturity, both subtasks linked ---
    page.goto(f"{base_url}/node?id={super_id}")

    task_section = page.locator("#node-task")
    expect(task_section.locator(".task-state")).to_have_text("State: Open")
    expect(task_section.locator(".task-maturity")).to_contain_text("Maturity: S")

    subtasks = task_section.locator(".task-subtasks")
    sub1_link = subtasks.locator("a", has_text=sub1_id)
    sub2_link = subtasks.locator("a", has_text=sub2_id)
    expect(sub1_link).to_have_attribute("href", f"/node?id={sub1_id}")
    expect(sub2_link).to_have_attribute("href", f"/node?id={sub2_id}")
    expect(subtasks).to_contain_text("Done")
    expect(subtasks).to_contain_text("Open")

    # R9 copy discipline: never the literal word "true" anywhere in the
    # rendered task section.
    task_text = task_section.inner_text()
    assert "true" not in task_text.lower()

    # --- Follow the link to the last-open subtask and toggle it done ---
    sub2_link.click()
    expect(page).to_have_url(f"{base_url}/node?id={sub2_id}")

    sub_task_section = page.locator("#node-task")
    expect(sub_task_section.locator(".task-state")).to_have_text("State: Open")
    toggle_btn = sub_task_section.locator(".task-toggle")
    expect(toggle_btn).to_have_text("Mark done")

    toggle_btn.click()

    # Re-rendered from the real server response -- not a client-side
    # simulation.
    expect(sub_task_section.locator(".task-state")).to_have_text("State: Done")
    expect(sub_task_section.locator(".task-toggle")).to_have_text("Reopen")

    with httpx.Client(
        base_url=base_url, headers={"Authorization": f"Bearer {token}"}, timeout=10.0
    ) as client:
        sub2_node = client.get(f"/v1/nodes/{sub2_id}")
        assert sub2_node.status_code == 200, sub2_node.text
        assert sub2_node.json()["task_state"] == "done"

        # Closing the last open subtask must flag the supertask for review
        # (subtasks_closed) -- and never auto-close it.
        reviews = client.get(f"/v1/review?status=open&node={super_id}")
        assert reviews.status_code == 200, reviews.text
        cause_kinds = [r["cause_kind"] for r in reviews.json()["reviews"]]
        assert "subtasks_closed" in cause_kinds, cause_kinds

        super_node = client.get(f"/v1/nodes/{super_id}")
        assert super_node.status_code == 200, super_node.text
        assert super_node.json()["task_state"] == "open", "supertask must never auto-close"

    # Also confirm the supertask shows up in the Web UI's own review queue,
    # flagged subtasks_closed -- not just via the raw API.
    page.goto(f"{base_url}/review")
    review_queue = page.locator("#review-queue")
    review_item = review_queue.locator(".review-item", has_text=super_id)
    expect(review_item).to_be_visible()
    expect(review_item).to_contain_text("subtasks_closed")

    # --- Back on the supertask's own node view: still Open, flagged text
    # uses "flagged for review", never "complete" ---
    page.goto(f"{base_url}/node?id={super_id}")
    task_section = page.locator("#node-task")
    expect(task_section.locator(".task-state")).to_have_text("State: Open")
    flagged = task_section.locator(".task-flagged")
    expect(flagged).to_be_visible()
    expect(flagged).to_contain_text("flagged for review")
    flagged_text = flagged.inner_text().lower()
    assert "complete" not in flagged_text
    assert "true" not in flagged_text


def test_node_view_non_task_node_has_no_task_section(daemon: dict[str, Any], page: Page) -> None:
    """Step 1: a non-task node (``task_state is None``) must render exactly
    as it did before this task -- the Task section stays entirely empty,
    never showing state/maturity/toggle text for a node that isn't a task.
    """
    base_url = daemon["base_url"]
    token = daemon["token"]

    with httpx.Client(
        base_url=base_url, headers={"Authorization": f"Bearer {token}"}, timeout=10.0
    ) as client:
        resp = client.post(
            "/v1/nodes",
            json={"node_type": "claim", "body": "a distinctive quenthral non-task claim"},
        )
        assert resp.status_code == 201, resp.text
        node_id = resp.json()["id"]

    page.context.add_init_script(
        f"window.localStorage.setItem('tm_token', {json.dumps(token)});"
    )
    page.goto(f"{base_url}/node?id={node_id}")

    # Wait for the view to finish loading (body populated) before asserting
    # on the Task section's absence, so this isn't just "hasn't loaded yet".
    expect(page.locator("#node-body")).to_contain_text("quenthral non-task claim")
    expect(page.locator("#node-task")).to_be_empty()
