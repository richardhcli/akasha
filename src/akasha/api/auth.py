"""Token authentication, secret hashing and per-token rate limiting (spec §4.11, §4.4 ``tokens``).

This module only READS ``tokens`` (issuance and revocation are ``kernel/store.py``, rule 0.4);
looking a token up by bearer value is authentication, not a truth-bearing write. It exposes plain
functions, not a FastAPI dependency; ``app.py`` wires ``authenticate``.

Bearer format: ``"{token_id}.{raw_secret}"``. The table stores only ``secret_hash``; the id (an
id8, which never contains ``"."``) makes lookup one ``SELECT ... WHERE id=?`` instead of a
hash-and-scan. Splitting is on the FIRST ``"."``, so a secret may contain more. Token creation must
use ``mint_secret``/``hash_secret`` so the scheme lives in one place.

Hashing: stdlib ``hashlib.sha256`` (no new dependency), compared with ``hmac.compare_digest``
(constant time); secrets come from ``secrets.token_urlsafe`` (CSPRNG).

Rate limiting: ``rate_per_min`` is per token, ``NULL`` means unlimited. State is an in-process dict
of call timestamps per token, pruned to a trailing 60 s (a sliding window). It is not persisted and
resets on restart, which is fine for a rate limit on a single local process (spec §3).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import time
from collections import deque
from dataclasses import dataclass
from typing import Literal

from akasha.kernel import store

TokenClass = Literal["human", "agent"]

# Sliding rate-limit window, in seconds. ``rate_per_min`` counts calls
# allowed per this many seconds (spec: "rate-limited per token", §4.11;
# the column name ``rate_per_min`` fixes the window to 60s).
_RATE_WINDOW_SECONDS = 60.0


class AuthError(Exception):
    """Base class for every authentication failure raised by this module.

    Carries ``.code`` so a future FastAPI dependency (T4.3+) can map each
    subclass to a distinct HTTP status/error-envelope code without
    re-deriving the mapping.
    """

    code = "E_AUTH"


class MalformedBearerError(AuthError):
    """Bearer value doesn't parse as ``"{token_id}.{raw_secret}"``."""

    code = "E_AUTH_MALFORMED"


class UnknownTokenError(AuthError):
    """No row in ``tokens`` has this token id."""

    code = "E_AUTH_UNKNOWN_TOKEN"


class InvalidSecretError(AuthError):
    """Token id exists but the raw secret doesn't match ``secret_hash``."""

    code = "E_AUTH_INVALID_SECRET"


class RevokedTokenError(AuthError):
    """Token id exists, secret matches, but ``revoked_at`` is set."""

    code = "E_AUTH_REVOKED"


class RateLimitExceededError(AuthError):
    """Token authenticated but has exceeded its ``rate_per_min`` budget.

    Distinguishable from every other ``AuthError`` subclass so a future
    caller (T4.3+ FastAPI dependency) can map this specifically to a
    429-style response rather than 401/403.
    """

    code = "E_RATE_LIMITED"


@dataclass(frozen=True)
class AuthContext:
    """Result of a successful ``authenticate`` call.

    Exposes ``token_class`` so downstream routes (T4.4-T4.6) can branch
    on human vs. agent (e.g. agent-class mutations to non-∅ endpoints are
    rewritten into review-queue proposals, spec §4.11).
    """

    token_id: str
    name: str
    token_class: TokenClass
    rate_per_min: int | None


def hash_secret(raw_secret: str) -> str:
    """Hash a raw secret the same way for minting and verifying (sha256 hex)."""
    return hashlib.sha256(raw_secret.encode("utf-8")).hexdigest()


def mint_secret() -> str:
    """A fresh CSPRNG raw secret for a new token. Token-creating code (``POST /tokens``, the CLI)
    reuses this so the secret scheme is defined once.
    """
    return secrets.token_urlsafe(32)


def format_bearer_token(token_id: str, raw_secret: str) -> str:
    """Compose the bearer value handed to a client at token-creation time."""
    return f"{token_id}.{raw_secret}"


def _split_bearer(bearer_value: str) -> tuple[str, str]:
    if "." not in bearer_value:
        raise MalformedBearerError(
            "bearer value must be '{token_id}.{raw_secret}'; no '.' separator found"
        )
    token_id, _, raw_secret = bearer_value.partition(".")
    if not token_id or not raw_secret:
        raise MalformedBearerError(
            "bearer value must be '{token_id}.{raw_secret}'; both parts must be non-empty"
        )
    return token_id, raw_secret


# Module-level, in-process rate-limit state: token_id -> deque of call
# timestamps (seconds, monotonic clock) within the trailing window.
# Documented in the module docstring: intentionally not persisted, resets
# on daemon restart, fine for a single-process localhost daemon (spec §3).
_call_log: dict[str, deque[float]] = {}


def _clock() -> float:
    return time.monotonic()


def check_rate_limit(token_id: str, rate_per_min: int | None, *, now: float | None = None) -> None:
    """Record a call for ``token_id`` and raise if it exceeds ``rate_per_min``.

    ``None`` means unlimited: no history is kept. Otherwise the log is pruned to the trailing
    window and ``RateLimitExceededError`` is raised if this call would exceed the budget; a
    rejected call is NOT logged, so backing off does not compound the penalty. ``now`` is an
    injectable clock (seconds).
    """
    if rate_per_min is None:
        return

    current = _clock() if now is None else now
    window_start = current - _RATE_WINDOW_SECONDS
    log = _call_log.setdefault(token_id, deque())

    while log and log[0] < window_start:
        log.popleft()

    if len(log) >= rate_per_min:
        raise RateLimitExceededError(
            f"token {token_id!r} exceeded rate limit of {rate_per_min}/min"
        )

    log.append(current)


def authenticate(
    conn: sqlite3.Connection, bearer_value: str, *, now: float | None = None
) -> AuthContext:
    """Authenticate an ``Authorization: Bearer`` value against ``tokens`` (read-only).

    Raises, in order: ``MalformedBearerError`` (no non-empty id and secret), ``UnknownTokenError``,
    ``InvalidSecretError`` (constant-time compare), ``RevokedTokenError``,
    ``RateLimitExceededError``. Returns an ``AuthContext`` whose ``token_class`` distinguishes
    human from agent (spec §4.11).
    """
    token_id, raw_secret = _split_bearer(bearer_value)

    row = conn.execute(
        "SELECT name, class, secret_hash, rate_per_min, revoked_at FROM tokens WHERE id=?",
        (token_id,),
    ).fetchone()
    if row is None:
        raise UnknownTokenError(f"unknown token id {token_id!r}")

    name, token_class, secret_hash, rate_per_min, revoked_at = row

    candidate_hash = hash_secret(raw_secret)
    if not hmac.compare_digest(candidate_hash, secret_hash):
        raise InvalidSecretError(f"invalid secret for token id {token_id!r}")

    if revoked_at is not None:
        raise RevokedTokenError(f"token id {token_id!r} was revoked at {revoked_at}")

    check_rate_limit(token_id, rate_per_min, now=now)

    return AuthContext(
        token_id=token_id,
        name=name,
        token_class=token_class,
        rate_per_min=rate_per_min,
    )


# --- Audit log (spec §4.4 ``audit_log``, §4.11) --- Every mutating API action appends one ``(ts,
# token_id, action, detail)`` row; reads append nothing. This module owns the policy (what counts
# as a mutation, never recording a secret); the INSERT is ``kernel.store.append_audit`` (rule 0.4).

# HTTP methods that mutate persistent state and therefore MUST be audited
# (spec §4.11: agent-class *mutating* endpoints; every mutation is auditable).
# ``GET``/``HEAD``/``OPTIONS`` are reads and record nothing (DoD: "reads write
# none"). Kept as a frozenset so T4.3's middleware can classify a request by
# its method without re-deriving this list.
MUTATING_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def is_mutating_method(method: str) -> bool:
    """True iff ``method`` (any case) is an HTTP method that mutates state."""
    return method.upper() in MUTATING_METHODS


def record_mutation(
    conn: sqlite3.Connection,
    method: str,
    action: str,
    ctx: AuthContext | None,
    *,
    detail: str | None = None,
) -> bool:
    """Append one ``audit_log`` row iff ``method`` is a mutating HTTP method; return whether one
    was written.

    The primitive the app's middleware wraps around each request: the HTTP ``method``, a stable
    ``action`` label (``"POST /v1/nodes"``) and the authenticated ``ctx`` (``None`` if
    unauthenticated). Only ``ctx.token_id`` is in scope, never the secret; the caller keeps
    ``detail`` secret-free (§4.11).
    """
    if not is_mutating_method(method):
        return False
    token_id = ctx.token_id if ctx is not None else None
    store.append_audit(conn, token_id, action, detail)
    return True
