"""``store.get_projection_bulk``: the light read a projection needs; equal to ``get_nodes_bulk``."""

from __future__ import annotations

from akasha.kernel import store


def _conn():
    conn = store.connect(":memory:", check_same_thread=False)
    store.run_migrations(conn)
    return conn


def test_projection_bulk_matches_get_nodes_bulk() -> None:
    conn = _conn()
    a = store.create_node(conn, "claim", "multi\nline body")
    b = store.create_node(conn, "task", "buy milk", task_state="open")
    store.commit_node(
        conn, b.id, task_state="done", change_class="patch", facets_touched=[], author="human"
    )
    heavy = store.get_nodes_bulk(conn, [a.id, b.id, "zzzzzzzz"])
    light = store.get_projection_bulk(conn, [a.id, b.id, "zzzzzzzz", a.id])
    assert set(light) == set(heavy) == {a.id, b.id}  # unknown ids omitted, duplicates fine
    for node_id, node in heavy.items():
        assert light[node_id] == (node.status, node.body, node.task_state)


def test_projection_bulk_handles_more_than_one_chunk_and_empty_input() -> None:
    conn = _conn()
    ids_ = [store.create_node(conn, "claim", f"n{i}").id for i in range(1200)]
    got = store.get_projection_bulk(conn, ids_)
    assert len(got) == 1200 and got[ids_[777]][1] == "n777\n"
    assert store.get_projection_bulk(conn, []) == {}
