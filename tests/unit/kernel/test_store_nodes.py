"""Node create/read + commit DAG tests (task T1.3, spec §4.5, §4.4, §4.1).

Covers: create->get round trip returns canonical body, commit moves head
and preserves parent, get_node(as_of=<past>) returns the older object,
objects table rows are never mutated (append-only).
"""

import time

import pytest

from akasha.kernel import store
from akasha.kernel.model import Facet


def _fresh_conn(tmp_path):
    conn = store.connect(tmp_path / "store_nodes.db")
    store.run_migrations(conn)
    return conn


def test_create_node_round_trip_returns_canonical_body(tmp_path):
    conn = _fresh_conn(tmp_path)
    created = store.create_node(conn, "claim", "hello world  \n\n\n", author="alice")
    fetched = store.get_node(conn, created.id)

    assert fetched.id == created.id
    assert fetched.body == "hello world\n"  # trailing whitespace/blank lines canonicalized
    assert fetched.node_type == "claim"
    assert fetched.status == "live"
    assert fetched.vetted is False


def test_create_node_mints_valid_id_and_persists_genesis_commit(tmp_path):
    conn = _fresh_conn(tmp_path)
    node = store.create_node(conn, "entity", "some entity", author="alice")

    row = conn.execute("SELECT id FROM nodes WHERE id=?", (node.id,)).fetchone()
    assert row is not None

    hist = store.history(conn, node.id)
    assert len(hist) == 1
    assert hist[0]["parents"] == []
    assert hist[0]["change_class"] == "major"
    assert hist[0]["author"] == "alice"


def test_create_node_persists_facets(tmp_path):
    conn = _fresh_conn(tmp_path)
    facet = Facet(facet_id="bbbbbbbb", name="def", span="the span", version=0)
    node = store.create_node(conn, "definition", "a definition", facets=[facet], author="alice")

    fetched = store.get_node(conn, node.id)
    assert len(fetched.facets) == 1
    assert fetched.facets[0].facet_id == "bbbbbbbb"
    assert fetched.facets[0].name == "def"


def test_create_node_rejects_invalid_node_type(tmp_path):
    conn = _fresh_conn(tmp_path)
    with pytest.raises(ValueError):
        store.create_node(conn, "not-a-real-type", "body", author="alice")


def test_commit_node_moves_head_and_preserves_parent(tmp_path):
    conn = _fresh_conn(tmp_path)
    node = store.create_node(conn, "claim", "version one", author="alice")

    updated = store.commit_node(
        conn,
        node.id,
        new_body="version two",
        change_class="patch",
        facets_touched=[],
        author="bob",
    )
    assert updated.body == "version two\n"

    fetched = store.get_node(conn, node.id)
    assert fetched.body == "version two\n"

    hist = store.history(conn, node.id)
    assert len(hist) == 2
    genesis, second = hist
    assert second["parents"] == [genesis["hash"]]
    assert second["author"] == "bob"
    assert second["change_class"] == "patch"
    # head_hash must point at the newest commit's object, not the genesis object
    assert second["object_hash"] != genesis["object_hash"]


def test_commit_node_without_new_body_keeps_current_body(tmp_path):
    conn = _fresh_conn(tmp_path)
    facet = Facet(facet_id="cccccccc", name="f", span="span", version=0)
    node = store.create_node(conn, "definition", "unchanged body", author="alice")

    updated = store.commit_node(
        conn,
        node.id,
        facets=[facet],
        change_class="minor",
        facets_touched=["cccccccc"],
        author="alice",
    )
    assert updated.body == "unchanged body\n"
    assert updated.facets[0].facet_id == "cccccccc"


def test_get_node_as_of_returns_older_object(tmp_path):
    conn = _fresh_conn(tmp_path)
    node = store.create_node(conn, "claim", "version one", author="alice")
    hist_before = store.history(conn, node.id)
    ts_after_genesis = hist_before[0]["ts"]

    time.sleep(0.001)
    store.commit_node(
        conn,
        node.id,
        new_body="version two",
        change_class="patch",
        facets_touched=[],
        author="alice",
    )

    as_of_node = store.get_node(conn, node.id, as_of=ts_after_genesis)
    assert as_of_node.body == "version one\n"

    current_node = store.get_node(conn, node.id)
    assert current_node.body == "version two\n"


def test_get_node_as_of_before_genesis_raises(tmp_path):
    conn = _fresh_conn(tmp_path)
    node = store.create_node(conn, "claim", "version one", author="alice")
    with pytest.raises(store.NodeNotFoundError):
        store.get_node(conn, node.id, as_of="1970-01-01T00:00:00.000000+00:00")


def test_get_node_unknown_id_raises(tmp_path):
    conn = _fresh_conn(tmp_path)
    with pytest.raises(store.NodeNotFoundError):
        store.get_node(conn, "zzzzzzzz")


def test_history_is_ordered_oldest_first(tmp_path):
    conn = _fresh_conn(tmp_path)
    node = store.create_node(conn, "claim", "v1", author="alice")
    store.commit_node(
        conn, node.id, new_body="v2", change_class="patch", facets_touched=[], author="alice"
    )
    store.commit_node(
        conn, node.id, new_body="v3", change_class="patch", facets_touched=[], author="alice"
    )

    hist = store.history(conn, node.id)
    assert len(hist) == 3
    assert [c["ts"] for c in hist] == sorted(c["ts"] for c in hist)


def test_objects_rows_are_never_mutated(tmp_path):
    conn = _fresh_conn(tmp_path)
    node = store.create_node(conn, "claim", "version one", author="alice")
    genesis_hash = store.history(conn, node.id)[0]["object_hash"]
    genesis_bytes_before = conn.execute(
        "SELECT bytes FROM objects WHERE hash=?", (genesis_hash,)
    ).fetchone()[0]

    store.commit_node(
        conn,
        node.id,
        new_body="version two",
        change_class="patch",
        facets_touched=[],
        author="alice",
    )

    genesis_bytes_after = conn.execute(
        "SELECT bytes FROM objects WHERE hash=?", (genesis_hash,)
    ).fetchone()[0]
    assert genesis_bytes_before == genesis_bytes_after

    total_objects = conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0]
    assert total_objects == 2


def test_create_node_id_collision_retries_then_succeeds(tmp_path, monkeypatch):
    conn = _fresh_conn(tmp_path)
    fixed_id = "aaaaaaaa"
    from akasha.kernel import ids as ids_module

    real_checksum = ids_module.checksum(fixed_id[: ids_module.CORE_LEN])
    fixed_id = fixed_id[: ids_module.CORE_LEN] + real_checksum

    calls = {"n": 0}
    real_mint = ids_module.mint

    def fake_mint():
        calls["n"] += 1
        if calls["n"] == 1:
            return fixed_id
        return real_mint()

    monkeypatch.setattr(store.ids, "mint", fake_mint)

    first = store.create_node(conn, "claim", "first", author="alice")
    assert first.id == fixed_id

    calls["n"] = 0
    second = store.create_node(conn, "claim", "second", author="alice")
    assert second.id != fixed_id
    assert calls["n"] == 2


def test_create_node_id_collision_bound_raises(tmp_path, monkeypatch):
    conn = _fresh_conn(tmp_path)
    always_same = "aaaaaaaa"
    from akasha.kernel import ids as ids_module

    always_same = always_same[: ids_module.CORE_LEN] + ids_module.checksum(
        always_same[: ids_module.CORE_LEN]
    )
    store.create_node(conn, "claim", "seed", author="alice")
    # Force nodes.id to collide by directly inserting the fixed id first.
    conn.execute(
        "INSERT INTO nodes (id, node_type, head_hash, created_at, updated_at) "
        "SELECT ?, node_type, head_hash, created_at, updated_at FROM nodes LIMIT 1",
        (always_same,),
    )
    conn.commit()

    monkeypatch.setattr(store.ids, "mint", lambda: always_same)
    with pytest.raises(store.IdMintError):
        store.create_node(conn, "claim", "body", author="alice")
