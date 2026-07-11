"""SQLite access layer. The only module that writes SQLite (build-plan rule 0.4).

Node/edge/commit CRUD lands in later milestones (M1, spec §4.5); this module
currently only carries the migration runner (task T0.3).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _ensure_bookkeeping(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "filename TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now'))"
        ")"
    )


def applied_migrations(conn: sqlite3.Connection) -> set[str]:
    _ensure_bookkeeping(conn)
    rows = conn.execute("SELECT filename FROM schema_migrations").fetchall()
    return {row[0] for row in rows}


def run_migrations(
    conn: sqlite3.Connection, migrations_dir: str | Path = MIGRATIONS_DIR
) -> list[str]:
    """Apply pending .sql files from migrations_dir in filename order, once each.

    Refuses to re-run an already-applied file and never applies files out of
    the sorted-filename order (forward-only, per spec §4.4 note).
    """
    _ensure_bookkeeping(conn)
    applied = applied_migrations(conn)
    files = sorted(Path(migrations_dir).glob("*.sql"))
    newly_applied: list[str] = []
    for path in files:
        if path.name in applied:
            continue
        sql = path.read_text(encoding="utf-8")
        with conn:
            conn.executescript(sql)
            conn.execute("INSERT INTO schema_migrations (filename) VALUES (?)", (path.name,))
        newly_applied.append(path.name)
    return newly_applied
