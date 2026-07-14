"""Deletion/tombstone/redirects/split/merge + maturity-wiring tests
(task T1.6, spec §4.5, §4.6, §4.4 redirects table).

Covers: maturity recompute is wired into create_edge/retract_edge/
commit_node so a node actually reaches S1+ from a live inbound edge; S0
delete_node hard-deletes (node/commits/incident edges, none dangling);
S1+ delete_node without redirect_to/tombstone raises NeedsRedirectError
(E_NEEDS_REDIRECT) and writes nothing; S1+ delete_node with redirect_to
tombstones + inserts a redirects row + reassigns inbound edges; S1+
delete_node with tombstone=True (no redirect) just tombstones;
split_node/merge_nodes create redirects rows and leave zero dangling
live-edge references to retired node ids; commits.facets_touched /
commits.default_change_class narrow heuristics.
"""

from __future__ import annotations

import json

import pytest

from akasha.kernel import store
from akasha.kernel.commits import default_change_class, facets_touched
from akasha.kernel.model import Facet


def _fresh_conn(tmp_path):
    conn = store.connect(tmp_path / "store_lifecycle.db")
    store.run_migrations(conn)
    return conn


def _live_edges_touching(conn, node_id) -> list[tuple[str, str, str]]:
    """(id, src, dst) for every live edge where node_id appears as src or dst."""
    rows = conn.execute(
        "SELECT id, src, dst FROM edges WHERE (src=? OR dst=?) AND retracted_at IS NULL",
        (node_id, node_id),
    ).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


# --- maturity wiring (deferred from T1.5) ----------------------------------


def test_maturity_wiring_create_edge_promotes_dst_to_s1(tmp_path):
    conn = _fresh_conn(tmp_path)
    a = store.create_node(conn, "claim", "claim a", author="alice")
    b = store.create_node(conn, "claim", "claim b", author="alice")
    assert conn.execute("SELECT maturity FROM nodes WHERE id=?", (b.id,)).fetchone()[0] == "S0"

    store.create_edge(
        conn, src=a.id, dst=b.id, edge_type="composes", facet_binding=None, provenance="human"
    )

    assert conn.execute("SELECT maturity FROM nodes WHERE id=?", (b.id,)).fetchone()[0] == "S1"


def test_maturity_wiring_retract_edge_demotes_dst_back(tmp_path):
    conn = _fresh_conn(tmp_path)
    a = store.create_node(conn, "claim", "claim a", author="alice")
    b = store.create_node(conn, "claim", "claim b", author="alice")
    edge = store.create_edge(
        conn, src=a.id, dst=b.id, edge_type="composes", facet_binding=None, provenance="human"
    )
    assert conn.execute("SELECT maturity FROM nodes WHERE id=?", (b.id,)).fetchone()[0] == "S1"

    store.retract_edge(conn, edge.id)

    assert conn.execute("SELECT maturity FROM nodes WHERE id=?", (b.id,)).fetchone()[0] == "S0"


def test_maturity_wiring_commit_node_recomputes_on_facet_change(tmp_path):
    conn = _fresh_conn(tmp_path)
    a = store.create_node(conn, "claim", "claim a", author="alice")
    b = store.create_node(conn, "definition", "def b", author="alice")
    store.create_edge(
        conn, src=a.id, dst=b.id, edge_type="composes", facet_binding=None, provenance="human"
    )
    # S1 (inbound edge) but not S2 yet: definition is not facet-exempt and has 0 facets.
    assert conn.execute("SELECT maturity FROM nodes WHERE id=?", (b.id,)).fetchone()[0] == "S1"

    store.commit_node(
        conn,
        b.id,
        facets=[Facet(facet_id="fct12345", name="f1", span="x", version=1)],
        change_class="minor",
        facets_touched=["fct12345"],
        author="alice",
    )

    assert conn.execute("SELECT maturity FROM nodes WHERE id=?", (b.id,)).fetchone()[0] == "S2"


# --- delete_node -------------------------------------------------------------


def test_delete_node_s0_hard_deletes_node_commits_and_incident_edges(tmp_path):
    conn = _fresh_conn(tmp_path)
    a = store.create_node(conn, "claim", "claim a", author="alice")
    b = store.create_node(conn, "claim", "claim b", author="alice")
    # outbound edge from a (a stays S0: outbound edges don't affect a's own maturity)
    edge = store.create_edge(
        conn, src=a.id, dst=b.id, edge_type="composes", facet_binding=None, provenance="human"
    )
    assert conn.execute("SELECT maturity FROM nodes WHERE id=?", (a.id,)).fetchone()[0] == "S0"

    store.delete_node(conn, a.id)

    assert conn.execute("SELECT 1 FROM nodes WHERE id=?", (a.id,)).fetchone() is None
    assert conn.execute("SELECT COUNT(*) FROM commits WHERE node_id=?", (a.id,)).fetchone()[0] == 0
    assert conn.execute("SELECT 1 FROM edges WHERE id=?", (edge.id,)).fetchone() is None
    assert conn.execute("SELECT 1 FROM nodes_fts WHERE id=?", (a.id,)).fetchone() is None
    # b (which had the incident edge) is untouched and has no dangling edge left
    assert conn.execute("SELECT 1 FROM nodes WHERE id=?", (b.id,)).fetchone() is not None
    assert _live_edges_touching(conn, a.id) == []


def test_delete_node_s1_plus_without_redirect_or_tombstone_raises_and_writes_nothing(tmp_path):
    conn = _fresh_conn(tmp_path)
    a = store.create_node(conn, "claim", "claim a", author="alice")
    b = store.create_node(conn, "claim", "claim b", author="alice")
    store.create_edge(
        conn, src=a.id, dst=b.id, edge_type="composes", facet_binding=None, provenance="human"
    )
    assert conn.execute("SELECT maturity FROM nodes WHERE id=?", (b.id,)).fetchone()[0] == "S1"

    with pytest.raises(store.NeedsRedirectError) as excinfo:
        store.delete_node(conn, b.id)

    assert excinfo.value.code == "E_NEEDS_REDIRECT"
    # nothing was deleted or tombstoned
    row = conn.execute("SELECT status, maturity FROM nodes WHERE id=?", (b.id,)).fetchone()
    assert row == ("live", "S1")
    assert conn.execute("SELECT 1 FROM redirects WHERE old_id=?", (b.id,)).fetchone() is None


def test_delete_node_s1_plus_with_redirect_to_tombstones_and_reassigns_inbound(tmp_path):
    conn = _fresh_conn(tmp_path)
    a = store.create_node(conn, "claim", "claim a", author="alice")
    b = store.create_node(conn, "claim", "claim b", author="alice")
    successor = store.create_node(conn, "claim", "claim successor", author="alice")
    edge = store.create_edge(
        conn, src=a.id, dst=b.id, edge_type="composes", facet_binding=None, provenance="human"
    )
    assert conn.execute("SELECT maturity FROM nodes WHERE id=?", (b.id,)).fetchone()[0] == "S1"

    store.delete_node(conn, b.id, redirect_to=[successor.id])

    row = conn.execute("SELECT status FROM nodes WHERE id=?", (b.id,)).fetchone()
    assert row == ("tombstone",)
    redirect_row = conn.execute(
        "SELECT successors FROM redirects WHERE old_id=?", (b.id,)
    ).fetchone()
    assert json.loads(redirect_row[0]) == [successor.id]
    # the live inbound edge now points at the successor, not the tombstoned node
    new_edge_row = conn.execute(
        "SELECT dst, retracted_at FROM edges WHERE id=?", (edge.id,)
    ).fetchone()
    assert new_edge_row == (successor.id, None)
    assert _live_edges_touching(conn, b.id) == []
    # successor's maturity was recomputed (gained an inbound edge)
    assert (
        conn.execute("SELECT maturity FROM nodes WHERE id=?", (successor.id,)).fetchone()[0] == "S1"
    )


def test_delete_node_s1_plus_with_tombstone_true_and_no_redirect(tmp_path):
    conn = _fresh_conn(tmp_path)
    a = store.create_node(conn, "claim", "claim a", author="alice")
    b = store.create_node(conn, "claim", "claim b", author="alice")
    store.create_edge(
        conn, src=a.id, dst=b.id, edge_type="composes", facet_binding=None, provenance="human"
    )

    store.delete_node(conn, b.id, tombstone=True)

    row = conn.execute("SELECT status FROM nodes WHERE id=?", (b.id,)).fetchone()
    assert row == ("tombstone",)
    assert conn.execute("SELECT 1 FROM redirects WHERE old_id=?", (b.id,)).fetchone() is None


def test_delete_node_not_found_raises(tmp_path):
    conn = _fresh_conn(tmp_path)
    with pytest.raises(store.NodeNotFoundError):
        store.delete_node(conn, "tm-nonexist")


# --- split_node / merge_nodes -------------------------------------------------


def test_split_node_creates_successors_redirect_and_reassigns_inbound(tmp_path):
    conn = _fresh_conn(tmp_path)
    a = store.create_node(conn, "claim", "claim a", author="alice")
    target = store.create_node(conn, "claim", "claim target", author="alice")
    edge = store.create_edge(
        conn,
        src=a.id,
        dst=target.id,
        edge_type="composes",
        facet_binding=None,
        provenance="human",
    )

    result = store.split_node(
        conn,
        target.id,
        parts=[
            {"node_type": "claim", "body": "part one", "author": "alice"},
            {"node_type": "claim", "body": "part two", "author": "alice"},
        ],
    )

    assert set(result.keys()) == {target.id}
    successors = result[target.id]
    assert len(successors) == 2
    assert all(conn.execute("SELECT 1 FROM nodes WHERE id=?", (s,)).fetchone() for s in successors)

    redirect_row = conn.execute(
        "SELECT successors FROM redirects WHERE old_id=?", (target.id,)
    ).fetchone()
    assert json.loads(redirect_row[0]) == successors

    status_row = conn.execute("SELECT status FROM nodes WHERE id=?", (target.id,)).fetchone()
    assert status_row == ("tombstone",)

    # zero dangling live references to the retired node
    assert _live_edges_touching(conn, target.id) == []
    new_edge_row = conn.execute(
        "SELECT dst, retracted_at FROM edges WHERE id=?", (edge.id,)
    ).fetchone()
    assert new_edge_row == (successors[0], None)


def test_split_node_requires_nonempty_parts(tmp_path):
    conn = _fresh_conn(tmp_path)
    target = store.create_node(conn, "claim", "claim target", author="alice")
    with pytest.raises(ValueError):
        store.split_node(conn, target.id, parts=[])


def test_split_node_not_found_raises(tmp_path):
    conn = _fresh_conn(tmp_path)
    with pytest.raises(store.NodeNotFoundError):
        store.split_node(
            conn, "tm-nonexist", parts=[{"node_type": "claim", "body": "x", "author": "a"}]
        )


def test_merge_nodes_tombstones_retired_and_reassigns_inbound(tmp_path):
    conn = _fresh_conn(tmp_path)
    a = store.create_node(conn, "claim", "claim a", author="alice")
    survivor = store.create_node(conn, "claim", "claim survivor", author="alice")
    retired = store.create_node(conn, "claim", "claim retired", author="alice")
    edge = store.create_edge(
        conn,
        src=a.id,
        dst=retired.id,
        edge_type="composes",
        facet_binding=None,
        provenance="human",
    )

    result = store.merge_nodes(conn, [survivor.id, retired.id])

    assert result == {retired.id: [survivor.id]}
    redirect_row = conn.execute(
        "SELECT successors FROM redirects WHERE old_id=?", (retired.id,)
    ).fetchone()
    assert json.loads(redirect_row[0]) == [survivor.id]

    status_row = conn.execute("SELECT status FROM nodes WHERE id=?", (retired.id,)).fetchone()
    assert status_row == ("tombstone",)
    survivor_status = conn.execute("SELECT status FROM nodes WHERE id=?", (survivor.id,)).fetchone()
    assert survivor_status == ("live",)

    assert _live_edges_touching(conn, retired.id) == []
    new_edge_row = conn.execute(
        "SELECT dst, retracted_at FROM edges WHERE id=?", (edge.id,)
    ).fetchone()
    assert new_edge_row == (survivor.id, None)

    assert (
        conn.execute("SELECT maturity FROM nodes WHERE id=?", (survivor.id,)).fetchone()[0] == "S1"
    )


def test_merge_nodes_requires_at_least_two_ids(tmp_path):
    conn = _fresh_conn(tmp_path)
    a = store.create_node(conn, "claim", "claim a", author="alice")
    with pytest.raises(ValueError):
        store.merge_nodes(conn, [a.id])


def test_merge_nodes_not_found_raises_and_writes_nothing(tmp_path):
    conn = _fresh_conn(tmp_path)
    a = store.create_node(conn, "claim", "claim a", author="alice")
    with pytest.raises(store.NodeNotFoundError):
        store.merge_nodes(conn, [a.id, "tm-nonexist"])
    # nothing tombstoned
    row = conn.execute("SELECT status FROM nodes WHERE id=?", (a.id,)).fetchone()
    assert row == ("live",)


# --- commits.py helpers -------------------------------------------------------


def _facet(facet_id="fct00001", name="n", span="s", version=1) -> Facet:
    return Facet(facet_id=facet_id, name=name, span=span, version=version)


def test_facets_touched_added_and_removed():
    old = [_facet(facet_id="fct00001", name="a")]
    new = [_facet(facet_id="fct00002", name="b")]
    assert facets_touched(old, new) == ["fct00001", "fct00002"]


def test_facets_touched_renamed():
    old = [_facet(facet_id="fct00001", name="a", version=1)]
    new = [_facet(facet_id="fct00001", name="b", version=1)]
    assert facets_touched(old, new) == ["fct00001"]


def test_facets_touched_version_bumped():
    old = [_facet(facet_id="fct00001", name="a", version=1)]
    new = [_facet(facet_id="fct00001", name="a", version=2)]
    assert facets_touched(old, new) == ["fct00001"]


def test_facets_touched_span_only_change_not_touched():
    old = [_facet(facet_id="fct00001", name="a", span="x", version=1)]
    new = [_facet(facet_id="fct00001", name="a", span="y", version=1)]
    assert facets_touched(old, new) == []


def test_facets_touched_unchanged_empty():
    old = [_facet(facet_id="fct00001", name="a", version=1)]
    new = [_facet(facet_id="fct00001", name="a", version=1)]
    assert facets_touched(old, new) == []


def test_default_change_class_major_on_removed():
    old = [_facet(facet_id="fct00001")]
    new: list[Facet] = []
    assert default_change_class(old, new) == "major"


def test_default_change_class_major_on_renamed():
    old = [_facet(facet_id="fct00001", name="a", version=1)]
    new = [_facet(facet_id="fct00001", name="b", version=1)]
    assert default_change_class(old, new) == "major"


def test_default_change_class_major_on_version_bump():
    old = [_facet(facet_id="fct00001", name="a", version=1)]
    new = [_facet(facet_id="fct00001", name="a", version=2)]
    assert default_change_class(old, new) == "major"


def test_default_change_class_patch_on_addition_only():
    old: list[Facet] = []
    new = [_facet(facet_id="fct00001")]
    assert default_change_class(old, new) == "patch"


def test_default_change_class_patch_on_no_change():
    old = [_facet(facet_id="fct00001", name="a", version=1)]
    new = [_facet(facet_id="fct00001", name="a", version=1)]
    assert default_change_class(old, new) == "patch"
