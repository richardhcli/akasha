"""Split/merge inbound-reassignment queue + zero-dangling property (task T7.6).

Spec §9 story 4 ("split/merge zero dangling") and §4.11: after any
composed sequence of split / merge / reassignment-resolution, every live
edge's destination, followed transitively through ``redirects``, lands on
a live node — never a tombstone, never missing.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from akasha.kernel import store
from akasha.tms import review


def _fresh_conn(tmp_dir: Path) -> sqlite3.Connection:
    conn = store.connect(tmp_dir / "store.db")
    store.run_migrations(conn)
    return conn


def _live_node_ids(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT id FROM nodes WHERE status='live'").fetchall()
    return [r[0] for r in rows]


def _assert_zero_transitively_dangling(conn: sqlite3.Connection) -> None:
    """Invariant: every live edge's dst resolves transitively to a live node."""
    rows = conn.execute(
        "SELECT id, dst FROM edges WHERE retracted_at IS NULL"
    ).fetchall()
    for edge_id, dst in rows:
        terminal = store.resolve_redirect_chain(conn, dst)
        status_row = conn.execute(
            "SELECT status FROM nodes WHERE id=?", (terminal,)
        ).fetchone()
        assert status_row is not None, (
            f"live edge {edge_id}: transitive resolve of dst {dst!r} "
            f"landed on missing node {terminal!r}"
        )
        assert status_row[0] == "live", (
            f"live edge {edge_id}: transitive resolve of dst {dst!r} "
            f"landed on {terminal!r} with status={status_row[0]!r} "
            f"(expected 'live')"
        )


def _part(body: str) -> dict[str, str]:
    return {"node_type": "claim", "body": body, "author": "hyp"}


def _make_edge(conn: sqlite3.Connection, src: str, dst: str) -> str:
    edge = store.create_edge(
        conn,
        src=src,
        dst=dst,
        edge_type="composes",
        facet_binding=None,
        provenance="human",
    )
    return edge.id


# ---------------------------------------------------------------------------
# (A) Hypothesis property: composed split/merge/resolve sequences
# ---------------------------------------------------------------------------


@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(data=st.data())
def test_split_merge_resolve_sequences_leave_zero_transitively_dangling(
    data: st.DataObject,
) -> None:
    """§9 story 4: CHAINS of split/merge/resolve leave zero dangling edges.

    Strategy builds a small seed graph, then applies a random sequence of
    ops drawn from {split a live node, merge two live nodes, resolve a
    pending reassignment}. Later ops may target nodes *produced* by earlier
    splits — a one-shot single-op-on-fresh-graph strategy would be vacuous.
    """
    with tempfile.TemporaryDirectory() as tmp:
        conn = _fresh_conn(Path(tmp))
        # sqlite3 keeps the db file handle open for the life of `conn`; on
        # Windows (unlike POSIX) you cannot delete/rmtree a file that's still
        # open, so TemporaryDirectory's own cleanup raises PermissionError
        # unless the connection is explicitly closed first.
        try:
            # Seed graph: several live nodes with a few inbound edges so splits
            # have something to reassign / enqueue.
            seed_nodes = [
                store.create_node(conn, "claim", f"seed-{i}", author="hyp").id
                for i in range(5)
            ]
            for i in range(3):
                _make_edge(conn, seed_nodes[i], seed_nodes[i + 1])
            _assert_zero_transitively_dangling(conn)

            n_ops = data.draw(st.integers(min_value=3, max_value=12), label="n_ops")
            for _ in range(n_ops):
                live = _live_node_ids(conn)
                open_reassign = store.find_open_reviews(conn, cause_kind="reassignment")

                choices: list[str] = []
                if len(live) >= 1:
                    choices.append("split")
                if len(live) >= 2:
                    choices.append("merge")
                if open_reassign:
                    choices.append("resolve")
                if not choices:
                    break

                op = data.draw(st.sampled_from(choices), label="op")

                if op == "split":
                    target = data.draw(st.sampled_from(live), label="split_target")
                    n_parts = data.draw(st.integers(min_value=2, max_value=3), label="n_parts")
                    store.split_node(
                        conn,
                        target,
                        parts=[_part(f"split-{target}-{j}") for j in range(n_parts)],
                    )

                elif op == "merge":
                    pair = data.draw(
                        st.lists(
                            st.sampled_from(live),
                            min_size=2,
                            max_size=2,
                            unique=True,
                        ),
                        label="merge_pair",
                    )
                    store.merge_nodes(conn, pair)

                elif op == "resolve":
                    item = data.draw(st.sampled_from(open_reassign), label="resolve_item")
                    envelope = json.loads(item["cause_ref"])
                    successors = envelope["successors"]
                    chosen = data.draw(st.sampled_from(successors), label="chosen_successor")
                    review.resolve_reassignment(conn, item["id"], chosen)

                _assert_zero_transitively_dangling(conn)
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# (B) Explicit unit tests for queue behavior
# ---------------------------------------------------------------------------


def test_split_enqueues_exactly_k_reassignment_reviews(tmp_path: Path) -> None:
    """(b1) Split of a node with K live inbound edges enqueues exactly K reviews."""
    conn = _fresh_conn(tmp_path)
    target = store.create_node(conn, "claim", "target", author="t")
    k = 3
    for i in range(k):
        src = store.create_node(conn, "claim", f"src-{i}", author="t")
        _make_edge(conn, src.id, target.id)

    store.split_node(
        conn,
        target.id,
        parts=[_part("a"), _part("b")],
    )

    reviews = store.find_open_reviews(conn, cause_kind="reassignment")
    assert len(reviews) == k


def test_merge_enqueues_no_reassignment_reviews(tmp_path: Path) -> None:
    """(b2) Merge enqueues zero reassignment reviews even with inbound edges."""
    conn = _fresh_conn(tmp_path)
    survivor = store.create_node(conn, "claim", "survivor", author="t")
    retired = store.create_node(conn, "claim", "retired", author="t")
    src = store.create_node(conn, "claim", "src", author="t")
    _make_edge(conn, src.id, retired.id)
    _make_edge(conn, src.id, survivor.id)

    store.merge_nodes(conn, [survivor.id, retired.id])

    reviews = store.find_open_reviews(conn, cause_kind="reassignment")
    assert len(reviews) == 0


def _multihop_reassignment_setup(
    conn: sqlite3.Connection,
) -> tuple[str, str, str, str]:
    """Build X→{S1,S2}, then S1→{S1a,S1b}; return (review_id, edge_id, S1, S1a).

    Resolves nothing — caller chooses when to call ``resolve_reassignment``.
    """
    src = store.create_node(conn, "claim", "src", author="t")
    x = store.create_node(conn, "claim", "X", author="t")
    edge_id = _make_edge(conn, src.id, x.id)

    split_x = store.split_node(
        conn, x.id, parts=[_part("S1"), _part("S2")]
    )
    s1, s2 = split_x[x.id]

    x_reviews = store.find_open_reviews(conn, cause_kind="reassignment")
    assert len(x_reviews) == 1
    x_review = x_reviews[0]
    envelope = json.loads(x_review["cause_ref"])
    assert envelope["successors"] == [s1, s2]
    assert envelope["edge_id"] == edge_id

    split_s1 = store.split_node(
        conn, s1, parts=[_part("S1a"), _part("S1b")]
    )
    s1a = split_s1[s1][0]

    return x_review["id"], edge_id, s1, s1a


def test_resolve_reassignment_follows_multihop_redirect_chain(tmp_path: Path) -> None:
    """(b3) Choosing S1 after S1 was re-split lands the edge on live S1a, not S1."""
    conn = _fresh_conn(tmp_path)
    review_id, edge_id, s1, s1a = _multihop_reassignment_setup(conn)

    expected = store.resolve_redirect_chain(conn, s1)
    assert expected == s1a
    s1_status = conn.execute("SELECT status FROM nodes WHERE id=?", (s1,)).fetchone()
    assert s1_status == ("tombstone",)

    review.resolve_reassignment(conn, review_id, s1)

    dst = conn.execute(
        "SELECT dst FROM edges WHERE id=? AND retracted_at IS NULL", (edge_id,)
    ).fetchone()[0]
    assert dst == s1a
    assert dst != s1


def test_resolve_reassignment_preserves_zero_dangling_invariant(tmp_path: Path) -> None:
    """(b4) After the same multi-hop resolution, the whole-graph invariant holds."""
    conn = _fresh_conn(tmp_path)
    review_id, _edge_id, s1, _s1a = _multihop_reassignment_setup(conn)
    review.resolve_reassignment(conn, review_id, s1)
    _assert_zero_transitively_dangling(conn)
