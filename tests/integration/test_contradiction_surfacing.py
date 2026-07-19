"""Integration tests for contradiction surfacing at capture (task T10.2b).

Spec §4.11 `POST /nodes` response field `contradiction_candidates` and the
"Contradiction surfacing at capture" definition paragraph directly under the
endpoint table (PRD §8 story 2, T10.2b fable ruling, 2026-07-19). Verifies
`docs/build-plan.md` T10.2b / `docs/mvp-spec.md` §9 row 2.

Mirrors `tests/integration/test_api.py`'s ``api`` fixture (in-process
FastAPI ``TestClient`` over a fresh SQLite store) since this is pure
`POST /nodes` route coverage, not a sync/daemon-transport test.
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from akasha.api import auth
from akasha.api.app import create_app
from akasha.kernel import store


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
    conn = store.connect(tmp_path / "api.db", check_same_thread=False)
    store.run_migrations(conn)
    human_secret = auth.mint_secret()
    agent_secret = auth.mint_secret()
    _insert_token(conn, "humantoken", human_secret, "human")
    _insert_token(conn, "agenttoken", agent_secret, "agent")
    client = TestClient(create_app(conn=conn))
    human_bearer = auth.format_bearer_token("humantoken", human_secret)
    agent_bearer = auth.format_bearer_token("agenttoken", agent_secret)
    return {
        "client": client,
        "conn": conn,
        "human": {"Authorization": f"Bearer {human_bearer}"},
        "agent": {"Authorization": f"Bearer {agent_bearer}"},
    }


def _create(client, headers, node_type="claim", body="hello world", **kw):
    return client.post(
        "/v1/nodes", json={"node_type": node_type, "body": body, **kw}, headers=headers
    )


def test_exact_duplicate_ranks_first_with_evidence(api):
    client, h, conn = api["client"], api["human"], api["conn"]

    body = "Paris is the capital of France."
    existing = _create(client, h, node_type="claim", body=body)
    assert existing.status_code == 201
    existing_id = existing.json()["id"]

    ev = _create(client, h, node_type="evidence", body="INSEE 2020 census record, page 12")
    assert ev.status_code == 201
    ev_id = ev.json()["id"]

    store.create_edge(conn, existing_id, ev_id, "cites", "*", "human")

    # a second, unrelated live claim so the exact-duplicate isn't the only match
    other = _create(client, h, node_type="claim", body="The sky often looks blue at noon.")
    assert other.status_code == 201

    dup = _create(client, h, node_type="claim", body=body)
    assert dup.status_code == 201
    payload = dup.json()

    candidates = payload["contradiction_candidates"]
    assert candidates, "expected at least one candidate for an exact-duplicate claim"
    first = candidates[0]
    assert first["node_id"] == existing_id
    assert first["body"].strip() == body.strip()
    assert "created_at" in first and first["created_at"]
    assert len(first["evidence"]) == 1
    assert first["evidence"][0]["node_id"] == ev_id
    assert first["evidence"][0]["body"].strip() == "INSEE 2020 census record, page 12"


def test_near_duplicate_surfaces(api):
    client, h = api["client"], api["human"]

    existing = _create(
        client,
        h,
        node_type="claim",
        body="The quarterly stock price rose sharply due to strong earnings.",
    )
    assert existing.status_code == 201
    existing_id = existing.json()["id"]

    near = _create(
        client,
        h,
        node_type="claim",
        body="Stock price rose due to earnings surprise this quarter.",
    )
    assert near.status_code == 201
    candidate_ids = {c["node_id"] for c in near.json()["contradiction_candidates"]}
    assert existing_id in candidate_ids


def test_non_claim_create_returns_empty_candidates(api):
    client, h = api["client"], api["human"]
    resp = _create(client, h, node_type="entity", body="Some entity body text here.")
    assert resp.status_code == 201
    assert resp.json()["contradiction_candidates"] == []


def test_agent_create_stays_202_proposal_without_candidates_field(api):
    client, a = api["client"], api["agent"]
    resp = _create(client, a, node_type="claim", body="An agent-proposed claim body.")
    assert resp.status_code == 202
    payload = resp.json()
    assert payload["proposed"] is True
    assert "contradiction_candidates" not in payload


def test_fts5_hostile_body_returns_201_with_sane_candidates(api):
    client, h = api["client"], api["human"]
    hostile = 'quote " colon: hyphen-word AND OR NOT NEAR * ^ ~~~ !!! ???'
    resp = _create(client, h, node_type="claim", body=hostile)
    assert resp.status_code == 201
    candidates = resp.json()["contradiction_candidates"]
    assert isinstance(candidates, list)
    assert len(candidates) <= 5

    punctuation_only = _create(client, h, node_type="claim", body="!!! ??? --- ...")
    assert punctuation_only.status_code == 201
    assert punctuation_only.json()["contradiction_candidates"] == []


def test_surfacing_helper_is_strictly_read_only(api):
    client, h, conn = api["client"], api["human"], api["conn"]

    body = "A duplicate-prone claim about read-only surfacing behavior."
    first = _create(client, h, node_type="claim", body=body)
    assert first.status_code == 201

    dup = _create(client, h, node_type="claim", body=body)
    assert dup.status_code == 201
    node_id = dup.json()["id"]
    canonical_body = dup.json()["body"]

    open_before = store.find_open_reviews(conn)
    nodes_before = conn.execute("SELECT * FROM nodes ORDER BY id").fetchall()
    commits_before = conn.execute("SELECT * FROM commits ORDER BY rowid").fetchall()
    edges_before = conn.execute("SELECT * FROM edges ORDER BY id").fetchall()

    # Re-run the exact read-only computation the route already performed on
    # the create path -- mirrors T10.2's read-only-gate test discipline
    # (call the helper directly, diff DB state before/after).
    result = store.find_contradiction_candidates(conn, node_id, canonical_body)
    assert result  # sanity: the helper actually found the duplicate

    open_after = store.find_open_reviews(conn)
    nodes_after = conn.execute("SELECT * FROM nodes ORDER BY id").fetchall()
    commits_after = conn.execute("SELECT * FROM commits ORDER BY rowid").fetchall()
    edges_after = conn.execute("SELECT * FROM edges ORDER BY id").fetchall()

    assert open_before == open_after
    assert nodes_before == nodes_after
    assert commits_before == commits_after
    assert edges_before == edges_after
