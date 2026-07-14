"""Integration tests for the node routes (task T4.4, spec §4.11 /nodes*)."""

from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from akasha.api import auth
from akasha.api.app import create_app
from akasha.contract.parser import parse
from akasha.contract.render import render
from akasha.kernel import store
from akasha.kernel.canonical import canonicalize_text
from akasha.kernel.ids import contract_anchor
from akasha.sync import base_store


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


def test_nodes_create_and_get_includes_maturity(api):
    client, h = api["client"], api["human"]
    resp = _create(client, h)
    assert resp.status_code == 201
    node = resp.json()
    assert node["body"].strip() == "hello world"  # store canonicalizes (trailing \n)
    assert node["maturity"] == "S0"  # fresh node, no inbound edges
    got = client.get(f"/v1/nodes/{node['id']}", headers=h)
    assert got.status_code == 200
    assert got.json()["id"] == node["id"]
    assert got.json()["maturity"] == "S0"


def test_nodes_get_missing_returns_404_envelope(api):
    client, h = api["client"], api["human"]
    resp = client.get("/v1/nodes/nope2345", headers=h)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "E_NOT_FOUND"


def test_nodes_require_auth_missing_token_401(api):
    client = api["client"]
    resp = client.get("/v1/nodes/whatever2")  # no Authorization header
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "E_AUTH"


def test_nodes_patch_commits_edit(api):
    client, h = api["client"], api["human"]
    node = _create(client, h).json()
    resp = client.patch(
        f"/v1/nodes/{node['id']}",
        json={"body": "revised body", "change_class": "patch", "facets_touched": []},
        headers=h,
    )
    assert resp.status_code == 200
    assert resp.json()["body"].strip() == "revised body"
    # history now has genesis + this commit
    hist = client.get(f"/v1/nodes/{node['id']}/history", headers=h).json()["history"]
    assert len(hist) == 2


def test_nodes_get_as_of_returns_earlier_body(api):
    client, h = api["client"], api["human"]
    node = _create(client, h).json()
    genesis = client.get(f"/v1/nodes/{node['id']}/history", headers=h).json()["history"][0]
    client.patch(
        f"/v1/nodes/{node['id']}",
        json={"body": "v2", "change_class": "patch", "facets_touched": []},
        headers=h,
    )
    as_of = client.get(f"/v1/nodes/{node['id']}", headers=h, params={"as_of": genesis["ts"]})
    assert as_of.status_code == 200
    assert as_of.json()["body"].strip() == "hello world"


def test_nodes_neighborhood(api):
    client, h, conn = api["client"], api["human"], api["conn"]
    a = _create(client, h, body="target").json()
    b = _create(client, h, body="source").json()
    store.create_edge(conn, b["id"], a["id"], "supports", "*", "human")
    resp = client.get(f"/v1/nodes/{a['id']}/neighborhood", headers=h, params={"hops": 1})
    assert resp.status_code == 200
    data = resp.json()
    assert a["id"] in data["node_ids"] and b["id"] in data["node_ids"]
    assert len(data["edges"]) == 1


def test_nodes_delete_s0_hard(api):
    client, h = api["client"], api["human"]
    node = _create(client, h).json()
    resp = client.delete(f"/v1/nodes/{node['id']}", headers=h)
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True
    assert client.get(f"/v1/nodes/{node['id']}", headers=h).status_code == 404


def test_nodes_delete_s1_without_redirect_returns_409(api):
    client, h, conn = api["client"], api["human"], api["conn"]
    a = _create(client, h, body="target").json()
    b = _create(client, h, body="source").json()
    store.create_edge(conn, b["id"], a["id"], "supports", "*", "human")  # a -> S1
    resp = client.delete(f"/v1/nodes/{a['id']}", headers=h)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "E_NEEDS_REDIRECT"


def test_nodes_split_returns_redirect(api):
    client, h = api["client"], api["human"]
    node = _create(client, h, body="whole").json()
    resp = client.post(
        f"/v1/nodes/{node['id']}/split",
        json={
            "parts": [
                {"node_type": "claim", "body": "part one"},
                {"node_type": "claim", "body": "part two"},
            ]
        },
        headers=h,
    )
    assert resp.status_code == 200
    successors = resp.json()["redirect"][node["id"]]
    assert len(successors) == 2


def test_nodes_merge_returns_redirect(api):
    client, h = api["client"], api["human"]
    survivor = _create(client, h, body="survivor").json()
    other = _create(client, h, body="other").json()
    resp = client.post(
        f"/v1/nodes/{survivor['id']}/merge",
        json={"ids": [other["id"]]},
        headers=h,
    )
    assert resp.status_code == 200
    assert resp.json()["redirect"][other["id"]] == [survivor["id"]]


def test_nodes_vet_human_sets_s4(api):
    client, h = api["client"], api["human"]
    node = _create(client, h).json()
    resp = client.post(f"/v1/nodes/{node['id']}/vet", headers=h)
    assert resp.status_code == 200
    assert resp.json()["maturity"] == "S4"
    assert resp.json()["vetted"] is True


def test_nodes_vet_from_agent_token_is_rejected(api):
    client, h, agent = api["client"], api["human"], api["agent"]
    node = _create(client, h).json()
    resp = client.post(f"/v1/nodes/{node['id']}/vet", headers=agent)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "E_HUMAN_ONLY"


def test_nodes_mutating_request_writes_one_audit_row(api):
    client, h, conn = api["client"], api["human"], api["conn"]
    _create(client, h)
    rows = conn.execute(
        "SELECT token_id, action FROM audit_log WHERE action LIKE 'POST %'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "humantoken"
    assert rows[0][1] == "POST /v1/nodes"


def test_nodes_read_writes_no_audit_row(api):
    client, h = api["client"], api["human"]
    node = _create(client, h).json()
    conn = api["conn"]
    before = conn.execute("SELECT count(*) FROM audit_log").fetchone()[0]
    client.get(f"/v1/nodes/{node['id']}", headers=h)
    after = conn.execute("SELECT count(*) FROM audit_log").fetchone()[0]
    assert before == after  # a GET adds no audit row


# --- T4.5: /edges ------------------------------------------------------


def test_edges_create_with_valid_facet_binding(api):
    client, h = api["client"], api["human"]
    src = _create(client, h, body="source").json()
    dst = _create(client, h, body="target").json()
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
    assert edge["edge_type"] == "supports"
    assert edge["facet_binding"] == "*"
    assert edge["src"] == src["id"] and edge["dst"] == dst["id"]


def test_edges_create_justification_type_missing_facet_binding_rejected(api):
    client, h = api["client"], api["human"]
    src = _create(client, h, body="source").json()
    dst = _create(client, h, body="target").json()
    resp = client.post(
        "/v1/edges",
        json={
            "src": src["id"],
            "dst": dst["id"],
            "edge_type": "supports",
            "facet_binding": None,
            "provenance": "human",
        },
        headers=h,
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "E_INVALID"


def test_edges_create_composes_allows_none_facet_binding(api):
    client, h = api["client"], api["human"]
    parent = _create(client, h, body="parent").json()
    child = _create(client, h, body="child").json()
    resp = client.post(
        "/v1/edges",
        json={
            "src": parent["id"],
            "dst": child["id"],
            "edge_type": "composes",
            "facet_binding": None,
            "provenance": "human",
        },
        headers=h,
    )
    assert resp.status_code == 201
    assert resp.json()["facet_binding"] is None


def test_edges_create_redirects_to_allows_none_facet_binding(api):
    client, h = api["client"], api["human"]
    old = _create(client, h, body="old").json()
    new = _create(client, h, body="new").json()
    resp = client.post(
        "/v1/edges",
        json={
            "src": old["id"],
            "dst": new["id"],
            "edge_type": "redirects_to",
            "facet_binding": None,
            "provenance": "human",
        },
        headers=h,
    )
    assert resp.status_code == 201
    assert resp.json()["facet_binding"] is None


def test_edges_delete_soft_retracts_and_drops_from_neighborhood(api):
    client, h, conn = api["client"], api["human"], api["conn"]
    src = _create(client, h, body="source").json()
    dst = _create(client, h, body="target").json()
    created = client.post(
        "/v1/edges",
        json={
            "src": src["id"],
            "dst": dst["id"],
            "edge_type": "supports",
            "facet_binding": "*",
            "provenance": "human",
        },
        headers=h,
    ).json()

    nb_before = client.get(f"/v1/nodes/{dst['id']}/neighborhood", headers=h).json()
    assert len(nb_before["edges"]) == 1

    resp = client.delete(f"/v1/edges/{created['id']}", headers=h)
    assert resp.status_code == 200
    assert resp.json()["retracted"] is True

    row = conn.execute("SELECT retracted_at FROM edges WHERE id=?", (created["id"],)).fetchone()
    assert row is not None  # soft retract: row still present, never DELETEd
    assert row[0] is not None  # retracted_at is set

    nb_after = client.get(f"/v1/nodes/{dst['id']}/neighborhood", headers=h).json()
    assert len(nb_after["edges"]) == 0  # dropped out of neighborhood


def test_edges_delete_missing_returns_404(api):
    client, h = api["client"], api["human"]
    resp = client.delete("/v1/edges/nope2345", headers=h)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "E_NOT_FOUND"


def test_edges_require_auth_missing_token_401(api):
    client = api["client"]
    resp = client.post(
        "/v1/edges",
        json={
            "src": "aaaaaaaa",
            "dst": "bbbbbbbb",
            "edge_type": "composes",
            "facet_binding": None,
            "provenance": "human",
        },
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "E_AUTH"


# --- T4.5: /search -------------------------------------------------------


def test_search_returns_hits_for_matching_query(api):
    client, h = api["client"], api["human"]
    _create(client, h, body="the quick brown fox jumps")
    _create(client, h, body="an unrelated sentence about oceans")
    resp = client.get("/v1/search", params={"q": "fox"}, headers=h)
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) == 1
    assert "fox" in results[0]["body"]


def test_search_empty_list_for_non_matching_query(api):
    client, h = api["client"], api["human"]
    _create(client, h, body="the quick brown fox jumps")
    resp = client.get("/v1/search", params={"q": "zzznomatchzzz"}, headers=h)
    assert resp.status_code == 200
    assert resp.json()["results"] == []


def test_search_requires_auth_missing_token_401(api):
    client = api["client"]
    resp = client.get("/v1/search", params={"q": "fox"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "E_AUTH"


# --- T4.5: /tokens ---------------------------------------------------------


def test_tokens_human_can_create_list_revoke(api):
    client, h = api["client"], api["human"]
    created = client.post("/v1/tokens", json={"name": "ci-bot", "token_class": "agent"}, headers=h)
    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "ci-bot"
    assert body["class"] == "agent"
    assert "bearer_token" in body and body["bearer_token"]
    assert "secret_hash" not in body

    listed = client.get("/v1/tokens", headers=h)
    assert listed.status_code == 200
    ids = [t["id"] for t in listed.json()["tokens"]]
    assert body["id"] in ids
    # never exposes a secret on list
    assert all("secret_hash" not in t and "bearer_token" not in t for t in listed.json()["tokens"])

    revoked = client.delete(f"/v1/tokens/{body['id']}", headers=h)
    assert revoked.status_code == 200
    assert revoked.json()["revoked"] is True


def test_tokens_created_bearer_authenticates_then_revoked_bearer_rejected(api):
    client, h = api["client"], api["human"]
    created = client.post(
        "/v1/tokens", json={"name": "new-human", "token_class": "human"}, headers=h
    ).json()
    new_headers = {"Authorization": f"Bearer {created['bearer_token']}"}

    # the newly-minted bearer actually authenticates
    resp = client.get("/v1/tokens", headers=new_headers)
    assert resp.status_code == 200

    client.delete(f"/v1/tokens/{created['id']}", headers=h)

    # after revocation the same bearer is rejected
    resp2 = client.get("/v1/tokens", headers=new_headers)
    assert resp2.status_code == 401
    assert resp2.json()["error"]["code"] == "E_AUTH_REVOKED"


def test_tokens_revoke_missing_returns_404(api):
    client, h = api["client"], api["human"]
    resp = client.delete("/v1/tokens/nope2345", headers=h)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "E_NOT_FOUND"


def test_tokens_agent_rejected_on_every_verb(api):
    client, h, agent = api["client"], api["human"], api["agent"]
    # GET (list)
    resp = client.get("/v1/tokens", headers=agent)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "E_HUMAN_ONLY"
    # POST (create)
    resp = client.post("/v1/tokens", json={"name": "x", "token_class": "agent"}, headers=agent)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "E_HUMAN_ONLY"
    # DELETE (revoke) — use a real token id created by a human so the 403
    # can't be confused with a 404
    real = client.post("/v1/tokens", json={"name": "y", "token_class": "human"}, headers=h).json()
    resp = client.delete(f"/v1/tokens/{real['id']}", headers=agent)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "E_HUMAN_ONLY"


def test_tokens_require_auth_missing_token_401(api):
    client = api["client"]
    resp = client.get("/v1/tokens")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "E_AUTH"


# --- T4.10: /sync/roots ----------------------------------------------------


def test_sync_roots_human_can_register_and_list(api):
    client, h = api["client"], api["human"]
    resp = client.post(
        "/v1/sync/roots",
        json={"name": "notes", "root_path": "/home/user/notes"},
        headers=h,
    )
    assert resp.status_code == 201
    assert resp.json()["id"]
    assert resp.json()["name"] == "notes"
    assert resp.json()["root_path"] == "/home/user/notes"

    listed = client.get("/v1/sync/roots", headers=h)
    assert listed.status_code == 200
    names = [root["name"] for root in listed.json()["sync_roots"]]
    assert "notes" in names


def test_sync_roots_survive_connection_restart(tmp_path):
    db_path = tmp_path / "restart.db"
    conn = store.connect(db_path, check_same_thread=False)
    store.run_migrations(conn)
    secret = auth.mint_secret()
    _insert_token(conn, "humanroot", secret, "human")
    headers = {"Authorization": f"Bearer {auth.format_bearer_token('humanroot', secret)}"}
    with TestClient(create_app(conn=conn)) as client:
        response = client.post(
            "/v1/sync/roots",
            json={"name": "durable", "root_path": "/home/user/durable"},
            headers=headers,
        )
        assert response.status_code == 201
    conn.close()

    reopened = store.connect(db_path, check_same_thread=False)
    try:
        roots = store.list_sync_roots(reopened)
        assert [(root["name"], root["root_path"]) for root in roots] == [
            ("durable", "/home/user/durable")
        ]
    finally:
        reopened.close()


def test_sync_roots_agent_rejected_on_get_and_post(api):
    client, agent = api["client"], api["agent"]
    resp = client.get("/v1/sync/roots", headers=agent)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "E_HUMAN_ONLY"

    resp = client.post("/v1/sync/roots", json={"name": "x", "root_path": "/tmp/x"}, headers=agent)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "E_HUMAN_ONLY"


def test_sync_roots_require_auth_missing_token_401(api):
    client = api["client"]
    resp = client.get("/v1/sync/roots")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "E_AUTH"


def test_old_vaults_route_is_absent(api):
    assert api["client"].get("/v1/vaults").status_code == 404


# --- T4.6: agent-token proposal rewriting -----------------------------------


def _review_rows(conn):
    return conn.execute(
        "SELECT id, node_id, cause_kind, cause_ref, facet, created_at, resolved_at, "
        "resolution FROM review_queue"
    ).fetchall()


def test_agent_writes_become_proposals(api):
    """Named T4.6 Verify test (build-plan): agent POST /nodes -> proposal, not a mutation."""
    client, agent, conn = api["client"], api["agent"], api["conn"]
    before_nodes = conn.execute("SELECT count(*) FROM nodes").fetchone()[0]

    resp = _create(client, agent, body="an agent-proposed claim")
    assert resp.status_code == 202
    data = resp.json()
    assert data["proposed"] is True
    review = data["review"]
    assert review["cause_kind"] == "proposal"

    after_nodes = conn.execute("SELECT count(*) FROM nodes").fetchone()[0]
    assert after_nodes == before_nodes  # no mutation happened

    rows = _review_rows(conn)
    assert len(rows) == 1
    row = rows[0]
    assert row[0] == review["id"]
    assert row[2] == "proposal"  # cause_kind exactly 'proposal'
    assert row[5] == review["created_at"]  # created_at persisted
    assert row[1] is None
    assert review["node_id"] is None

    # the proposed request is recoverable from cause_ref (canonical JSON)
    import json

    recovered = json.loads(row[3])
    assert recovered["method"] == "POST"
    assert recovered["path"] == "/v1/nodes"
    assert recovered["body"]["body"].strip() == "an agent-proposed claim"


def test_human_post_nodes_mutates_directly_no_proposal(api):
    """Same call as a human mutates directly (T4.6 DoD's other half)."""
    client, h, conn = api["client"], api["human"], api["conn"]
    resp = _create(client, h, body="a human-created claim")
    assert resp.status_code == 201
    node = resp.json()
    assert conn.execute("SELECT 1 FROM nodes WHERE id=?", (node["id"],)).fetchone() is not None
    assert len(_review_rows(conn)) == 0


def test_agent_patch_nodes_becomes_proposal_and_does_not_mutate(api):
    client, h, agent, conn = api["client"], api["human"], api["agent"], api["conn"]
    node = _create(client, h, body="original body").json()

    resp = client.patch(
        f"/v1/nodes/{node['id']}",
        json={"body": "agent-proposed edit", "change_class": "patch", "facets_touched": []},
        headers=agent,
    )
    assert resp.status_code == 202
    review = resp.json()["review"]
    assert review["cause_kind"] == "proposal"
    assert review["node_id"] == node["id"]

    # underlying node body is unchanged
    got = client.get(f"/v1/nodes/{node['id']}", headers=h).json()
    assert got["body"].strip() == "original body"
    rows = _review_rows(conn)
    assert len(rows) == 1
    assert rows[0][1] == node["id"]


def test_agent_delete_nodes_becomes_proposal_and_does_not_mutate(api):
    client, h, agent, conn = api["client"], api["human"], api["agent"], api["conn"]
    node = _create(client, h, body="do not delete me").json()

    resp = client.delete(f"/v1/nodes/{node['id']}", headers=agent)
    assert resp.status_code == 202
    review = resp.json()["review"]
    assert review["cause_kind"] == "proposal"

    got = client.get(f"/v1/nodes/{node['id']}", headers=h)
    assert got.status_code == 200  # node still exists
    assert len(_review_rows(conn)) == 1


def test_agent_post_edges_becomes_proposal_and_does_not_mutate(api):
    client, h, agent, conn = api["client"], api["human"], api["agent"], api["conn"]
    src = _create(client, h, body="source").json()
    dst = _create(client, h, body="target").json()

    resp = client.post(
        "/v1/edges",
        json={
            "src": src["id"],
            "dst": dst["id"],
            "edge_type": "supports",
            "facet_binding": "*",
            "provenance": "agent",
        },
        headers=agent,
    )
    assert resp.status_code == 202
    review = resp.json()["review"]
    assert review["cause_kind"] == "proposal"
    assert review["node_id"] == dst["id"]

    nb = client.get(f"/v1/nodes/{dst['id']}/neighborhood", headers=h).json()
    assert len(nb["edges"]) == 0  # no edge was actually created
    rows = _review_rows(conn)
    assert len(rows) == 1


def test_agent_delete_edges_becomes_proposal_and_does_not_mutate(api):
    client, h, agent, conn = api["client"], api["human"], api["agent"], api["conn"]
    src = _create(client, h, body="source").json()
    dst = _create(client, h, body="target").json()
    edge = client.post(
        "/v1/edges",
        json={
            "src": src["id"],
            "dst": dst["id"],
            "edge_type": "supports",
            "facet_binding": "*",
            "provenance": "human",
        },
        headers=h,
    ).json()

    resp = client.delete(f"/v1/edges/{edge['id']}", headers=agent)
    assert resp.status_code == 202
    review = resp.json()["review"]
    assert review["cause_kind"] == "proposal"
    assert review["node_id"] == dst["id"]

    row = conn.execute("SELECT retracted_at FROM edges WHERE id=?", (edge["id"],)).fetchone()
    assert row[0] is None  # never retracted


def test_agent_rejected_outright_on_every_empty_endpoint_no_proposal(api):
    """∅ endpoints reject agent tokens (403), never proposalize them."""
    client, h, agent, conn = api["client"], api["human"], api["agent"], api["conn"]
    node = _create(client, h).json()

    # /nodes/{id}/vet
    resp = client.post(f"/v1/nodes/{node['id']}/vet", headers=agent)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "E_HUMAN_ONLY"

    # POST /tokens
    resp = client.post("/v1/tokens", json={"name": "x", "token_class": "agent"}, headers=agent)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "E_HUMAN_ONLY"

    # POST /sync/roots
    resp = client.post("/v1/sync/roots", json={"name": "x", "root_path": "/tmp/x"}, headers=agent)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "E_HUMAN_ONLY"

    assert len(_review_rows(conn)) == 0  # nothing was queued, only rejected


def test_agent_split_and_merge_also_become_proposals(api):
    client, h, agent, conn = api["client"], api["human"], api["agent"], api["conn"]
    whole = _create(client, h, body="whole").json()
    other = _create(client, h, body="other").json()

    resp = client.post(
        f"/v1/nodes/{whole['id']}/split",
        json={
            "parts": [
                {"node_type": "claim", "body": "part one"},
                {"node_type": "claim", "body": "part two"},
            ]
        },
        headers=agent,
    )
    assert resp.status_code == 202
    assert resp.json()["review"]["cause_kind"] == "proposal"
    # no successors were actually created
    hist = client.get(f"/v1/nodes/{whole['id']}/history", headers=h).json()["history"]
    assert len(hist) == 1  # still just genesis

    resp = client.post(
        f"/v1/nodes/{whole['id']}/merge",
        json={"ids": [other["id"]]},
        headers=agent,
    )
    assert resp.status_code == 202
    assert resp.json()["review"]["cause_kind"] == "proposal"
    # other node was not merged away
    assert client.get(f"/v1/nodes/{other['id']}", headers=h).status_code == 200

    assert len(_review_rows(conn)) == 2


# --- T5.7: GET /sync/status · POST /sync/rescan -----------------------------


def _managed(body: str) -> str:
    return canonicalize_text(f"---\ntm: 1\n---\n{body}")


def test_sync_status_reports_seeded_roots_and_files(api, tmp_path):
    client, h, conn = api["client"], api["human"], api["conn"]
    root = client.post(
        "/v1/sync/roots", json={"name": "vault", "root_path": str(tmp_path)}, headers=h
    ).json()
    path = str(tmp_path / "note.md")
    base_store.put(conn, root["id"], path, _managed("hello\n"))

    resp = client.get("/v1/sync/status", headers=h)
    assert resp.status_code == 200
    body = resp.json()
    [entry] = [r for r in body["sync_roots"] if r["id"] == root["id"]]
    assert entry["name"] == "vault"
    assert entry["root_path"] == str(tmp_path)
    assert [f["path"] for f in entry["files"]] == [path]
    assert entry["files"][0]["contract_version"] == 1
    assert entry["violations"] == []
    assert entry["pauses"] == []
    assert entry["conflicts"] == []


def test_sync_status_splits_violations_pauses_and_conflicts_by_root(api, tmp_path):
    client, h, conn = api["client"], api["human"], api["conn"]
    root = client.post(
        "/v1/sync/roots", json={"name": "vault", "root_path": str(tmp_path)}, headers=h
    ).json()
    path = str(tmp_path / "note.md")
    base_store.put(conn, root["id"], path, _managed("hello\n"))

    # A plain (non-pause) violation.
    store.enqueue_review(
        conn,
        None,
        "violation",
        cause_ref=json.dumps({"path": path, "code": "E_DUP_ID", "line_nos": [1], "message": "x"}),
    )
    # A pause: same cause_kind='violation', but cause_ref carries pause=true
    # (spec/M3 follow-up -- never a distinct cause_kind, never branched on
    # via a borrowed ViolationCode).
    store.enqueue_review(
        conn,
        None,
        "violation",
        cause_ref=json.dumps({"path": path, "pause": True, "diff": "--- a\n+++ b\n"}),
    )
    # A conflict.
    node = _create(client, h).json()
    store.enqueue_review(
        conn,
        node["id"],
        "conflict",
        cause_ref=json.dumps({"path": path, "vault_text": "x", "base_text": "y"}),
    )

    resp = client.get("/v1/sync/status", headers=h)
    assert resp.status_code == 200
    body = resp.json()
    [entry] = [r for r in body["sync_roots"] if r["id"] == root["id"]]

    assert len(entry["violations"]) == 1
    assert entry["violations"][0]["cause_kind"] == "violation"
    assert entry["violations"][0]["path"] == path

    assert len(entry["pauses"]) == 1
    assert entry["pauses"][0]["path"] == path

    assert len(entry["conflicts"]) == 1
    assert entry["conflicts"][0]["cause_kind"] == "conflict"
    assert entry["conflicts"][0]["node_id"] == node["id"]

    assert body["unresolved"] == []  # every review correlated to a known root


def test_sync_rescan_converges_a_real_vault_edit_and_returns_summary(api, tmp_path):
    client, h, conn = api["client"], api["human"], api["conn"]
    root = client.post(
        "/v1/sync/roots", json={"name": "vault", "root_path": str(tmp_path)}, headers=h
    ).json()
    node = _create(client, h, body="line one").json()
    x = node["id"]
    base_text = render(parse(_managed(f"line one {contract_anchor(x)}\n")))
    path = tmp_path / "note.md"
    path.write_text(_managed(f"line two {contract_anchor(x)}\n"), encoding="utf-8")
    base_store.put(conn, root["id"], str(path), base_text)

    resp = client.post("/v1/sync/rescan", headers=h)
    assert resp.status_code == 200
    summary = resp.json()
    assert summary["files_reconciled"] == 1
    assert summary["files_missing"] == 0
    assert summary["reviews_open"] == 0

    # Real store effects, not just an HTTP 200: the vault-side edit landed
    # as a new commit on the hub node, and the base snapshot advanced to
    # match the converged (canonical) file.
    hist = client.get(f"/v1/nodes/{x}/history", headers=h).json()["history"]
    assert len(hist) == 2
    got = client.get(f"/v1/nodes/{x}", headers=h).json()
    assert got["body"].strip() == "line two"
    final_on_disk = path.read_text(encoding="utf-8")
    assert store.read_base_snapshot(conn, root["id"], str(path)) == final_on_disk


def test_sync_status_and_rescan_require_auth_missing_token_401(api):
    client = api["client"]
    resp = client.get("/v1/sync/status")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "E_AUTH"

    resp = client.post("/v1/sync/rescan")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "E_AUTH"


def test_sync_status_and_rescan_allow_agent_tokens(api):
    """``/sync/*`` carries no ∅ marker in the spec table -- agents may call it too."""
    client, agent = api["client"], api["agent"]
    resp = client.get("/v1/sync/status", headers=agent)
    assert resp.status_code == 200
    assert resp.json()["sync_roots"] == []

    resp = client.post("/v1/sync/rescan", headers=agent)
    assert resp.status_code == 200
    assert resp.json() == {"files_reconciled": 0, "files_missing": 0, "reviews_open": 0}
