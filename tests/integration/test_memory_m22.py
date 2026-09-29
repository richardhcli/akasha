"""M22 (build-plan T22.1-T22.5): akasha as an AI-memory backend.

T22.1 search options (``mode=any``, ``limit``, ``type``, ``status``; defaults unchanged), T22.2
Unicode query terms, T22.4 the ``journal`` node type, T22.5 contradiction review on an explicit
``contradicts`` edge and the atomic ``supersede`` override (user rulings M22-C, M22-D). Mirrors
``test_contradiction_surfacing.py``'s in-process ``TestClient`` fixture.
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
        (token_id, token_id, cls, auth.hash_secret(secret), None,
         "2026-01-01T00:00:00.000000+00:00", None),
    )  # fmt: skip
    conn.commit()


@pytest.fixture
def api(tmp_path):
    conn = store.connect(tmp_path / "api.db", check_same_thread=False)
    store.run_migrations(conn)
    human, agent = auth.mint_secret(), auth.mint_secret()
    _insert_token(conn, "humantoken", human, "human")
    _insert_token(conn, "agenttoken", agent, "agent")
    return {
        "client": TestClient(create_app(conn=conn)),
        "conn": conn,
        "human": {"Authorization": f"Bearer {auth.format_bearer_token('humantoken', human)}"},
        "agent": {"Authorization": f"Bearer {auth.format_bearer_token('agenttoken', agent)}"},
    }


def _new(api, body: str, node_type: str = "claim") -> str:
    r = api["client"].post(
        "/v1/nodes", json={"node_type": node_type, "body": body}, headers=api["human"]
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _search(api, **params) -> list[str]:
    r = api["client"].get("/v1/search", params=params, headers=api["human"])
    assert r.status_code == 200, r.text
    return [n["id"] for n in r.json()["results"]]


def test_search_defaults_unchanged_and_any_mode(api):
    a = _new(api, "Goaltender is associated with the sport of ice hockey.")
    b = _new(api, "Placekicker is associated with the sport of rugby.")
    question = "Which sport is goaltender associated with?"
    assert _search(api, q=question) == []  # all-terms: "which" matches nothing
    hits = _search(api, q=question, mode="any")
    assert hits[0] == a and set(hits) == {a, b}  # bm25: the goaltender claim ranks first
    assert _search(api, q=question, mode="any", limit=1) == [a]


def test_search_type_and_status_filters(api):
    claim = _new(api, "The chairperson of Fatah is Mahmoud Abbas.")
    journal = _new(api, "User: the chairperson of Fatah is Mahmoud Abbas, I read.", "journal")
    assert set(_search(api, q="Fatah chairperson")) == {claim, journal}
    assert _search(api, q="Fatah chairperson", type="journal") == [journal]
    newer = _new(api, "The chairperson of Fatah is Jibril Rajoub.")
    api["client"].post(f"/v1/nodes/{claim}/supersede", json={"by": newer}, headers=api["human"])
    live = _search(api, q="Fatah chairperson", status="live", type="claim")
    assert live == [newer]
    assert claim in _search(api, q="Fatah chairperson")  # tombstones stay findable by default


def test_unicode_terms_match(api):
    n = _new(api, "Goaltender is associated with the sport of pesäpallo.")
    assert _search(api, q="pesäpallo") == [n]
    assert _search(api, q="Rogério Ceni pesäpallo", mode="any") == [n]


def test_journal_node_type(api):
    j = _new(api, "Dialogue 2026-09-28: the user said the meeting moved to Tuesday.", "journal")
    r = api["client"].get(f"/v1/nodes/{j}", headers=api["human"])
    assert r.status_code == 200 and r.json()["node_type"] == "journal"
    c = _new(api, "The meeting is on Tuesday.")
    store.create_edge(api["conn"], c, j, "cites", "*", "human")  # a claim cites its journal
    assert store.get_maturity(api["conn"], j) == "S1"


def test_contradicts_edge_flags_review(api):
    old = _new(api, "Boston is located in the continent of North America.")
    new = _new(api, "Boston is located in the continent of Asia.")
    edge = store.create_edge(api["conn"], new, old, "contradicts", "*", "human")
    open_ = store.find_open_reviews(api["conn"], node_id=old, cause_kind="contradiction")
    assert [r["cause_ref"] for r in open_] == [edge.id]
    other = store.create_edge(api["conn"], new, old, "supports", "*", "human")
    assert other.edge_type == "supports"  # non-contradicts edges enqueue nothing new
    assert len(store.find_open_reviews(api["conn"], node_id=old)) == 1


def test_supersede_overrides_old_fact(api):
    conn = api["conn"]
    old = _new(api, "The capital of Niger is Niamey.")
    new = _new(api, "The capital of Niger is Agadez.")
    dependent = _new(api, "Niger's capital lies on the Niger river.")
    store.create_edge(conn, dependent, old, "depends_on", "*", "human")
    flag = store.create_edge(conn, new, old, "contradicts", "*", "human")  # flagged first
    r = api["client"].post(f"/v1/nodes/{old}/supersede", json={"by": new}, headers=api["human"])
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["superseded_by"] == new and out["contradicts_edge"] == flag.id
    assert len(out["reviews_resolved"]) == 1
    assert store.get_node(conn, old).status == "tombstone"
    assert store.find_open_reviews(conn, node_id=old, cause_kind="contradiction") == []
    # the dependent is flagged stale and now points at the successor; the challenger is not
    assert store.find_open_reviews(conn, node_id=dependent, cause_kind="facet_break")
    assert store.find_open_reviews(conn, node_id=new, cause_kind="facet_break") == []
    assert [e.dst for e in store.find_live_edges(conn, src=dependent)] == [new]
    # the contradiction stays recorded on the superseded node
    assert [e.id for e in store.find_live_edges(conn, dst=old, edge_type="contradicts")] == [
        flag.id
    ]


def test_supersede_creates_edge_when_missing_and_validates(api):
    conn = api["conn"]
    old, new = _new(api, "X is married to Y."), _new(api, "X is married to Z.")
    c = api["client"]
    assert c.post(f"/v1/nodes/{old}/supersede", json={"by": old}, headers=api["human"]).status_code == 400  # noqa: E501
    assert c.post("/v1/nodes/nope1234/supersede", json={"by": new}, headers=api["human"]).status_code == 404  # noqa: E501
    r = c.post(f"/v1/nodes/{old}/supersede", json={"by": new}, headers=api["agent"])
    assert r.status_code == 202  # agent tokens stay propose-only (spec §4.11)
    assert store.get_node(conn, old).status == "live"
    r = c.post(f"/v1/nodes/{old}/supersede", json={"by": new}, headers=api["human"])
    assert r.status_code == 200
    assert store.find_live_edges(conn, src=new, dst=old, edge_type="contradicts")
    again = c.post(f"/v1/nodes/{old}/supersede", json={"by": new}, headers=api["human"])
    assert again.status_code == 400  # already tombstoned
