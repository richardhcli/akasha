"""ID minting, checksum, and validation (build-plan task T2.1, spec §4.1).

An akasha id is an 8-char lowercase base32 string: 7 random "core" chars
(from ``secrets``) followed by 1 weighted-checksum char. Contract anchors
wrap the id as ``^tm-<id8>``; the bare id is never shown without that prefix
in managed files (spec §4.1).

Minting here is pure (no DB access) — collision-retry against
``nodes.id`` is the store layer's job (T1.x), not this module's.
"""

from __future__ import annotations

import secrets

# RFC 4648 base32 alphabet, lowercase, index 0-31 (spec §4.1, verbatim).
A = "abcdefghijklmnopqrstuvwxyz234567"

CORE_LEN = 7
ID_LEN = CORE_LEN + 1


class IdError(Exception):
    """Contract violation raised on a malformed or checksum-invalid id.

    ``code`` mirrors the linter code from spec §4.7 (`E_ID_CHECKSUM`
    covers any malformed anchor, not only a checksum mismatch, per the
    "E_ID_CHECKSUM malformed anchor" description in §4.7).
    """

    def __init__(self, message: str, code: str = "E_ID_CHECKSUM") -> None:
        super().__init__(message)
        self.code = code


def checksum(core: str) -> str:
    """Weighted checksum char for a 7-char core string (spec §4.1, verbatim)."""
    return A[sum((i + 1) * A.index(c) for i, c in enumerate(core)) % 32]


def mint() -> str:
    """Generate a fresh 8-char id: 7 random core chars + checksum char.

    Pure function — does not check for DB collisions. Callers that persist
    minted ids must retry on collision themselves (spec §4.1: loop bound
    10, then error) via the store layer.
    """
    core = "".join(secrets.choice(A) for _ in range(CORE_LEN))
    return core + checksum(core)


def validate(id_: str) -> None:
    """Validate an id: length 8, alphabet membership, checksum match.

    Raises ``IdError`` (code ``E_ID_CHECKSUM``) on any violation — never
    guesses or silently repairs (spec §4.1).
    """
    if len(id_) != ID_LEN:
        raise IdError(f"id {id_!r} must be {ID_LEN} chars, got {len(id_)}")
    if any(c not in A for c in id_):
        raise IdError(f"id {id_!r} contains chars outside alphabet {A!r}")
    core, check = id_[:CORE_LEN], id_[CORE_LEN]
    expected = checksum(core)
    if check != expected:
        raise IdError(f"id {id_!r} checksum mismatch: expected {expected!r}, got {check!r}")


def contract_anchor(id_: str) -> str:
    """Managed-file contract anchor for an id: ``^tm-<id8>`` (spec §4.1)."""
    return "^tm-" + id_
