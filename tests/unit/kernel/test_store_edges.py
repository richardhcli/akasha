"""Edge create/retract + neighborhood/search tests (task T1.4, spec §4.5, §4.2, §4.4).

Covers: an invalid justification edge (missing facet_binding) is rejected
before any write; a valid edge appears in a live neighborhood; retracting
an edge sets retracted_at and drops it from the neighborhood; search finds
a node by a body term and stays in sync across commit_node edits.
"""

import pydantic
import pytest

from akasha.kernel import store


def _fresh_conn(tmp_path):
    conn = store.connect(tmp_path / "store_edges.db")
    store.run_migrations(conn)
    return conn


def test_create_edge_rejects_justification_edge_without_facet_binding(tmp_path):
    conn = _fresh_conn(tmp_path)
    a = store.create_node(conn, "claim", "claim a", author="alice")
    b = store.create_node(conn, "evidence", "evidence b", author="alice")

    with pytest.raises(pydantic.ValidationError):
        store.create_edge(
            conn,
            src=a.id,
            dst=b.id,
            edge_type="supports",
            facet_binding=None,
            provenance="human",
        )

    # nothing was written
    row = conn.execute("SELECT COUNT(*) FROM edges").fetchone()
    assert row[0] == 0


def test_create_edge_accepts_justification_edge_with_wildcard_facet_binding(tmp_path):
    conn = _fresh_conn(tmp_path)
    a = store.create_node(conn, "claim", "claim a", author="alice")
    b = store.create_node(conn, "evidence", "evidence b", author="alice")

    edge = store.create_edge(
        conn,
        src=a.id,
        dst=b.id,
        edge_type="supports",
        facet_binding="*",
        provenance="human",
    )
    assert edge.id
    assert edge.src == a.id
    assert edge.dst == b.id
    assert edge.facet_binding == "*"

    row = conn.execute("SELECT retracted_at FROM edges WHERE id=?", (edge.id,)).fetchone()
    assert row is not None
    assert row[0] is None


def test_create_edge_allows_none_facet_binding_for_composes(tmp_path):
    conn = _fresh_conn(tmp_path)
    a = store.create_node(conn, "entity", "whole", author="alice")
    b = store.create_node(conn, "entity", "part", author="alice")

    edge = store.create_edge(
        conn,
        src=a.id,
        dst=b.id,
        edge_type="composes",
        facet_binding=None,
        provenance="human",
    )
    assert edge.facet_binding is None


def test_neighborhood_returns_live_edge_one_hop(tmp_path):
    conn = _fresh_conn(tmp_path)
    a = store.create_node(conn, "claim", "claim a", author="alice")
    b = store.create_node(conn, "evidence", "evidence b", author="alice")
    edge = store.create_edge(
        conn,
        src=a.id,
        dst=b.id,
        edge_type="supports",
        facet_binding="*",
        provenance="human",
    )

    result = store.neighborhood(conn, a.id, hops=1)
    assert set(result["node_ids"]) == {a.id, b.id}
    assert [e.id for e in result["edges"]] == [edge.id]


def test_neighborhood_two_hops_reaches_second_degree_node(tmp_path):
    conn = _fresh_conn(tmp_path)
    a = store.create_node(conn, "claim", "claim a", author="alice")
    b = store.create_node(conn, "evidence", "evidence b", author="alice")
    c = store.create_node(conn, "evidence", "evidence c", author="alice")
    store.create_edge(
        conn, src=a.id, dst=b.id, edge_type="supports", facet_binding="*", provenance="human"
    )
    store.create_edge(
        conn, src=b.id, dst=c.id, edge_type="cites", facet_binding="*", provenance="human"
    )

    one_hop = store.neighborhood(conn, a.id, hops=1)
    assert c.id not in one_hop["node_ids"]

    two_hop = store.neighborhood(conn, a.id, hops=2)
    assert set(two_hop["node_ids"]) == {a.id, b.id, c.id}


def test_retract_edge_sets_retracted_at_and_drops_from_neighborhood(tmp_path):
    conn = _fresh_conn(tmp_path)
    a = store.create_node(conn, "claim", "claim a", author="alice")
    b = store.create_node(conn, "evidence", "evidence b", author="alice")
    edge = store.create_edge(
        conn,
        src=a.id,
        dst=b.id,
        edge_type="supports",
        facet_binding="*",
        provenance="human",
    )

    store.retract_edge(conn, edge.id)

    row = conn.execute("SELECT retracted_at FROM edges WHERE id=?", (edge.id,)).fetchone()
    assert row[0] is not None  # row still present, never deleted

    result = store.neighborhood(conn, a.id, hops=1)
    assert edge.id not in [e.id for e in result["edges"]]
    assert b.id not in result["node_ids"]


def test_retract_edge_raises_on_unknown_id(tmp_path):
    conn = _fresh_conn(tmp_path)
    with pytest.raises(store.EdgeNotFoundError):
        store.retract_edge(conn, "zzzzzzzz")


def test_search_returns_node_by_body_term(tmp_path):
    conn = _fresh_conn(tmp_path)
    node = store.create_node(conn, "claim", "the ionosphere reflects radio waves", author="alice")
    store.create_node(conn, "claim", "an unrelated statement about turnips", author="alice")

    results = store.search(conn, "ionosphere")
    assert [n.id for n in results] == [node.id]


def test_search_stays_in_sync_after_commit_node_edit(tmp_path):
    conn = _fresh_conn(tmp_path)
    node = store.create_node(conn, "claim", "original wording about frogs", author="alice")

    assert [n.id for n in store.search(conn, "frogs")] == [node.id]

    store.commit_node(
        conn,
        node.id,
        new_body="revised wording about toads",
        change_class="patch",
        facets_touched=[],
        author="bob",
    )

    assert store.search(conn, "frogs") == []
    assert [n.id for n in store.search(conn, "toads")] == [node.id]
