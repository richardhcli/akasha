"""S0 garbage-collection job tests (task T1.7, spec §4.4, §4.5).

Binding invariant under test: GC never removes a referenced object. An
"orphaned S0 object" (per this task's DoD) is an object left behind by
``delete_node``'s S0 hard-delete path (spec §4.5: hard-delete removes
``nodes``/``commits``/incident-``edges`` rows but intentionally leaves the
``objects`` row for later GC). Covers: an orphaned object is collected;
every object referenced by an S1+ node (via its commits/head) or a base
snapshot (``sync_files.base_hash``) survives; a live S0 node's own head
object is never collected.
"""

from __future__ import annotations

from akasha.kernel import store
from akasha.kernel.canonical import canonical_json, object_hash


def _fresh_conn(tmp_path):
    conn = store.connect(tmp_path / "store_gc.db")
    store.run_migrations(conn)
    return conn


def test_gc_removes_orphaned_object_left_by_s0_hard_delete(tmp_path):
    conn = _fresh_conn(tmp_path)
    a = store.create_node(conn, "claim", "claim a", author="alice")
    orphan_hash = conn.execute("SELECT head_hash FROM nodes WHERE id=?", (a.id,)).fetchone()[0]
    # object exists pre-GC
    assert conn.execute("SELECT 1 FROM objects WHERE hash=?", (orphan_hash,)).fetchone()

    # S0 hard-delete removes node/commits/edges but (by design) leaves the object row.
    store.delete_node(conn, a.id)
    assert conn.execute("SELECT 1 FROM objects WHERE hash=?", (orphan_hash,)).fetchone()

    deleted = store.gc_objects(conn)

    assert orphan_hash in deleted
    assert conn.execute("SELECT 1 FROM objects WHERE hash=?", (orphan_hash,)).fetchone() is None


def test_gc_never_removes_object_referenced_by_s1_plus_node(tmp_path):
    conn = _fresh_conn(tmp_path)
    src = store.create_node(conn, "claim", "claim src", author="alice")
    dst = store.create_node(conn, "claim", "claim dst", author="alice")
    store.create_edge(
        conn, src=src.id, dst=dst.id, edge_type="composes", facet_binding=None, provenance="human"
    )
    assert conn.execute("SELECT maturity FROM nodes WHERE id=?", (dst.id,)).fetchone()[0] == "S1"
    dst_head = conn.execute("SELECT head_hash FROM nodes WHERE id=?", (dst.id,)).fetchone()[0]
    src_head = conn.execute("SELECT head_hash FROM nodes WHERE id=?", (src.id,)).fetchone()[0]

    # also create an orphan to make sure the GC actually collects something
    orphan = store.create_node(conn, "claim", "claim orphan", author="alice")
    orphan_head = conn.execute("SELECT head_hash FROM nodes WHERE id=?", (orphan.id,)).fetchone()[0]
    store.delete_node(conn, orphan.id)

    deleted = store.gc_objects(conn)

    assert orphan_head in deleted
    assert dst_head not in deleted
    assert src_head not in deleted
    assert conn.execute("SELECT 1 FROM objects WHERE hash=?", (dst_head,)).fetchone() is not None
    assert conn.execute("SELECT 1 FROM objects WHERE hash=?", (src_head,)).fetchone() is not None


def test_gc_never_removes_base_snapshot_object(tmp_path):
    conn = _fresh_conn(tmp_path)
    # A base snapshot object referenced only via sync_files.base_hash, not
    # by any node/commit -- must still survive GC.
    content = {"body": "base snapshot content", "facets": [], "task_state": None}
    obj_bytes = canonical_json(content)
    base_hash = object_hash(obj_bytes)
    now = "2026-01-01T00:00:00.000000+00:00"
    with conn:
        conn.execute(
            "INSERT INTO objects (hash, kind, bytes, created_at) VALUES (?, ?, ?, ?)",
            (base_hash, "node_snapshot", obj_bytes, now),
        )
        conn.execute(
            "INSERT INTO sync_roots (id, name, root_path, created_at) VALUES (?, ?, ?, ?)",
            ("default", "default", "/tmp/default", now),
        )
        conn.execute(
            "INSERT INTO sync_files "
            "(path, sync_root_id, base_hash, contract_version, last_synced_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("notes/foo.md", "default", base_hash, 1, now),
        )

    deleted = store.gc_objects(conn)

    assert base_hash not in deleted
    assert conn.execute("SELECT 1 FROM objects WHERE hash=?", (base_hash,)).fetchone() is not None


def test_gc_never_collects_live_s0_nodes_head_object(tmp_path):
    conn = _fresh_conn(tmp_path)
    a = store.create_node(conn, "claim", "claim a", author="alice")
    assert conn.execute("SELECT maturity FROM nodes WHERE id=?", (a.id,)).fetchone()[0] == "S0"
    head = conn.execute("SELECT head_hash FROM nodes WHERE id=?", (a.id,)).fetchone()[0]

    deleted = store.gc_objects(conn)

    assert head not in deleted
    assert conn.execute("SELECT 1 FROM objects WHERE hash=?", (head,)).fetchone() is not None
    # node itself is untouched
    assert conn.execute("SELECT 1 FROM nodes WHERE id=?", (a.id,)).fetchone() is not None


def test_gc_returns_sorted_list_of_deleted_hashes(tmp_path):
    conn = _fresh_conn(tmp_path)
    a = store.create_node(conn, "claim", "claim a", author="alice")
    b = store.create_node(conn, "claim", "claim b", author="alice")
    hash_a = conn.execute("SELECT head_hash FROM nodes WHERE id=?", (a.id,)).fetchone()[0]
    hash_b = conn.execute("SELECT head_hash FROM nodes WHERE id=?", (b.id,)).fetchone()[0]
    store.delete_node(conn, a.id)
    store.delete_node(conn, b.id)

    deleted = store.gc_objects(conn)

    assert deleted == sorted({hash_a, hash_b})


def test_gc_is_idempotent_when_nothing_orphaned(tmp_path):
    conn = _fresh_conn(tmp_path)
    store.create_node(conn, "claim", "claim a", author="alice")

    deleted = store.gc_objects(conn)

    assert deleted == []
