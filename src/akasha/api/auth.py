"""Token authentication, secret hashing, and per-token rate limiting.

Task T4.1 (spec §4.11 Bearer-token auth; §4.4 ``tokens`` DDL, verbatim,
already migrated by ``migrations/001_init.sql`` — not re-derived here):

    CREATE TABLE tokens (id TEXT PRIMARY KEY, name TEXT NOT NULL,
                          class TEXT NOT NULL,               -- human|agent
                          secret_hash TEXT NOT NULL, rate_per_min INTEGER,
                          created_at TEXT NOT NULL, revoked_at TEXT);

This module only *reads* the ``tokens`` table (never INSERT/UPDATE/DELETE
here — build-plan rule 0.4 reserves persistent writes for
``kernel/store.py``, and token issuance/revocation is a separate,
API-layer concern that belongs to T4.5's ``/tokens`` route). Looking up a
token by bearer value is authentication, not truth-bearing state
mutation, so it is a legitimate direct read against ``sqlite3.Connection``
here rather than a ``kernel/store.py`` addition (§4.5's documented store
surface has no "get token by bearer value" function, and doesn't need
one).

Bearer token format
--------------------
The ``tokens`` table stores only ``secret_hash``, never the raw secret.
The bearer value presented over HTTP (``Authorization: Bearer <value>``)
is therefore ``"{token_id}.{raw_secret}"`` — a single ``"."`` separates
the token's primary-key id (spec §4.1 id8, ``[a-z2-7]{8}``, which never
itself contains ``"."``) from an opaque raw secret. This lets lookup be an
O(1) ``SELECT ... WHERE id=?`` rather than a hash-and-scan over every row.
Splitting on the *first* ``"."`` is used (so a raw secret is free to
contain further ``"."`` characters). ``T4.5``'s ``POST /tokens`` route
(and the CLI's ``token create`` verb) must mint new tokens in this same
``"{id}.{raw_secret}"`` shape — see ``mint_secret``/``hash_secret`` below,
which this module exposes precisely so that future token-creation code
reuses the same hashing scheme instead of re-deriving it.

Secret hashing
---------------
Per build-plan rule 0.5 constraints for this task (no new dependency —
``bcrypt``/``passlib``/``argon2`` are not in ``pyproject.toml``, and this
task's scope does not include adding one), the raw secret is hashed with
stdlib ``hashlib.sha256`` and compared against the stored ``secret_hash``
with ``hmac.compare_digest`` (constant-time, avoids leaking a byte-by-byte
timing oracle on the comparison). Raw secrets are minted with
``secrets.token_urlsafe`` (stdlib, CSPRNG) — never anything from
``random``.

Rate limiting
--------------
``rate_per_min`` is a per-token integer column; ``NULL`` means unlimited
(never rate-limited). The daemon is a single local process on
``127.0.0.1`` (spec §3), so an external store (Redis etc.) would be
overkill; this module keeps an in-process, in-memory fixed-window counter
keyed by token id (module-level dict, one deque of call timestamps per
token, pruned to the trailing 60-second window on each check — i.e. a
sliding window, not a fixed-window that resets abruptly on the minute
boundary). This state is intentionally process-local and not persisted;
it resets on daemon restart, which is acceptable for a rate limit (not a
truth-bearing invariant).

This module exposes plain functions only — it is deliberately NOT a
FastAPI dependency or route itself; wiring ``authenticate`` into the app
as a dependency is T4.3's (``app.py``) job.
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
    """Generate a fresh, CSPRNG raw secret suitable for a new token.

    Not wired to any DB write here (T4.1 is read-only re: ``tokens`` —
    see module docstring); provided so token-minting code (T4.5's
    ``POST /tokens``, the CLI's ``token create``) reuses the exact same
    secret-generation scheme rather than re-deriving it, since this
    module is the natural owner of the hashing/secret format.
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


def check_rate_limit(
    token_id: str, rate_per_min: int | None, *, now: float | None = None
) -> None:
    """Record a call for ``token_id`` and raise if it exceeds ``rate_per_min``.

    ``rate_per_min is None`` means unlimited: never rate-limited, and no
    call history is recorded for it (nothing to enforce). Otherwise
    prunes the call log to the trailing ``_RATE_WINDOW_SECONDS`` window,
    then raises ``RateLimitExceededError`` if recording this call would
    put the count for the window over ``rate_per_min``; a call that is
    itself rejected as rate-limited is NOT added to the log (retrying
    immediately after backing off should not compound the penalty).

    ``now`` is an injectable clock hook (seconds) for deterministic tests;
    defaults to ``time.monotonic()``.
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
    """Authenticate an ``Authorization: Bearer <bearer_value>`` header value.

    Read-only against ``tokens`` (never writes ``tokens``/``audit_log`` —
    see module docstring). Raises, in order of check:

    - ``MalformedBearerError`` if ``bearer_value`` doesn't split into a
      non-empty ``token_id`` and ``raw_secret``.
    - ``UnknownTokenError`` if no ``tokens`` row has this id.
    - ``InvalidSecretError`` if the row exists but the hashed secret
      doesn't match ``secret_hash`` (constant-time compare).
    - ``RevokedTokenError`` if the secret matches but ``revoked_at`` is
      non-NULL.
    - ``RateLimitExceededError`` if the token's ``rate_per_min`` budget
      (sliding 60s window, see ``check_rate_limit``) is exceeded by this
      call.

    On success returns an ``AuthContext`` exposing ``token_class`` so
    callers can branch on human vs. agent (spec §4.11).
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


# --- Audit log (task T4.2, spec §4.4 ``audit_log`` DDL / §4.11) -------------
#
# Every *mutating* API action appends one ``(ts, token_id, action, detail)``
# row to ``audit_log``; reads append nothing. This module owns the *policy*
# (what counts as a mutation, what to record, never recording a secret); the
# actual SQLite INSERT is delegated to ``kernel.store.append_audit`` because
# build-plan rule 0.4 reserves all persistent writes for ``kernel/store.py``
# (no other module writes SQLite directly). See the SPEC-QUESTION below on the
# Files-list vs. rule-0.4 tension for T4.2.

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
    """Append one ``audit_log`` row iff ``method`` is a mutating HTTP method.

    This is the primitive T4.3's FastAPI middleware/decorator wraps around
    each request: pass the request's HTTP ``method``, a stable ``action``
    label (e.g. ``"POST /v1/nodes"``), and the authenticated ``ctx`` (or
    ``None`` for an unauthenticated action). Returns ``True`` if a row was
    written, ``False`` for a read (so a read provably writes nothing —
    T4.2 DoD).

    The bearer *secret* is never in scope here (only ``ctx.token_id`` is),
    so no secret can leak into the log; ``detail`` is caller-controlled and
    the caller keeps it secret-free (spec §4.11 step 2). Exactly one row is
    appended per mutating call — ``store.append_audit`` issues a single
    append-only ``INSERT`` (T4.2 DoD: "exactly one audit row").
    """
    if not is_mutating_method(method):
        return False
    token_id = ctx.token_id if ctx is not None else None
    store.append_audit(conn, token_id, action, detail)
    return True


# SPEC-QUESTION (T4.2): the build-plan Files list for T4.2 is
# "src/akasha/api/auth.py or middleware, tests/unit/api/test_audit.py" and does
# NOT list kernel/store.py, but non-negotiable rule 0.4 ("every mutation of
# persistent state goes through kernel/store.py; no other module writes SQLite
# directly") forbids INSERTing into audit_log from this API-layer module.
# Narrowest reading taken: rule 0.4 is non-negotiable and controls, so the raw
# audit INSERT is a minimal append-only helper (store.append_audit) in
# store.py, and this module holds only the mutation-detection/recording policy.
# The alternative (INSERT directly here) would violate rule 0.4. Logged in
# docs/spec-questions.md for a human to confirm the store.py touch is intended.
