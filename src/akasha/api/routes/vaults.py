"""Vault routes: register/list managed vaults (task T4.5, spec §4.11 ``/vaults``, human-only ∅).

# SPEC-QUESTION (T4.5): §4.11 lists ``GET/POST /vaults`` ("register/list
# managed vaults", human only ∅), but (a) T4.5's Files list has no
# ``routes/vaults.py`` entry and (b) the frozen §4.4 DDL (``migrations/
# 001_init.sql``, "verbatim") has no ``vaults`` table — only ``sync_files
# (path, vault, base_hash, contract_version, last_synced_at)``, which is
# per-*file*, not per-vault, and requires a real synced file to exist
# before a vault name shows up in it. Adding a new ``vaults`` table via a
# ``migrations/002_*.sql`` was considered and rejected: build-plan rule 2
# ("never invent schema... beyond docs/mvp-spec.md") is non-negotiable, and
# ``tests/unit/kernel/test_schema.py::test_no_unlisted_tables_beyond_spec_and_bookkeeping``
# already hard-asserts no table exists beyond §4.4's exact list — a new
# migration would fail that existing, out-of-scope-for-T4.5 acceptance test
# (or force editing it, an unlisted file, to weaken a schema-freeze
# invariant, which build-plan rule 8/9 forbid guessing into). Narrowest
# reading taken instead, matching build-plan T4.5 step 4 verbatim ("state
# only; watching arrives in M5"): (1) ``GET /vaults`` returns the union of
# (a) vault names derived read-only from ``sync_files.vault`` (the one
# spec-sanctioned column that names a vault, via ``store.list_synced_vaults``
# — zero schema invention) and (b) vaults explicitly registered through this
# route's ``POST``, held in a process-local, in-memory registry — the exact
# same "acceptable non-persisted operational state" precedent
# ``api/auth.py``'s rate-limiter already established for this codebase
# (documented there: "intentionally process-local and not persisted...
# acceptable... not a truth-bearing invariant"). (2) ``POST /vaults``
# upserts into that in-memory registry only; it does NOT write to
# ``sync_files`` (a real per-file table; writing a fake sentinel row into it
# would corrupt M5's reconcile-pipeline invariants over that table).
# CONSEQUENCE / RISK (flagged loudly per the task instructions): a vault
# registered via ``POST /vaults`` does **not** survive a daemon restart —
# only vaults that have since gained real ``sync_files`` rows (via M5's
# watcher) persist across restarts. This is a real gap a human should
# confirm before M5 (T5.1, "base store") lands; the true fix is likely a
# proper ``vaults`` table added as part of that milestone's own schema
# task, not smuggled in here. Logged in docs/spec-questions.md.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from akasha.api import auth
from akasha.api.deps import get_conn, require_human
from akasha.kernel import store

router = APIRouter(prefix="/v1", tags=["vaults"])


class RegisterVaultBody(BaseModel):
    name: str
    root_path: str


# Process-local, in-memory registry of explicitly-``POST``ed vaults: name ->
# {"name", "root_path", "created_at"}. See the module SPEC-QUESTION above —
# intentionally not persisted (mirrors ``api/auth.py``'s rate-limit state).
_registered: dict[str, dict[str, Any]] = {}


def _reset_registry() -> None:  # pyright: ignore[reportUnusedFunction]
    """Test-only hook to clear in-process vault registration state between tests.

    Not called from anywhere in ``src/`` (pyright's ``reportUnusedFunction``
    would otherwise flag it) — only from ``tests/integration/test_api.py``'s
    autouse fixture, mirroring how ``api/auth.py``'s ``_call_log`` is reset
    directly by tests rather than via an in-``src`` caller.
    """
    _registered.clear()


@router.get("/vaults")
def list_vaults(
    conn: Any = Depends(get_conn),
    _ctx: auth.AuthContext = Depends(require_human),
) -> dict[str, Any]:
    synced = {name: {"name": name, "root_path": None, "created_at": None}
              for name in store.list_synced_vaults(conn)}
    # Explicitly-registered entries win over the bare synced-only placeholder
    # (they carry a real root_path/created_at).
    merged = {**synced, **_registered}
    return {"vaults": [merged[name] for name in sorted(merged)]}


@router.post("/vaults", status_code=201)
def register_vault(
    payload: RegisterVaultBody,
    _ctx: auth.AuthContext = Depends(require_human),
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    existing = _registered.get(payload.name)
    record = {
        "name": payload.name,
        "root_path": payload.root_path,
        "created_at": existing["created_at"] if existing else now,
    }
    _registered[payload.name] = record
    return record
