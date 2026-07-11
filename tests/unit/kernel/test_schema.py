"""Schema acceptance tests for migrations/001_init.sql (task T1.1, spec §4.4).

Asserts that after running migrations, sqlite_master contains every
table/index/vtable named in §4.4 with matching column names. This is a
byte-exact acceptance check on the DDL, not a behavioral test of the store
API (which lands in later M1 tasks).
"""

from akasha.kernel import store

EXPECTED_TABLE_COLUMNS = {
    "objects": ["hash", "kind", "bytes", "created_at"],
    "nodes": [
        "id",
        "node_type",
        "head_hash",
        "maturity",
        "status",
        "vetted",
        "created_at",
        "updated_at",
    ],
    "commits": [
        "hash",
        "node_id",
        "parents",
        "object_hash",
        "change_class",
        "facets_touched",
        "author",
        "message",
        "ts",
    ],
    "edges": [
        "id",
        "src",
        "dst",
        "edge_type",
        "facet_binding",
        "provenance",
        "mode",
        "pinned_commit",
        "created_at",
        "retracted_at",
    ],
    "redirects": ["old_id", "successors", "created_at"],
    "review_queue": [
        "id",
        "node_id",
        "cause_kind",
        "cause_ref",
        "facet",
        "created_at",
        "resolved_at",
        "resolution",
    ],
    "triggers": ["id", "node_id", "condition", "params", "enabled"],
    "sync_files": ["path", "vault", "base_hash", "contract_version", "last_synced_at"],
    "tokens": [
        "id",
        "name",
        "class",
        "secret_hash",
        "rate_per_min",
        "created_at",
        "revoked_at",
    ],
    "audit_log": ["ts", "token_id", "action", "detail"],
}

EXPECTED_INDEXES = {"ix_edges_dst", "ix_edges_src"}


def _run_migrations(tmp_path):
    conn = store.connect(tmp_path / "schema_test.db")
    store.run_migrations(conn)
    return conn


def test_001_init_applied(tmp_path):
    conn = store.connect(tmp_path / "test.db")
    applied = store.run_migrations(conn)
    assert "001_init.sql" in applied


def test_all_tables_present_with_matching_columns(tmp_path):
    conn = _run_migrations(tmp_path)
    for table, expected_columns in EXPECTED_TABLE_COLUMNS.items():
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        assert rows, f"table {table!r} missing from schema"
        actual_columns = [row[1] for row in rows]
        assert actual_columns == expected_columns, (
            f"column mismatch for {table!r}: {actual_columns} != {expected_columns}"
        )


def test_partial_indexes_present(tmp_path):
    conn = _run_migrations(tmp_path)
    rows = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='index' AND name IN "
        "('ix_edges_dst', 'ix_edges_src')"
    ).fetchall()
    names = {row[0] for row in rows}
    assert names == EXPECTED_INDEXES
    for _name, sql in rows:
        assert "WHERE retracted_at IS NULL" in sql


def test_nodes_fts_virtual_table_present(tmp_path):
    conn = _run_migrations(tmp_path)
    row = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table' AND name='nodes_fts'"
    ).fetchone()
    assert row is not None
    assert "fts5" in row[1]

    columns = {r[1] for r in conn.execute("PRAGMA table_info(nodes_fts)").fetchall()}
    assert {"id", "body"} <= columns


def test_no_unlisted_tables_beyond_spec_and_bookkeeping(tmp_path):
    conn = _run_migrations(tmp_path)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual table')"
    ).fetchall()
    actual = {row[0] for row in rows}
    # _placeholder is created by 000_placeholder.sql (task T0.3's scaffold
    # migration), which also runs since migrations apply in filename order.
    expected = set(EXPECTED_TABLE_COLUMNS) | {
        "nodes_fts",
        "schema_migrations",
        "_placeholder",
    }
    # fts5 creates shadow tables (nodes_fts_data, nodes_fts_idx, etc.) — allow
    # anything prefixed with nodes_fts_ in addition to the exact expected set.
    unexpected = {
        name for name in actual if name not in expected and not name.startswith("nodes_fts_")
    }
    assert not unexpected, f"unexpected tables not in §4.4: {unexpected}"
