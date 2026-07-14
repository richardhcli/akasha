"""Conflict-branching integration test (task T5.5, spec §4.8 conflict semantics, §6.2 E12).

Drives the FULL reconcile pipeline (task T5.4's ``Reconciler``) end-to-end
against a real (in-memory) sqlite store and a real temp-directory file,
through its default ``conflict_handler`` (``reconcile.conflict_branch_handler``,
task T5.5). E12: hub+vault concurrent edit of the SAME node must keep BOTH
versions on the node's commit DAG (no data loss) and enqueue exactly one
``cause_kind="conflict"`` review.

Fixture helpers below are copied verbatim from
``tests/unit/sync/test_reconcile.py`` (that module's own docstring notes
they exist precisely so sibling test files can reuse this pattern) rather
than imported, since ``tests/`` is not a package (no ``__init__.py`` files
anywhere under it, confirmed via a repo-wide search before choosing this
approach).
"""

from __future__ import annotations

import json
import sqlite3

from akasha.contract.parser import parse
from akasha.contract.render import render
from akasha.kernel import ids, store
from akasha.kernel.canonical import canonicalize_text
from akasha.kernel.ids import contract_anchor
from akasha.sync import base_store
from akasha.sync.origin import OriginTracker
from akasha.sync.reconcile import Reconciler


def _conn() -> sqlite3.Connection:
    conn = store.connect(":memory:", check_same_thread=False)
    store.run_migrations(conn)
    return conn


def _seed_node(
    conn: sqlite3.Connection,
    node_id: str,
    node_type: str,
    body: str,
    task_state: str | None = None,
) -> None:
    """Test-only fixture seeding: insert a node under a CHOSEN id (genesis C0)."""
    now = store._now()
    canonical_body = canonicalize_text(body)
    content = store._node_content(canonical_body, [], task_state)
    with conn:
        obj_hash = store._insert_object(conn, content, now)
        conn.execute(
            "INSERT INTO nodes (id, node_type, head_hash, maturity, status, vetted, "
            "created_at, updated_at) VALUES (?, ?, ?, 'S0', 'live', 0, ?, ?)",
            (node_id, node_type, obj_hash, now, now),
        )
        conn.execute("INSERT INTO nodes_fts (id, body) VALUES (?, ?)", (node_id, canonical_body))
        store._insert_commit(
            conn,
            node_id,
            parents=[],
            object_hash_=obj_hash,
            change_class="major",
            facets_touched=[],
            author="test",
            message="",
            now=now,
        )


def _managed(body: str) -> str:
    return canonicalize_text(f"---\ntm: 1\n---\n{body}")


def _register_root(conn: sqlite3.Connection, root_path) -> str:
    return store.register_sync_root(conn, "vault", str(root_path))["id"]


def test_e12_conflict_branches_both_versions_and_enqueues_one_review(tmp_path):
    conn = _conn()
    root_id = _register_root(conn, tmp_path)
    x = ids.mint()
    _seed_node(conn, x, "claim", "line one")  # genesis C0

    base_text = render(parse(_managed(f"line one {contract_anchor(x)}\n")))
    path = tmp_path / "note.md"
    base_store.put(conn, root_id, str(path), base_text)

    # Concurrent hub edit -> C1, becomes the new head.
    store.commit_node(
        conn, x, new_body="hub changed", change_class="patch", facets_touched=[], author="human"
    )
    c1_hash = store.history(conn, x)[-1]["hash"]
    # Divergent vault edit of the SAME node.
    vault_text = _managed(f"vault changed {contract_anchor(x)}\n")
    path.write_text(vault_text, encoding="utf-8")

    origin = OriginTracker()
    reconciler = Reconciler(conn, origin)
    reconciler.on_change(str(path))

    # --- (a) both versions survive on the commit DAG; head is unmoved -------
    hist = store.history(conn, x)
    assert len(hist) == 3
    assert hist[1]["hash"] == c1_hash
    c2_hash = hist[2]["hash"]
    assert hist[2]["parents"] == [c1_hash]
    assert hist[2]["message"].startswith("conflict-branch:")
    assert hist[2]["author"] == "sync"

    assert store.get_node(conn, x).body == "hub changed\n"  # hub head unmoved
    branch_snapshot = store.get_commit_snapshot(conn, c2_hash)
    assert branch_snapshot["body"] == "vault changed\n"

    # --- (b) exactly one open conflict review, cause_ref carries the trio ---
    rows = conn.execute(
        "SELECT node_id, cause_kind, cause_ref FROM review_queue WHERE cause_kind='conflict'"
    ).fetchall()
    assert len(rows) == 1
    node_id, cause_kind, cause_ref = rows[0]
    assert node_id == x
    payload = json.loads(cause_ref)
    assert payload["vault_text"] == "vault changed"
    assert payload["base_text"] == "line one"
    assert payload["branch_commit"] == c2_hash

    # --- (d) canonical write-back: hub wins the file, no data loss ----------
    final = path.read_text(encoding="utf-8")
    assert "hub changed" in final
    assert "vault changed" not in final
    assert base_store.get(conn, root_id, str(path)) == final

    # --- (c1) a plain second on_change, already-converged, is quiet ---------
    reconciler.on_change(str(path))
    assert len(store.history(conn, x)) == 3
    assert (
        conn.execute("SELECT COUNT(*) FROM review_queue WHERE cause_kind='conflict'").fetchone()[0]
        == 1
    )

    # --- (c2) crash-replay idempotence: restore base + rewrite same vault ---
    # text -> re-running on_change reproduces the SAME conflict verdict
    # without a duplicate branch commit or a duplicate review row.
    base_store.put(conn, root_id, str(path), base_text)
    path.write_text(vault_text, encoding="utf-8")
    reconciler.on_change(str(path))

    hist_after_replay = store.history(conn, x)
    assert len(hist_after_replay) == 3  # no new branch commit -- content-addressed dedup
    assert hist_after_replay[2]["hash"] == c2_hash
    assert (
        conn.execute("SELECT COUNT(*) FROM review_queue WHERE cause_kind='conflict'").fetchone()[0]
        == 1
    )
    assert store.get_node(conn, x).body == "hub changed\n"  # still unmoved

    final_replay = path.read_text(encoding="utf-8")
    assert "hub changed" in final_replay
    assert "vault changed" not in final_replay
    assert base_store.get(conn, root_id, str(path)) == final_replay

    # --- pins the commit_node parent-fix: a NEW mainline commit must parent
    # on C1 (the true head commit), never on the branch C2, or the branch
    # would silently collapse into the mainline.
    store.commit_node(
        conn, x, new_body="hub again", change_class="patch", facets_touched=[], author="human"
    )
    hist_final = store.history(conn, x)
    assert len(hist_final) == 4
    assert hist_final[3]["parents"] == [c1_hash]
