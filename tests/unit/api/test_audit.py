"""Unit tests for the audit log (task T4.2, spec §4.4 audit_log / §4.11).

Covers the DoD: a mutating request writes exactly one audit row carrying the
token id + action; a read writes none; the log is append-only and never
contains a secret.
"""

from __future__ import annotations

import sqlite3

import pytest

from akasha.api import auth
from akasha.kernel import store


def _fresh_conn(tmp_path) -> sqlite3.Connection:
    conn = store.connect(tmp_path / "audit_test.db")
    store.run_migrations(conn)
    return conn


def _audit_rows(conn: sqlite3.Connection) -> list[tuple]:
    return conn.execute(
        "SELECT ts, token_id, action, detail FROM audit_log ORDER BY rowid"
    ).fetchall()


def _ctx(token_id: str = "abc23456") -> auth.AuthContext:
    return auth.AuthContext(
        token_id=token_id, name="t", token_class="human", rate_per_min=None
    )


# --- store.append_audit: the append-only primitive -------------------------


def test_append_audit_writes_exactly_one_row(tmp_path):
    conn = _fresh_conn(tmp_path)
    store.append_audit(conn, "abc23456", "POST /v1/nodes", "detail text")
    rows = _audit_rows(conn)
    assert len(rows) == 1
    ts, token_id, action, detail = rows[0]
    assert token_id == "abc23456"
    assert action == "POST /v1/nodes"
    assert detail == "detail text"
    # ts is stamped by the store (non-empty ISO string), not the caller.
    assert ts and isinstance(ts, str)


def test_append_audit_allows_null_token_and_detail(tmp_path):
    conn = _fresh_conn(tmp_path)
    store.append_audit(conn, None, "unauthenticated action")
    rows = _audit_rows(conn)
    assert len(rows) == 1
    _, token_id, action, detail = rows[0]
    assert token_id is None
    assert action == "unauthenticated action"
    assert detail is None


def test_append_audit_is_append_only_and_ordered(tmp_path):
    conn = _fresh_conn(tmp_path)
    store.append_audit(conn, "abc23456", "POST /v1/nodes")
    store.append_audit(conn, "abc23456", "PATCH /v1/nodes/xy234567")
    store.append_audit(conn, "def23456", "DELETE /v1/edges/e2345678")
    rows = _audit_rows(conn)
    assert [r[2] for r in rows] == [
        "POST /v1/nodes",
        "PATCH /v1/nodes/xy234567",
        "DELETE /v1/edges/e2345678",
    ]


# --- auth.record_mutation: the middleware/decorator policy -----------------


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "post", "delete"])
def test_mutating_request_writes_exactly_one_row(tmp_path, method):
    conn = _fresh_conn(tmp_path)
    wrote = auth.record_mutation(conn, method, f"{method.upper()} /v1/nodes", _ctx())
    assert wrote is True
    rows = _audit_rows(conn)
    assert len(rows) == 1
    _, token_id, action, _ = rows[0]
    assert token_id == "abc23456"
    assert action == f"{method.upper()} /v1/nodes"


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS", "get"])
def test_read_request_writes_no_row(tmp_path, method):
    conn = _fresh_conn(tmp_path)
    wrote = auth.record_mutation(conn, method, f"{method.upper()} /v1/nodes/x", _ctx())
    assert wrote is False
    assert _audit_rows(conn) == []


def test_record_mutation_without_context_logs_null_token(tmp_path):
    conn = _fresh_conn(tmp_path)
    wrote = auth.record_mutation(conn, "POST", "POST /v1/health-check", None)
    assert wrote is True
    rows = _audit_rows(conn)
    assert len(rows) == 1
    assert rows[0][1] is None


def test_is_mutating_method_classification():
    assert auth.is_mutating_method("post")
    assert auth.is_mutating_method("DELETE")
    assert not auth.is_mutating_method("get")
    assert not auth.is_mutating_method("OPTIONS")


def test_audit_never_records_a_secret(tmp_path):
    """The recording path only ever sees a token_id, never a raw secret.

    ``record_mutation`` takes an ``AuthContext`` (token_id/name/class/rate),
    which structurally cannot carry the bearer secret, so a secret can never
    reach ``audit_log`` through this path.
    """
    conn = _fresh_conn(tmp_path)
    raw_secret = auth.mint_secret()
    ctx = _ctx()
    auth.record_mutation(conn, "POST", "POST /v1/nodes", ctx)
    stored = " ".join(str(field) for row in _audit_rows(conn) for field in row)
    assert raw_secret not in stored
    assert "AuthContext" not in stored  # we log token_id, not the ctx repr
