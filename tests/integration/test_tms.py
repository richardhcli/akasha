"""Integration tests for M7's TMS loop (spec §4.9, §4.10, §4.2, §4.11).

This file is M7's shared integration suite (other M7 tasks append their own
sections below as they land). This task (T7.7) covers ONLY the
facets-from-spans capture flow: ``POST /edges`` accepting an optional
``facet_span`` that mints a facet on the TARGET (``dst``) node and binds the
new edge to it. Select just these with ``-k facet_span``.
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from akasha.api import auth
from akasha.api.app import create_app
from akasha.kernel import ids, store


@pytest.fixture(autouse=True)
def _reset_rate_limit_state():
    auth._call_log.clear()
    yield
    auth._call_log.clear()


def _insert_token(conn: sqlite3.Connection, token_id: str, secret: str, cls: str) -> None:
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
def api(tmp_path):
    conn = store.connect(tmp_path / "tms.db", check_same_thread=False)
    store.run_migrations(conn)
    human_secret = auth.mint_secret()
    _insert_token(conn, "humantoken", human_secret, "human")
    client = TestClient(create_app(conn=conn))
    human_bearer = auth.format_bearer_token("humantoken", human_secret)
    return {
        "client": client,
        "conn": conn,
        "human": {"Authorization": f"Bearer {human_bearer}"},
    }


def _create_node(client, headers, node_type="claim", body="hello world", **kw):
    resp = client.post(
        "/v1/nodes", json={"node_type": node_type, "body": body, **kw}, headers=headers
    )
    assert resp.status_code == 201
    return resp.json()


# --- T7.7: facets-from-spans capture (POST /edges with facet_span) --------


def test_edges_create_with_facet_span_mints_facet_on_target(api):
    client, h, conn = api["client"], api["human"], api["conn"]
    src = _create_node(client, h, body="source claim")
    dst = _create_node(client, h, body="target definition")

    span_text = "the highlighted defining span"
    resp = client.post(
        "/v1/edges",
        json={
            "src": src["id"],
            "dst": dst["id"],
            "edge_type": "supports",
            "provenance": "human",
            "facet_span": span_text,
        },
        headers=h,
    )
    assert resp.status_code == 201
    edge = resp.json()

    # binding is a concrete facet_id, never '*' and never None.
    binding = edge["facet_binding"]
    assert binding is not None
    assert binding != "*"
    ids.validate(binding)  # valid id8

    # the new facet is actually attached to the TARGET (dst) node, not src.
    dst_node = client.get(f"/v1/nodes/{dst['id']}", headers=h).json()
    facets_by_id = {f["facet_id"]: f for f in dst_node["facets"]}
    assert binding in facets_by_id
    assert facets_by_id[binding]["span"] == span_text
    assert facets_by_id[binding]["version"] == 1

    src_node = client.get(f"/v1/nodes/{src['id']}", headers=h).json()
    assert binding not in {f["facet_id"] for f in src_node["facets"]}

    # also fetchable via neighborhood -- the edge shows up bound to the facet.
    nb = client.get(f"/v1/nodes/{dst['id']}/neighborhood", headers=h).json()
    nb_edge = next(e for e in nb["edges"] if e["id"] == edge["id"])
    assert nb_edge["facet_binding"] == binding

    # minting a facet is a "minor" commit, not "major" -- no review item
    # should be spuriously enqueued by facets-from-spans capture (spec §4.9:
    # invalidation only triggers on major commits; a brand-new v1 facet is
    # neither removed/renamed nor version-bumped).
    open_reviews = conn.execute("SELECT count(*) FROM review_queue").fetchone()[0]
    assert open_reviews == 0


def test_edges_create_without_facet_span_behaves_as_before(api):
    client, h = api["client"], api["human"]
    src = _create_node(client, h, body="source claim")
    dst = _create_node(client, h, body="target definition")

    resp = client.post(
        "/v1/edges",
        json={
            "src": src["id"],
            "dst": dst["id"],
            "edge_type": "supports",
            "facet_binding": "*",
            "provenance": "human",
        },
        headers=h,
    )
    assert resp.status_code == 201
    edge = resp.json()
    assert edge["facet_binding"] == "*"

    # no facet was minted on the target -- facet list stays empty.
    dst_node = client.get(f"/v1/nodes/{dst['id']}", headers=h).json()
    assert dst_node["facets"] == []


def test_edges_create_facet_span_satisfies_justification_binding_requirement(api):
    """§4.2 invariant: justification edge types require a non-None binding.

    Without facet_span, omitting facet_binding on a justification edge type
    is rejected (E_INVALID). With facet_span, the minted facet concretely
    satisfies that same requirement -- confirming the binding invariant
    still holds end to end.
    """
    client, h = api["client"], api["human"]
    src = _create_node(client, h, body="source claim")
    dst = _create_node(client, h, body="target definition")

    rejected = client.post(
        "/v1/edges",
        json={
            "src": src["id"],
            "dst": dst["id"],
            "edge_type": "contradicts",
            "provenance": "human",
        },
        headers=h,
    )
    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == "E_INVALID"

    accepted = client.post(
        "/v1/edges",
        json={
            "src": src["id"],
            "dst": dst["id"],
            "edge_type": "contradicts",
            "provenance": "human",
            "facet_span": "a contradicting definition span",
        },
        headers=h,
    )
    assert accepted.status_code == 201
    edge = accepted.json()
    assert edge["facet_binding"] not in (None, "*")

    dst_node = client.get(f"/v1/nodes/{dst['id']}", headers=h).json()
    assert any(f["facet_id"] == edge["facet_binding"] for f in dst_node["facets"])


# --- T7.4: all_subtasks_closed flags supertask for review (never auto-closes) ---


def test_supertask_flag(api):
    """When every composes-child task is done, enqueue subtasks_closed once; never auto-close."""
    from akasha.tms import triggers

    client, h, conn = api["client"], api["human"], api["conn"]

    # (1) supertask + 3 open subtasks linked by composes edges.
    supertask = _create_node(client, h, node_type="task", body="supertask", task_state="open")
    children = [
        _create_node(client, h, node_type="task", body=f"child-{i}", task_state="open")
        for i in range(3)
    ]
    for child in children:
        resp = client.post(
            "/v1/edges",
            json={
                "src": supertask["id"],
                "dst": child["id"],
                "edge_type": "composes",
                "facet_binding": None,
                "provenance": "human",
            },
            headers=h,
        )
        assert resp.status_code == 201

    # (2) close 2 of 3 children — one remains open.
    for child in children[:2]:
        store.commit_node(
            conn,
            child["id"],
            task_state="done",
            change_class="patch",
            facets_touched=[],
            author="test",
        )

    # (3) not yet flagged while a child is still open.
    result = triggers.evaluate(
        conn, supertask["id"], triggers.TriggerContext(now="2026-07-14")
    )
    assert result == []
    assert (
        store.find_open_reviews(
            conn, node_id=supertask["id"], cause_kind="subtasks_closed"
        )
        == []
    )

    # (4) close the last remaining open child.
    store.commit_node(
        conn,
        children[2]["id"],
        task_state="done",
        change_class="patch",
        facets_touched=[],
        author="test",
    )

    # (5) fires once: enqueue subtasks_closed; supertask stays open.
    result = triggers.evaluate(
        conn, supertask["id"], triggers.TriggerContext(now="2026-07-14")
    )
    assert len(result) == 1
    assert result[0]["cause_kind"] == "subtasks_closed"
    assert result[0]["node_id"] == supertask["id"]
    open_reviews = store.find_open_reviews(
        conn, node_id=supertask["id"], cause_kind="subtasks_closed"
    )
    assert len(open_reviews) == 1
    assert store.get_node(conn, supertask["id"]).task_state == "open"

    # (6) idempotent: second evaluate enqueues nothing.
    result = triggers.evaluate(
        conn, supertask["id"], triggers.TriggerContext(now="2026-07-14")
    )
    assert result == []
    open_reviews_again = store.find_open_reviews(
        conn, node_id=supertask["id"], cause_kind="subtasks_closed"
    )
    assert len(open_reviews_again) == 1

    # (7) sole lasting side effect is the review row — task_state still open.
    assert store.get_node(conn, supertask["id"]).task_state == "open"
