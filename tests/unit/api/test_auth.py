"""Unit tests for src/akasha/api/auth.py (task T4.1)."""

from __future__ import annotations

import sqlite3

import pytest

from akasha.api import auth
from akasha.kernel import store


@pytest.fixture(autouse=True)
def _reset_rate_limit_state():
    """Clear the module-level rate-limit log between tests.

    ``auth._call_log`` is intentional in-process state (see auth.py's
    module docstring), but that means it persists across test functions
    in the same pytest process unless explicitly reset here — otherwise a
    token id reused in a later test (or a real-clock call from an earlier
    test) would leak stale entries into a test using an injected clock.
    """
    auth._call_log.clear()
    yield
    auth._call_log.clear()


def _fresh_conn(tmp_path) -> sqlite3.Connection:
    conn = store.connect(tmp_path / "auth_test.db")
    store.run_migrations(conn)
    return conn


def _insert_token(
    conn: sqlite3.Connection,
    token_id: str,
    raw_secret: str,
    token_class: str,
    rate_per_min: int | None,
    *,
    name: str = "test token",
    revoked_at: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO tokens (id, name, class, secret_hash, rate_per_min, created_at, "
        "revoked_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            token_id,
            name,
            token_class,
            auth.hash_secret(raw_secret),
            rate_per_min,
            "2026-01-01T00:00:00.000000+00:00",
            revoked_at,
        ),
    )
    conn.commit()


def test_valid_human_token_authenticates_with_class_exposed(tmp_path):
    conn = _fresh_conn(tmp_path)
    _insert_token(conn, "humanid1", "secret-abc", "human", None, name="alice")

    ctx = auth.authenticate(conn, auth.format_bearer_token("humanid1", "secret-abc"))

    assert ctx.token_id == "humanid1"
    assert ctx.name == "alice"
    assert ctx.token_class == "human"
    assert ctx.rate_per_min is None


def test_valid_agent_token_authenticates_with_class_exposed(tmp_path):
    conn = _fresh_conn(tmp_path)
    _insert_token(conn, "agentid1", "secret-xyz", "agent", 100, name="bot")

    ctx = auth.authenticate(conn, auth.format_bearer_token("agentid1", "secret-xyz"))

    assert ctx.token_id == "agentid1"
    assert ctx.token_class == "agent"
    assert ctx.rate_per_min == 100


def test_unknown_token_id_rejected(tmp_path):
    conn = _fresh_conn(tmp_path)
    _insert_token(conn, "humanid1", "secret-abc", "human", None)

    with pytest.raises(auth.UnknownTokenError):
        auth.authenticate(conn, auth.format_bearer_token("nosuchid", "whatever"))


def test_wrong_secret_for_known_id_rejected_distinctly_from_unknown_id(tmp_path):
    conn = _fresh_conn(tmp_path)
    _insert_token(conn, "humanid1", "secret-abc", "human", None)

    with pytest.raises(auth.InvalidSecretError):
        auth.authenticate(conn, auth.format_bearer_token("humanid1", "wrong-secret"))


def test_revoked_token_rejected_distinctly_from_invalid(tmp_path):
    conn = _fresh_conn(tmp_path)
    _insert_token(
        conn,
        "humanid1",
        "secret-abc",
        "human",
        None,
        revoked_at="2026-02-01T00:00:00.000000+00:00",
    )

    with pytest.raises(auth.RevokedTokenError):
        auth.authenticate(conn, auth.format_bearer_token("humanid1", "secret-abc"))


def test_malformed_bearer_value_rejected(tmp_path):
    conn = _fresh_conn(tmp_path)
    _insert_token(conn, "humanid1", "secret-abc", "human", None)

    with pytest.raises(auth.MalformedBearerError):
        auth.authenticate(conn, "no-dot-separator-here")


def test_exceeding_rate_per_min_raises_rate_limit_error(tmp_path):
    conn = _fresh_conn(tmp_path)
    _insert_token(conn, "agentid1", "secret-xyz", "agent", 3, name="bot")
    bearer = auth.format_bearer_token("agentid1", "secret-xyz")

    # Use a fixed injected clock so the window doesn't depend on real time.
    for i in range(3):
        auth.authenticate(conn, bearer, now=100.0 + i * 0.01)

    with pytest.raises(auth.RateLimitExceededError):
        auth.authenticate(conn, bearer, now=100.05)


def test_rate_limit_window_slides_and_allows_calls_after_expiry(tmp_path):
    conn = _fresh_conn(tmp_path)
    _insert_token(conn, "agentid1", "secret-xyz", "agent", 2, name="bot")
    bearer = auth.format_bearer_token("agentid1", "secret-xyz")

    auth.authenticate(conn, bearer, now=0.0)
    auth.authenticate(conn, bearer, now=1.0)
    with pytest.raises(auth.RateLimitExceededError):
        auth.authenticate(conn, bearer, now=2.0)

    # Well past the 60s sliding window: earlier calls should have expired.
    auth.authenticate(conn, bearer, now=100.0)


def test_token_with_null_rate_per_min_is_never_rate_limited(tmp_path):
    conn = _fresh_conn(tmp_path)
    _insert_token(conn, "humanid1", "secret-abc", "human", None, name="alice")
    bearer = auth.format_bearer_token("humanid1", "secret-abc")

    for i in range(50):
        ctx = auth.authenticate(conn, bearer, now=1000.0 + i * 0.001)
        assert ctx.token_class == "human"


def test_check_rate_limit_directly_unlimited_never_raises():
    for i in range(1000):
        auth.check_rate_limit("some-unlimited-id", None, now=float(i))


def test_check_rate_limit_directly_exceeds_and_recovers():
    token_id = "direct-rl-token"
    auth.check_rate_limit(token_id, 2, now=0.0)
    auth.check_rate_limit(token_id, 2, now=0.5)
    with pytest.raises(auth.RateLimitExceededError):
        auth.check_rate_limit(token_id, 2, now=0.9)
    # Past the window: allowed again.
    auth.check_rate_limit(token_id, 2, now=61.0)


def test_hash_secret_is_deterministic_and_mint_secret_is_random():
    assert auth.hash_secret("same-input") == auth.hash_secret("same-input")
    assert auth.hash_secret("a") != auth.hash_secret("b")
    a, b = auth.mint_secret(), auth.mint_secret()
    assert a != b
    assert len(a) > 16
