from akasha.kernel import store


def test_applies_placeholder_migration_once(tmp_path):
    conn = store.connect(tmp_path / "test.db")
    applied = store.run_migrations(conn)
    assert "000_placeholder.sql" in applied

    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "_placeholder" in tables


def test_idempotent_on_rerun(tmp_path):
    conn = store.connect(tmp_path / "test.db")
    store.run_migrations(conn)
    second_run = store.run_migrations(conn)
    assert second_run == []


def test_pragmas_set_on_connection(tmp_path):
    conn = store.connect(tmp_path / "test.db")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
