"""Store property suite (task T1.8, spec §4.5, §6.1).

Drives the real ``kernel/store`` API against a freshly-migrated sqlite DB
through random *valid* sequences of ``create_node``/``commit_node``/
``create_edge``/``retract_edge`` operations (each drawn interactively via
``hypothesis.strategies.data()`` so every operation only ever references
node/edge ids the sequence itself has already created — no dangling-by-
construction noise) and asserts, after every operation, the two structural
invariants from spec §4.5's property-suite line, plus (once per sequence)
as-of correctness and S0-GC safety:

1. **No dangling edges** — every live edge (``retracted_at IS NULL``) has
   both endpoints present in ``nodes``.
2. **Head always reachable** — every node's ``head_hash`` equals the
   ``object_hash`` of its most recent commit, and walking that commit's
   ``parents`` chain back always reaches a genesis commit (``parents ==
   []``) without a missing link.
3. **As-of correctness** — for a random node with >= 2 commits and a
   timestamp strictly between two consecutive commits, ``get_node(id,
   as_of=ts)`` returns the object of the older (most-recent-at-or-before)
   commit.
4. **S0-GC safety** — after ``gc_objects``, every object referenced by a
   surviving ``commits.object_hash``, ``nodes.head_hash``, or
   ``sync_files.base_hash`` is still present in ``objects``.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from typing import get_args

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from akasha.kernel import store
from akasha.kernel.model import JUSTIFICATION_EDGE_TYPES, EdgeType, NodeType

_NODE_TYPES = list(get_args(NodeType))
_EDGE_TYPES = list(get_args(EdgeType))
_CHANGE_CLASSES = ["patch", "minor", "major"]
_PROVENANCES = ["human", "agent_approved", "imported"]

# Keep bodies non-empty/non-whitespace-only so canonicalize_text never turns
# two distinct draws into an ambiguous empty document.
_body_strategy = st.text(min_size=1, max_size=40).filter(lambda s: s.strip() != "")


def _fresh_conn(tmp_dir: Path) -> sqlite3.Connection:
    conn = store.connect(tmp_dir / "store.db")
    store.run_migrations(conn)
    return conn


def _assert_no_dangling_edges(conn: sqlite3.Connection) -> None:
    """Invariant 1: every live edge's src/dst both exist in nodes."""
    node_ids = {r[0] for r in conn.execute("SELECT id FROM nodes").fetchall()}
    rows = conn.execute("SELECT id, src, dst FROM edges WHERE retracted_at IS NULL").fetchall()
    for edge_id, src, dst in rows:
        assert src in node_ids, f"edge {edge_id} has dangling src {src!r}"
        assert dst in node_ids, f"edge {edge_id} has dangling dst {dst!r}"


def _assert_heads_reachable(conn: sqlite3.Connection) -> None:
    """Invariant 2: every node's head_hash matches its latest commit, and
    that commit's parent chain reaches an unbroken genesis."""
    node_rows = conn.execute("SELECT id, head_hash FROM nodes").fetchall()
    for node_id, head_hash in node_rows:
        commits = store.history(conn, node_id)
        assert commits, f"node {node_id} has no commits at all"
        latest = commits[-1]
        assert latest["object_hash"] == head_hash, (
            f"node {node_id}: head_hash {head_hash!r} != latest commit's "
            f"object_hash {latest['object_hash']!r}"
        )

        by_hash = {c["hash"]: c for c in commits}
        current = latest
        seen_hashes: set[str] = set()
        while current["parents"]:
            assert len(current["parents"]) == 1, (
                f"node {node_id}: commit {current['hash']} has multiple parents "
                f"{current['parents']!r} (store never creates merge commits)"
            )
            parent_hash = current["parents"][0]
            assert parent_hash in by_hash, (
                f"node {node_id}: commit {current['hash']} references missing "
                f"parent {parent_hash!r} -- broken chain"
            )
            assert parent_hash not in seen_hashes, (
                f"node {node_id}: cycle detected in commit parent chain at {parent_hash!r}"
            )
            seen_hashes.add(parent_hash)
            current = by_hash[parent_hash]
        assert current["parents"] == [], (
            f"node {node_id}: commit chain did not terminate at a genesis commit"
        )


def _midpoint_ts(older_ts: str, newer_ts: str) -> str:
    """A timestamp string with older_ts <= result < newer_ts (spec ts<=as_of rule).

    ISO-8601 microsecond-precision instants compare lexically the same as
    chronologically (fixed width), so any datetime strictly within
    [older_ts, newer_ts) is a valid as-of probe that must resolve to the
    older commit.
    """
    dt_older = datetime.fromisoformat(older_ts)
    dt_newer = datetime.fromisoformat(newer_ts)
    mid = dt_older + (dt_newer - dt_older) / 2
    return mid.isoformat(timespec="microseconds")


def _assert_gc_safety(conn: sqlite3.Connection) -> None:
    """Invariant 4: gc_objects never removes an object still referenced."""
    commit_hashes = {
        r[0] for r in conn.execute("SELECT DISTINCT object_hash FROM commits").fetchall()
    }
    head_hashes = {r[0] for r in conn.execute("SELECT head_hash FROM nodes").fetchall()}
    base_hashes = {
        r[0]
        for r in conn.execute(
            "SELECT base_hash FROM sync_files WHERE base_hash IS NOT NULL"
        ).fetchall()
    }
    reachable = commit_hashes | head_hashes | base_hashes

    store.gc_objects(conn)

    remaining = {r[0] for r in conn.execute("SELECT hash FROM objects").fetchall()}
    missing = reachable - remaining
    assert not missing, f"gc_objects removed still-referenced object(s): {missing!r}"


@settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(data=st.data())
def test_store_invariants_over_random_operation_sequences(data: st.DataObject) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        conn = _fresh_conn(Path(tmp))
        live_node_ids: list[str] = []
        live_edge_ids: list[str] = []

        n_ops = data.draw(st.integers(min_value=1, max_value=15), label="n_ops")
        for _ in range(n_ops):
            choices = ["create_node"]
            if live_node_ids:
                choices += ["commit_node", "create_edge"]
            if live_edge_ids:
                choices += ["retract_edge"]
            op = data.draw(st.sampled_from(choices), label="op")

            if op == "create_node":
                node_type = data.draw(st.sampled_from(_NODE_TYPES), label="node_type")
                body = data.draw(_body_strategy, label="body")
                node = store.create_node(conn, node_type, body, author="hyp")
                live_node_ids.append(node.id)

            elif op == "commit_node":
                node_id = data.draw(st.sampled_from(live_node_ids), label="commit_target")
                new_body = data.draw(_body_strategy, label="new_body")
                change_class = data.draw(st.sampled_from(_CHANGE_CLASSES), label="change_class")
                store.commit_node(
                    conn,
                    node_id,
                    new_body=new_body,
                    change_class=change_class,
                    facets_touched=[],
                    author="hyp",
                )

            elif op == "create_edge":
                src = data.draw(st.sampled_from(live_node_ids), label="edge_src")
                dst = data.draw(st.sampled_from(live_node_ids), label="edge_dst")
                edge_type = data.draw(st.sampled_from(_EDGE_TYPES), label="edge_type")
                provenance = data.draw(st.sampled_from(_PROVENANCES), label="provenance")
                facet_binding = "*" if edge_type in JUSTIFICATION_EDGE_TYPES else None
                edge = store.create_edge(
                    conn,
                    src=src,
                    dst=dst,
                    edge_type=edge_type,
                    facet_binding=facet_binding,
                    provenance=provenance,
                )
                live_edge_ids.append(edge.id)

            elif op == "retract_edge":
                edge_id = data.draw(st.sampled_from(live_edge_ids), label="retract_target")
                store.retract_edge(conn, edge_id)
                live_edge_ids.remove(edge_id)

            # Invariants 1 & 2 must hold after every single operation, not
            # just at the end of the sequence.
            _assert_no_dangling_edges(conn)
            _assert_heads_reachable(conn)

        # Invariant 3: as-of correctness, checked once per sequence (if any
        # node accrued more than one commit).
        multi_commit_nodes = [nid for nid in live_node_ids if len(store.history(conn, nid)) >= 2]
        if multi_commit_nodes:
            node_id = data.draw(st.sampled_from(multi_commit_nodes), label="as_of_node")
            commits = store.history(conn, node_id)
            idx = data.draw(st.integers(min_value=0, max_value=len(commits) - 2), label="as_of_idx")
            older, newer = commits[idx], commits[idx + 1]
            if older["ts"] < newer["ts"]:
                as_of_ts = _midpoint_ts(older["ts"], newer["ts"])
                result = store.get_node(conn, node_id, as_of=as_of_ts)
                expected_bytes = conn.execute(
                    "SELECT bytes FROM objects WHERE hash=?", (older["object_hash"],)
                ).fetchone()[0]
                expected_content = json.loads(expected_bytes)
                assert result.body == expected_content["body"], (
                    f"as-of query at {as_of_ts!r} (between commits {older['hash']!r} and "
                    f"{newer['hash']!r}) returned body {result.body!r}, expected "
                    f"{expected_content['body']!r} (the older commit's object)"
                )

        # Invariant 4: S0-GC safety, checked once at the end of the sequence.
        _assert_gc_safety(conn)
