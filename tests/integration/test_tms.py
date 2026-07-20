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

    # (4) close the last remaining open child. Task T10.2c note: since
    # store.commit_node's own commit-path wiring now evaluates
    # all_subtasks_closed for the committed node's parent supertask(s) in
    # the SAME transaction (see store.commit_node's T10.2c comment), this
    # single commit_node call is itself what fires the trigger — flagging
    # the supertask is a direct, immediate side effect of this commit, not
    # something a caller must separately trigger by calling evaluate().
    store.commit_node(
        conn,
        children[2]["id"],
        task_state="done",
        change_class="patch",
        facets_touched=[],
        author="test",
    )

    # (5) fires exactly once, as a side effect of the commit above: enqueue
    # subtasks_closed; supertask stays open.
    open_reviews = store.find_open_reviews(
        conn, node_id=supertask["id"], cause_kind="subtasks_closed"
    )
    assert len(open_reviews) == 1
    assert open_reviews[0]["cause_kind"] == "subtasks_closed"
    assert open_reviews[0]["node_id"] == supertask["id"]
    assert store.get_node(conn, supertask["id"]).task_state == "open"

    # (6) idempotent: a manual evaluate() after the fact finds the review
    # already open (via its own find_open_reviews gate) and enqueues
    # nothing new — the commit-path wiring got there first.
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


# --- T10.2c: all_subtasks_closed wired into the REAL commit path -------------
#
# T7.4's test_supertask_flag above (edited by T10.2c: see its step (4)/(5)
# comments) already exercises store.commit_node as its closing mechanism, so
# the wiring this task adds is implicitly covered there too. This test is
# the task's dedicated, explicit end-to-end coverage: it never calls
# triggers.evaluate() at all (unlike test_supertask_flag, which still uses
# it in step (6) to check idempotence) -- every assertion is driven purely
# through store.commit_node, the real production commit path (the same
# function PATCH /nodes and the sync/reconcile checkbox-toggle path both
# call). PatchNodeBody (api/routes/nodes.py) does not accept task_state
# today (out of this task's Files list to add), so store.commit_node is the
# narrowest "real path" available, exactly as api/routes/nodes.py's own
# patch_node route uses it.


def test_supertask_flag_fires_via_real_commit_path_not_direct_evaluate(api):
    """Drives story 8 end-to-end through store.commit_node only (no evaluate() call)."""
    client, h, conn = api["client"], api["human"], api["conn"]

    supertask = _create_node(
        client, h, node_type="task", body="real-path supertask", task_state="open"
    )
    children = [
        _create_node(client, h, node_type="task", body=f"real-path child-{i}", task_state="open")
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

    def open_reviews():
        return store.find_open_reviews(
            conn, node_id=supertask["id"], cause_kind="subtasks_closed"
        )

    # (b) not flagged while any subtask remains open.
    for child in children[:2]:
        store.commit_node(
            conn,
            child["id"],
            task_state="done",
            change_class="patch",
            facets_touched=[],
            author="test",
        )
        assert open_reviews() == []

    # (a) closing the LAST open subtask through the real commit path flags
    # the parent exactly once, as a direct side effect of that one commit.
    store.commit_node(
        conn,
        children[2]["id"],
        task_state="done",
        change_class="patch",
        facets_touched=[],
        author="test",
    )
    first_pass = open_reviews()
    assert len(first_pass) == 1
    assert first_pass[0]["cause_kind"] == "subtasks_closed"
    assert first_pass[0]["node_id"] == supertask["id"]

    # (d) the supertask's own task_state is never auto-closed by this path.
    assert store.get_node(conn, supertask["id"]).task_state == "open"

    # (c) re-committing after all subtasks are already closed (e.g. an
    # idempotent re-application of the same "done" state, or an unrelated
    # patch commit on an already-done child) must not enqueue a duplicate.
    store.commit_node(
        conn,
        children[2]["id"],
        task_state="done",
        change_class="patch",
        facets_touched=[],
        author="test",
    )
    store.commit_node(
        conn,
        children[0]["id"],
        new_body="child-0, edited after supertask already flagged",
        change_class="patch",
        facets_touched=[],
        author="test",
    )
    second_pass = open_reviews()
    assert len(second_pass) == 1
    assert second_pass[0]["id"] == first_pass[0]["id"]  # same row, not a duplicate

    # (d) still never auto-closed after the extra commits above.
    assert store.get_node(conn, supertask["id"]).task_state == "open"


# --- T7.5: review resolutions + daily active-queue cap -----------------------


def _trigger_facet_break(conn, client, headers, *, subscriber_body="subscriber"):
    """Major commit on a facet-bound target → open facet_break on the subscriber.

    Returns (subscriber_id, target_id, review_row, target_facet_id).
    """
    from akasha.kernel.model import Facet

    target_facet = ids.mint()
    target = store.create_node(
        conn,
        "definition",
        "target definition",
        facets=[Facet(facet_id=target_facet, name="tdef", span="tdef", version=1)],
    )
    sub = _create_node(client, headers, body=subscriber_body)
    store.create_edge(
        conn,
        src=sub["id"],
        dst=target.id,
        edge_type="supports",
        facet_binding=target_facet,
        provenance="human",
    )
    store.commit_node(
        conn,
        target.id,
        facets=[Facet(facet_id=target_facet, name="tdef-broken", span="tdef", version=2)],
        change_class="major",
        facets_touched=[target_facet],
        author="test",
    )
    reviews = store.find_open_reviews(conn, node_id=sub["id"], cause_kind="facet_break")
    assert len(reviews) == 1
    return sub["id"], target.id, reviews[0], target_facet


def test_review_still_holds_resolves_item(api):
    from akasha.tms import review

    client, h, conn = api["client"], api["human"], api["conn"]
    sub_id, _target_id, item, _facet = _trigger_facet_break(conn, client, h)

    result = review.resolve_review(conn, item["id"], "still_holds")
    assert result["resolved_at"] is not None
    assert result["resolution"] == "still_holds"
    assert store.find_open_reviews(conn, node_id=sub_id, cause_kind="facet_break") == []
    persisted = store.get_review(conn, item["id"])
    assert persisted["resolved_at"] is not None
    assert persisted["resolution"] == "still_holds"


def test_review_retracted_resolves_item(api):
    from akasha.tms import review

    client, h, conn = api["client"], api["human"], api["conn"]
    sub_id, _target_id, item, _facet = _trigger_facet_break(
        conn, client, h, subscriber_body="to retract"
    )

    result = review.resolve_review(conn, item["id"], "retracted")
    assert result["resolved_at"] is not None
    assert result["resolution"] == "retracted"
    assert store.find_open_reviews(conn, node_id=sub_id, cause_kind="facet_break") == []
    persisted = store.get_review(conn, item["id"])
    assert persisted["resolution"] == "retracted"


def test_review_revised_reclassifies_and_cascades(api):
    """Three-node chain: major on C → review on B; revised on B cascades to A."""
    from akasha.kernel.model import Facet
    from akasha.tms import review

    conn = api["conn"]
    facet_c = ids.mint()
    facet_b = ids.mint()

    node_c = store.create_node(
        conn,
        "definition",
        "node C",
        facets=[Facet(facet_id=facet_c, name="fc", span="fc", version=1)],
    )
    node_b = store.create_node(
        conn,
        "claim",
        "node B",
        facets=[Facet(facet_id=facet_b, name="fb", span="fb", version=1)],
    )
    node_a = store.create_node(conn, "claim", "node A")

    store.create_edge(
        conn,
        src=node_b.id,
        dst=node_c.id,
        edge_type="supports",
        facet_binding=facet_c,
        provenance="human",
    )
    store.create_edge(
        conn,
        src=node_a.id,
        dst=node_b.id,
        edge_type="supports",
        facet_binding=facet_b,
        provenance="human",
    )

    # Break C's facet → open facet_break on B (the review we will resolve).
    store.commit_node(
        conn,
        node_c.id,
        facets=[Facet(facet_id=facet_c, name="fc-broken", span="fc", version=2)],
        change_class="major",
        facets_touched=[facet_c],
        author="test",
    )
    reviews_b = store.find_open_reviews(conn, node_id=node_b.id, cause_kind="facet_break")
    assert len(reviews_b) == 1
    review_b_id = reviews_b[0]["id"]
    assert store.find_open_reviews(conn, node_id=node_a.id, cause_kind="facet_break") == []

    # revised → commit_node on B (major touching A's binding) then resolve.
    result = review.resolve_review(
        conn,
        review_b_id,
        "revised",
        new_body="node B revised after cascade",
        change_class="major",
        facets_touched=[facet_b],
        author="test",
        message="reclassify after review",
    )
    assert result["resolution"] == "revised"
    assert result["resolved_at"] is not None
    closed = store.get_review(conn, review_b_id)
    assert closed["resolution"] == "revised"
    assert closed["resolved_at"] is not None
    assert store.find_open_reviews(conn, node_id=node_b.id, cause_kind="facet_break") == []

    # Cascade observable: A now has a NEW open facet_break from B's major commit.
    reviews_a = store.find_open_reviews(conn, node_id=node_a.id, cause_kind="facet_break")
    assert len(reviews_a) >= 1


def test_review_dismissed_allowed_for_violation(api):
    from akasha.tms import review

    conn = api["conn"]
    node = store.create_node(conn, "claim", "violation subject")
    item = store.enqueue_review(conn, node.id, "violation", cause_ref="E_TEST")

    result = review.resolve_review(conn, item["id"], "dismissed")
    assert result["resolved_at"] is not None
    assert result["resolution"] == "dismissed"
    persisted = store.get_review(conn, item["id"])
    assert persisted["resolution"] == "dismissed"
    assert persisted["resolved_at"] is not None


def test_review_dismissed_rejected_for_non_violation(api):
    from akasha.tms import review

    conn = api["conn"]
    node = store.create_node(conn, "claim", "facet break subject")
    item = store.enqueue_review(conn, node.id, "facet_break", cause_ref="commit-x", facet="*")

    with pytest.raises(review.DismissalNotAllowedError):
        review.resolve_review(conn, item["id"], "dismissed")

    still_open = store.find_open_reviews(conn, node_id=node.id, cause_kind="facet_break")
    assert len(still_open) == 1
    assert still_open[0]["id"] == item["id"]
    assert still_open[0]["resolved_at"] is None
    persisted = store.get_review(conn, item["id"])
    assert persisted["resolved_at"] is None
    assert persisted["resolution"] is None


def test_review_proposal_approval_mints_once(api):
    from akasha.kernel.canonical import canonical_json
    from akasha.tms import review

    conn = api["conn"]
    envelope = {
        "method": "POST",
        "path": "/v1/nodes",
        "body": {"node_type": "claim", "body": "a proposed node"},
    }
    cause_ref = canonical_json(envelope).decode("utf-8")
    item = store.enqueue_review(conn, None, "proposal", cause_ref=cause_ref)
    nodes_before = conn.execute("SELECT count(*) FROM nodes").fetchone()[0]

    node_id = review.approve_proposal(conn, item["id"])
    node = store.get_node(conn, node_id)
    # canonicalize_text appends a trailing newline (spec §4.3).
    assert node.body == "a proposed node\n"
    assert node.node_type == "claim"

    persisted = store.get_review(conn, item["id"])
    assert persisted["node_id"] == node_id
    assert persisted["resolved_at"] is not None
    # SPEC-QUESTION (T7.5): enum has no 'approved'; implementation uses still_holds.
    assert persisted["resolution"] == "still_holds"

    nodes_after_first = conn.execute("SELECT count(*) FROM nodes").fetchone()[0]
    assert nodes_after_first == nodes_before + 1
    matching = conn.execute(
        "SELECT count(*) FROM nodes WHERE id=?", (node_id,)
    ).fetchone()[0]
    assert matching == 1
    # Exactly one node carries this body (idempotency baseline before 2nd approve).
    body_matches = [
        n_id
        for (n_id,) in conn.execute("SELECT id FROM nodes").fetchall()
        if store.get_node(conn, n_id).body == "a proposed node\n"
    ]
    assert body_matches == [node_id]

    with pytest.raises(store.ReviewAlreadyResolvedError):
        review.approve_proposal(conn, item["id"])
    nodes_after_second = conn.execute("SELECT count(*) FROM nodes").fetchone()[0]
    assert nodes_after_second == nodes_after_first


def test_review_active_queue_daily_cap(api):
    import time

    from akasha.kernel.canonical import canonical_json
    from akasha.tms import review

    conn = api["conn"]

    # Two nodes with deliberately different live inbound-edge counts.
    high = store.create_node(conn, "claim", "high-inbound")
    low = store.create_node(conn, "claim", "low-inbound")
    sources = [store.create_node(conn, "claim", f"src-{i}") for i in range(2)]
    for src in sources:
        store.create_edge(
            conn,
            src=src.id,
            dst=high.id,
            edge_type="supports",
            facet_binding="*",
            provenance="human",
        )
    assert len(store.find_live_edges(conn, dst=high.id)) == 2
    assert len(store.find_live_edges(conn, dst=low.id)) == 0

    # Enqueue high then low close together so inbound count is the tiebreaker.
    high_item = store.enqueue_review(conn, high.id, "recheck", cause_ref="high")
    time.sleep(0.002)
    low_item = store.enqueue_review(conn, low.id, "recheck", cause_ref="low")

    # Fill out to 15 open rows (13 more), including one NULL-node_id proposal.
    for i in range(12):
        n = store.create_node(conn, "claim", f"filler-{i}")
        store.enqueue_review(conn, n.id, "recheck", cause_ref=f"filler-{i}")
        time.sleep(0.002)

    proposal_ref = canonical_json(
        {"method": "POST", "path": "/v1/nodes", "body": {"node_type": "claim", "body": "p"}}
    ).decode("utf-8")
    proposal = store.enqueue_review(conn, None, "proposal", cause_ref=proposal_ref)

    open_all = store.find_open_reviews(conn)
    assert len(open_all) == 15

    queue = review.active_queue(conn)
    assert len(queue) == 10

    # Oldest-first among equal inbound: high was enqueued before low; both
    # start with the earliest timestamps among the 15, and high has more
    # inbound edges so it must appear before low.
    ids_in_queue = [r["id"] for r in queue]
    assert high_item["id"] in ids_in_queue
    assert low_item["id"] in ids_in_queue
    assert ids_in_queue.index(high_item["id"]) < ids_in_queue.index(low_item["id"])

    # created_at non-decreasing when inbound counts are equal is implied by
    # the sort key; check the full queue is sorted by (created_at, -inbound).
    def inbound_of(row: dict) -> int:
        if row["node_id"] is None:
            return 0
        return len(store.find_live_edges(conn, dst=row["node_id"]))

    for earlier, later in zip(queue, queue[1:], strict=False):
        key_e = (earlier["created_at"], -inbound_of(earlier))
        key_l = (later["created_at"], -inbound_of(later))
        assert key_e <= key_l

    # NULL-node_id proposal must not crash ordering and must count as inbound=0
    # (must not jump ahead of older, equal-inbound rows via the dst=None landmine).
    proposal_in_full = next(r for r in open_all if r["id"] == proposal["id"])
    assert proposal_in_full["node_id"] is None
    # Proposal is the newest of the 15; with inbound=0 it should be outside
    # the top-10 window (or at worst last among equals — never first).
    if proposal["id"] in ids_in_queue:
        assert ids_in_queue.index(proposal["id"]) > 0
    assert queue[0]["id"] != proposal["id"]


def test_review_active_queue_orders_by_inbound_count_on_created_at_tie(api, monkeypatch):
    """Freeze created_at so inbound-edge count is the ONLY discriminator.

    Without this tie, ``created_at`` alone (distinct per row) would make the
    ordering assertions pass even if the inbound-count tiebreaker were never
    implemented, or if the ``find_live_edges(dst=None)``-returns-everything
    landmine silently gave a NULL-node_id proposal a leaked nonzero inbound
    count instead of the required 0.
    """
    from akasha.kernel.canonical import canonical_json
    from akasha.tms import review

    conn = api["conn"]
    monkeypatch.setattr(store, "_now", lambda: "2026-07-14T00:00:00.000000+00:00")

    high = store.create_node(conn, "claim", "hi")  # will get 2 live inbound edges
    low = store.create_node(conn, "claim", "lo")  # 0 live inbound edges
    for i in range(2):
        s = store.create_node(conn, "claim", f"tiebreak-src-{i}")
        store.create_edge(
            conn,
            src=s.id,
            dst=high.id,
            edge_type="supports",
            facet_binding="*",
            provenance="human",
        )
    # Extra edges UNRELATED to high/low so the DB's total live-edge count (5)
    # exceeds high's own inbound count (2). This makes the ``dst=None``
    # landmine (which returns EVERY live edge, not zero) distinguishable: a
    # leaked count of 5 would wrongly outrank high's real count of 2, whereas
    # the correct NULL-node_id handling always yields 0.
    other_a = store.create_node(conn, "claim", "other-a")
    other_b = store.create_node(conn, "claim", "other-b")
    for i in range(3):
        s = store.create_node(conn, "claim", f"noise-src-{i}")
        store.create_edge(
            conn,
            src=s.id,
            dst=other_a.id if i % 2 == 0 else other_b.id,
            edge_type="supports",
            facet_binding="*",
            provenance="human",
        )
    assert len(store.find_live_edges(conn, dst=high.id)) == 2
    assert len(store.find_live_edges(conn, dst=low.id)) == 0

    r_low = store.enqueue_review(conn, low.id, "recheck", cause_ref="lo")
    r_high = store.enqueue_review(conn, high.id, "recheck", cause_ref="hi")
    proposal_ref = canonical_json(
        {"method": "POST", "path": "/v1/nodes", "body": {"node_type": "claim", "body": "p"}}
    ).decode("utf-8")
    r_prop = store.enqueue_review(conn, None, "proposal", cause_ref=proposal_ref)

    rows = [r_low, r_high, r_prop]
    assert len({r["created_at"] for r in rows}) == 1  # confirm the tie is real

    queue_ids = [r["id"] for r in review.active_queue(conn)]

    # tie on created_at => inbound count decides: high (2) before low (0).
    assert queue_ids.index(r_high["id"]) < queue_ids.index(r_low["id"])

    # landmine guard: a NULL-node_id proposal must count as inbound=0, not
    # leak every live edge via find_live_edges(dst=None). If it leaked, the
    # proposal would sort ahead of `high` on this tie; it must not.
    assert queue_ids.index(r_high["id"]) < queue_ids.index(r_prop["id"])


def test_s1_node_retraction_flags_dependents(api):
    """S1+ retract: facet_break on bound + '*'-bound deps; invalidate before reassign."""
    from akasha.kernel.model import Facet

    conn = api["conn"]

    # --- case 1: pure tombstone flags facet-bound and '*'-bound dependents ---
    facet_e = ids.mint()
    evidence = store.create_node(
        conn,
        "evidence",
        "evidence body",
        facets=[Facet(facet_id=facet_e, name="fe", span="fe", version=1)],
    )
    dependent = store.create_node(conn, "claim", "dependent D")
    store.create_edge(
        conn,
        src=dependent.id,
        dst=evidence.id,
        edge_type="supports",
        facet_binding=facet_e,
        provenance="human",
    )
    dependent2 = store.create_node(conn, "claim", "dependent D2")
    store.create_edge(
        conn,
        src=dependent2.id,
        dst=evidence.id,
        edge_type="supports",
        facet_binding="*",
        provenance="human",
    )

    store.delete_node(conn, evidence.id, tombstone=True)
    assert (
        len(store.find_open_reviews(conn, node_id=dependent.id, cause_kind="facet_break"))
        >= 1
    )
    assert (
        len(store.find_open_reviews(conn, node_id=dependent2.id, cause_kind="facet_break"))
        >= 1
    )

    # --- case 2: redirect_to — invalidate must run before edge reassignment ---
    facet_e2 = ids.mint()
    evidence2 = store.create_node(
        conn,
        "evidence",
        "evidence E2",
        facets=[Facet(facet_id=facet_e2, name="fe2", span="fe2", version=1)],
    )
    dependent3 = store.create_node(conn, "claim", "dependent D3")
    store.create_edge(
        conn,
        src=dependent3.id,
        dst=evidence2.id,
        edge_type="supports",
        facet_binding=facet_e2,
        provenance="human",
    )
    successor = store.create_node(conn, "claim", "successor body")

    store.delete_node(conn, evidence2.id, redirect_to=[successor.id])
    assert (
        len(store.find_open_reviews(conn, node_id=dependent3.id, cause_kind="facet_break"))
        >= 1
    )

