"""Invalidation walk tests (task T7.1, spec §4.9).

Covers every branch of the §4.9 subscriber-selection predicate and the
non-transitive damper: touched-facet binding, untouched-facet binding,
wildcard ('*') binding, the damper itself, a retracted edge, a non-track
edge mode, a non-justification/non-composes edge_type, the whole-node
`composes` predicate (both touched and empty-touched), and the "all
facets touched" retraction analogue.
"""

from akasha.kernel import store
from akasha.kernel.model import Facet
from akasha.tms import invalidate


def _fresh_conn(tmp_path):
    conn = store.connect(tmp_path / "invalidate.db")
    store.run_migrations(conn)
    return conn


def _facet(facet_id: str, name: str, version: int = 1) -> Facet:
    return Facet(facet_id=facet_id, name=name, span=name, version=version)


def test_subscriber_bound_to_touched_facet_is_flagged(tmp_path):
    conn = _fresh_conn(tmp_path)
    target = store.create_node(
        conn, "definition", "target", facets=[_facet("f1", "one"), _facet("f2", "two")]
    )
    sub = store.create_node(conn, "claim", "subscriber")
    edge = store.create_edge(
        conn,
        src=sub.id,
        dst=target.id,
        edge_type="supports",
        facet_binding="f1",
        provenance="human",
    )

    result = invalidate.invalidate(conn, target.id, "commit-1", {"f1"})

    assert len(result) == 1
    assert result[0]["node_id"] == sub.id
    assert result[0]["cause_kind"] == "facet_break"
    assert result[0]["cause_ref"] == "commit-1"
    assert result[0]["facet"] == "f1"

    open_reviews = store.find_open_reviews(conn, node_id=sub.id, cause_kind="facet_break")
    assert len(open_reviews) == 1
    assert open_reviews[0]["facet"] == edge.facet_binding


def test_subscriber_bound_to_untouched_facet_is_not_flagged(tmp_path):
    conn = _fresh_conn(tmp_path)
    target = store.create_node(
        conn, "definition", "target", facets=[_facet("f1", "one"), _facet("f2", "two")]
    )
    sub = store.create_node(conn, "claim", "subscriber")
    store.create_edge(
        conn,
        src=sub.id,
        dst=target.id,
        edge_type="supports",
        facet_binding="f2",
        provenance="human",
    )

    result = invalidate.invalidate(conn, target.id, "commit-1", {"f1"})

    assert result == []
    assert store.find_open_reviews(conn, node_id=sub.id, cause_kind="facet_break") == []


def test_wildcard_binding_flags_on_any_break(tmp_path):
    conn = _fresh_conn(tmp_path)
    target = store.create_node(
        conn, "definition", "target", facets=[_facet("f1", "one"), _facet("f2", "two")]
    )
    sub = store.create_node(conn, "claim", "subscriber")
    store.create_edge(
        conn,
        src=sub.id,
        dst=target.id,
        edge_type="supports",
        facet_binding="*",
        provenance="human",
    )

    # touched set is an unrelated facet id -- the wildcard still fires.
    result = invalidate.invalidate(conn, target.id, "commit-1", {"f2"})

    assert len(result) == 1
    assert result[0]["node_id"] == sub.id


def test_non_transitive_damper_prevents_reenqueue(tmp_path):
    conn = _fresh_conn(tmp_path)
    target = store.create_node(conn, "definition", "target", facets=[_facet("f1", "one")])
    sub = store.create_node(conn, "claim", "subscriber")
    store.create_edge(
        conn,
        src=sub.id,
        dst=target.id,
        edge_type="supports",
        facet_binding="f1",
        provenance="human",
    )

    first = invalidate.invalidate(conn, target.id, "commit-1", {"f1"})
    second = invalidate.invalidate(conn, target.id, "commit-2", {"f1"})

    assert len(first) == 1
    assert second == []  # damper: src already has an open facet_break review

    open_reviews = store.find_open_reviews(conn, node_id=sub.id, cause_kind="facet_break")
    assert len(open_reviews) == 1


def test_retracted_edge_is_ignored(tmp_path):
    conn = _fresh_conn(tmp_path)
    target = store.create_node(conn, "definition", "target", facets=[_facet("f1", "one")])
    sub = store.create_node(conn, "claim", "subscriber")
    edge = store.create_edge(
        conn,
        src=sub.id,
        dst=target.id,
        edge_type="supports",
        facet_binding="f1",
        provenance="human",
    )
    store.retract_edge(conn, edge.id)

    result = invalidate.invalidate(conn, target.id, "commit-1", {"f1"})

    assert result == []


def test_pin_mode_edge_is_ignored(tmp_path):
    conn = _fresh_conn(tmp_path)
    target = store.create_node(conn, "definition", "target", facets=[_facet("f1", "one")])
    sub = store.create_node(conn, "claim", "subscriber")
    store.create_edge(
        conn,
        src=sub.id,
        dst=target.id,
        edge_type="supports",
        facet_binding="f1",
        provenance="human",
        mode="pin",
    )

    result = invalidate.invalidate(conn, target.id, "commit-1", {"f1"})

    assert result == []


def test_non_justification_non_composes_edge_type_is_ignored(tmp_path):
    conn = _fresh_conn(tmp_path)
    target = store.create_node(conn, "definition", "target", facets=[_facet("f1", "one")])
    sub = store.create_node(conn, "claim", "subscriber")
    store.create_edge(
        conn,
        src=sub.id,
        dst=target.id,
        edge_type="redirects_to",
        facet_binding=None,
        provenance="human",
    )

    result = invalidate.invalidate(conn, target.id, "commit-1", {"f1"})

    assert result == []


def test_whole_node_composes_edge_flagged_when_touched_nonempty(tmp_path):
    conn = _fresh_conn(tmp_path)
    target = store.create_node(conn, "definition", "target", facets=[_facet("f1", "one")])
    sub = store.create_node(conn, "task", "subscriber")
    store.create_edge(
        conn,
        src=sub.id,
        dst=target.id,
        edge_type="composes",
        facet_binding=None,
        provenance="human",
    )

    result = invalidate.invalidate(conn, target.id, "commit-1", {"f1"})

    assert len(result) == 1
    assert result[0]["node_id"] == sub.id
    assert result[0]["facet"] is None


def test_whole_node_composes_edge_not_flagged_when_touched_empty(tmp_path):
    conn = _fresh_conn(tmp_path)
    target = store.create_node(conn, "definition", "target", facets=[_facet("f1", "one")])
    sub = store.create_node(conn, "task", "subscriber")
    store.create_edge(
        conn,
        src=sub.id,
        dst=target.id,
        edge_type="composes",
        facet_binding=None,
        provenance="human",
    )

    result = invalidate.invalidate(conn, target.id, "commit-1", set())

    assert result == []


def test_all_facets_touched_flags_every_bound_subscriber(tmp_path):
    """Retraction analogue: caller passes touched = all facet ids of the node."""
    conn = _fresh_conn(tmp_path)
    target = store.create_node(
        conn,
        "definition",
        "target",
        facets=[_facet("f1", "one"), _facet("f2", "two"), _facet("f3", "three")],
    )
    sub1 = store.create_node(conn, "claim", "subscriber-1")
    sub2 = store.create_node(conn, "claim", "subscriber-2")
    sub3 = store.create_node(conn, "claim", "subscriber-3")
    store.create_edge(
        conn, src=sub1.id, dst=target.id, edge_type="supports", facet_binding="f1",
        provenance="human",
    )
    store.create_edge(
        conn, src=sub2.id, dst=target.id, edge_type="contradicts", facet_binding="f2",
        provenance="human",
    )
    store.create_edge(
        conn, src=sub3.id, dst=target.id, edge_type="derived_from", facet_binding="f3",
        provenance="human",
    )

    result = invalidate.invalidate(conn, target.id, "commit-1", {"f1", "f2", "f3"})

    flagged = {r["node_id"] for r in result}
    assert flagged == {sub1.id, sub2.id, sub3.id}
