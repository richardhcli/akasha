"""Change-class heuristic + commit-wiring tests (task T7.2, spec §4.9, §4.2).

Covers the pure heuristic (`kernel.commits.default_change_class`) and its
end-to-end wiring into `kernel.store.commit_node`: a major commit
triggers T7.1's `invalidate()` walk atomically inside the commit's own
transaction; a non-major commit does not; an explicit caller-supplied
`change_class` always overrides the heuristic's own would-be answer; and
a node retraction (modeled as a major commit whose `facets_touched` is
every one of the node's facet ids, per `kernel/commits.py`'s module
docstring) flags every bound subscriber, wildcard or specific.
"""

from __future__ import annotations

import sqlite3

from akasha.kernel import store
from akasha.kernel.commits import default_change_class
from akasha.kernel.model import Facet


def _fresh_conn() -> sqlite3.Connection:
    conn = store.connect(":memory:", check_same_thread=False)
    store.run_migrations(conn)
    return conn


def _facet(facet_id: str, name: str, version: int = 1) -> Facet:
    return Facet(facet_id=facet_id, name=name, span=name, version=version)


# --- default_change_class: pure heuristic ------------------------------------


def test_default_major_on_facet_removed():
    old = [_facet("f1", "one")]
    new: list[Facet] = []
    assert default_change_class(old, new, ["f1"]) == "major"


def test_default_major_on_facet_renamed():
    old = [_facet("f1", "one")]
    new = [_facet("f1", "two")]
    assert default_change_class(old, new, ["f1"]) == "major"


def test_default_major_on_touched_facet_version_bump():
    old = [_facet("f1", "one", version=1)]
    new = [_facet("f1", "one", version=2)]
    assert default_change_class(old, new, ["f1"]) == "major"


def test_default_patch_when_version_bump_not_in_facets_touched():
    # Spec §4.9 scopes the version-bump clause to "a *touched* facet's
    # version was bumped" -- a version bump on a facet the caller did not
    # declare touched does not, on its own, default to major.
    old = [_facet("f1", "one", version=1)]
    new = [_facet("f1", "one", version=2)]
    assert default_change_class(old, new, []) == "patch"


def test_default_patch_on_pure_body_edit_no_facet_delta():
    facets = [_facet("f1", "one")]
    assert default_change_class(facets, facets, []) == "patch"


def test_default_patch_on_addition_only():
    old: list[Facet] = []
    new = [_facet("f1", "one")]
    assert default_change_class(old, new, ["f1"]) == "patch"


def test_default_omitted_facets_touched_falls_back_to_computed_diff():
    # Backward-compatible 2-arg call (T1.6 call sites): behaves exactly
    # like passing the module's own `facets_touched(old, new)` diff.
    old = [_facet("f1", "one", version=1)]
    new = [_facet("f1", "one", version=2)]
    assert default_change_class(old, new) == "major"


# --- store.commit_node wiring: major triggers invalidate, patch does not ----


def test_major_commit_via_store_triggers_invalidate():
    conn = _fresh_conn()
    target = store.create_node(
        conn, "definition", "target", facets=[_facet("f1", "one"), _facet("f2", "two")]
    )
    sub1 = store.create_node(conn, "claim", "sub1")
    sub2 = store.create_node(conn, "claim", "sub2")
    store.create_edge(
        conn, src=sub1.id, dst=target.id, edge_type="supports", facet_binding="f1",
        provenance="human",
    )
    store.create_edge(
        conn, src=sub2.id, dst=target.id, edge_type="supports", facet_binding="f2",
        provenance="human",
    )

    store.commit_node(
        conn,
        target.id,
        facets=[_facet("f1", "one-renamed"), _facet("f2", "two")],
        change_class="major",
        facets_touched=["f1"],
        author="human",
    )

    assert len(store.find_open_reviews(conn, node_id=sub1.id, cause_kind="facet_break")) == 1
    assert store.find_open_reviews(conn, node_id=sub2.id, cause_kind="facet_break") == []


def test_non_major_commit_via_store_enqueues_no_reviews():
    conn = _fresh_conn()
    target = store.create_node(conn, "definition", "target", facets=[_facet("f1", "one")])
    sub = store.create_node(conn, "claim", "sub")
    store.create_edge(
        conn, src=sub.id, dst=target.id, edge_type="supports", facet_binding="f1",
        provenance="human",
    )

    store.commit_node(
        conn,
        target.id,
        new_body="fixed a typo",
        change_class="patch",
        facets_touched=[],
        author="human",
    )

    assert store.find_open_reviews(conn, node_id=sub.id, cause_kind="facet_break") == []


# --- explicit override always wins over the heuristic's own answer ----------


def test_explicit_patch_override_suppresses_would_be_major_invalidate():
    conn = _fresh_conn()
    target = store.create_node(conn, "definition", "target", facets=[_facet("f1", "one")])
    sub = store.create_node(conn, "claim", "sub")
    store.create_edge(
        conn, src=sub.id, dst=target.id, edge_type="supports", facet_binding="f1",
        provenance="human",
    )

    # The heuristic itself would say major (a facet is being removed) ...
    assert default_change_class([_facet("f1", "one")], [], ["f1"]) == "major"

    # ... but the caller (UI/CLI) explicitly overrides to patch, and that
    # override -- not the heuristic -- is what commit_node honors.
    store.commit_node(
        conn, target.id, facets=[], change_class="patch", facets_touched=["f1"], author="human",
    )

    assert store.find_open_reviews(conn, node_id=sub.id, cause_kind="facet_break") == []


def test_explicit_major_override_on_pure_body_edit_fires_wildcard_subscriber():
    conn = _fresh_conn()
    target = store.create_node(conn, "definition", "target", facets=[_facet("f1", "one")])
    # A wildcard-bound subscriber, since a pure body edit's facets_touched
    # is empty -- only a '*'-bound subscriber can fire on an empty touched
    # set (§4.9's predicate requires `facet_binding in touched` for a
    # specific binding).
    sub = store.create_node(conn, "claim", "sub")
    store.create_edge(
        conn, src=sub.id, dst=target.id, edge_type="supports", facet_binding="*",
        provenance="human",
    )

    # The heuristic itself would say patch (no facet delta at all) ...
    facets = [_facet("f1", "one")]
    assert default_change_class(facets, facets, []) == "patch"

    # ... but the caller explicitly overrides to major.
    store.commit_node(
        conn,
        target.id,
        new_body="a substantive rewrite, still no facet delta",
        change_class="major",
        facets_touched=[],
        author="human",
    )

    reviews = store.find_open_reviews(conn, node_id=sub.id, cause_kind="facet_break")
    assert len(reviews) == 1
    assert reviews[0]["facet"] == "*"


# --- node retraction: always major, touching all facets ---------------------


def test_node_retraction_as_major_commit_touching_all_facets_flags_every_subscriber():
    conn = _fresh_conn()
    target = store.create_node(
        conn,
        "definition",
        "target",
        facets=[_facet("f1", "one"), _facet("f2", "two")],
    )
    sub_f1 = store.create_node(conn, "claim", "sub-f1")
    sub_f2 = store.create_node(conn, "claim", "sub-f2")
    sub_wild = store.create_node(conn, "claim", "sub-wild")
    store.create_edge(
        conn, src=sub_f1.id, dst=target.id, edge_type="supports", facet_binding="f1",
        provenance="human",
    )
    store.create_edge(
        conn, src=sub_f2.id, dst=target.id, edge_type="contradicts", facet_binding="f2",
        provenance="human",
    )
    store.create_edge(
        conn, src=sub_wild.id, dst=target.id, edge_type="derived_from", facet_binding="*",
        provenance="human",
    )

    all_facet_ids = [f.facet_id for f in target.facets]

    # Node retraction (spec §4.9: "node retraction is always major touching
    # all facets") is modeled as a major commit whose facets_touched is
    # every one of the node's facet ids -- see kernel/commits.py's module
    # docstring for why no bespoke retraction function is needed.
    store.commit_node(
        conn,
        target.id,
        change_class="major",
        facets_touched=all_facet_ids,
        author="human",
        message="retract",
    )

    for sub in (sub_f1, sub_f2, sub_wild):
        reviews = store.find_open_reviews(conn, node_id=sub.id, cause_kind="facet_break")
        assert len(reviews) == 1, f"{sub.id} was not flagged on retraction"
