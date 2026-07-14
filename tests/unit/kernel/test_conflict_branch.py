"""``store.record_conflict_branch`` unit tests (task T5.5, spec §4.8).

Isolated from the reconcile pipeline: exercises the store-level primitive
directly against a real (file-backed) sqlite connection. Covers the three
invariants the build-plan task calls out explicitly: the node's head is
never touched, a repeat call with identical content dedups to the same
commit hash (no duplicate branch), and the branch's object survives
``gc_objects`` (it is reachable via ``commits.object_hash`` even though it
is never ``nodes.head_hash``).
"""

from __future__ import annotations

from akasha.kernel import store


def _fresh_conn(tmp_path):
    conn = store.connect(tmp_path / "conflict_branch.db")
    store.run_migrations(conn)
    return conn


def test_record_conflict_branch_leaves_head_untouched(tmp_path):
    conn = _fresh_conn(tmp_path)
    node = store.create_node(conn, "claim", "original text", author="alice")
    before = conn.execute(
        "SELECT head_hash, updated_at FROM nodes WHERE id=?", (node.id,)
    ).fetchone()

    branch_commit = store.record_conflict_branch(
        conn, node.id, "branch text", message="conflict-branch: note.md"
    )

    after = conn.execute(
        "SELECT head_hash, updated_at FROM nodes WHERE id=?", (node.id,)
    ).fetchone()
    assert after == before
    assert store.get_node(conn, node.id).body == "original text\n"

    # The branch commit is a real, separate DAG entry, parented on genesis,
    # never reflected by get_node's head resolution.
    hist = store.history(conn, node.id)
    assert len(hist) == 2
    assert hist[1]["hash"] == branch_commit
    assert hist[1]["parents"] == [hist[0]["hash"]]
    assert hist[1]["message"] == "conflict-branch: note.md"
    assert hist[1]["author"] == "sync"
    assert hist[1]["change_class"] == "patch"
    assert hist[1]["facets_touched"] == []

    snapshot = store.get_commit_snapshot(conn, branch_commit)
    assert snapshot["body"] == "branch text\n"


def test_record_conflict_branch_double_call_dedups_to_same_hash(tmp_path):
    conn = _fresh_conn(tmp_path)
    node = store.create_node(conn, "claim", "original text", author="alice")

    first = store.record_conflict_branch(conn, node.id, "branch text")
    second = store.record_conflict_branch(conn, node.id, "branch text")

    assert first == second
    hist = store.history(conn, node.id)
    # Genesis + exactly one branch commit -- the second call inserted nothing.
    assert len(hist) == 2


def test_record_conflict_branch_task_state_preserves_by_default(tmp_path):
    conn = _fresh_conn(tmp_path)
    node = store.create_node(conn, "task", "do the thing", task_state="open", author="alice")

    branch_commit = store.record_conflict_branch(conn, node.id, "do the other thing")
    snapshot = store.get_commit_snapshot(conn, branch_commit)
    assert snapshot["task_state"] == "open"

    explicit = store.record_conflict_branch(conn, node.id, "do the third thing", task_state="done")
    snapshot2 = store.get_commit_snapshot(conn, explicit)
    assert snapshot2["task_state"] == "done"


def test_record_conflict_branch_unknown_node_raises(tmp_path):
    conn = _fresh_conn(tmp_path)
    try:
        store.record_conflict_branch(conn, "zzzzzzzz", "text")
    except store.NodeNotFoundError:
        pass
    else:
        raise AssertionError("expected NodeNotFoundError")


def test_gc_objects_keeps_conflict_branch_object(tmp_path):
    conn = _fresh_conn(tmp_path)
    node = store.create_node(conn, "claim", "original text", author="alice")
    branch_commit = store.record_conflict_branch(conn, node.id, "branch text")

    branch_object_hash = conn.execute(
        "SELECT object_hash FROM commits WHERE hash=?", (branch_commit,)
    ).fetchone()[0]

    deleted = store.gc_objects(conn)
    assert branch_object_hash not in deleted
    surviving = conn.execute(
        "SELECT 1 FROM objects WHERE hash=?", (branch_object_hash,)
    ).fetchone()
    assert surviving is not None
