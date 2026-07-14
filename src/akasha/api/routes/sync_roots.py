"""Durable filesystem sync-root routes (task T4.10, spec §4.11).

A sync root is one registered directory watched by the daemon. In the MVP
that directory is an Obsidian vault, but “spoke” remains the integration
type and “Obsidian vault” remains user-facing terminology.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from akasha.api import auth
from akasha.api.deps import ApiError, get_conn, require_human
from akasha.kernel import store

router = APIRouter(prefix="/v1/sync", tags=["sync-roots"])


class RegisterSyncRootBody(BaseModel):
    name: str
    root_path: str


@router.get("/roots")
def list_sync_roots(
    conn: Any = Depends(get_conn),
    _ctx: auth.AuthContext = Depends(require_human),
) -> dict[str, Any]:
    return {"sync_roots": store.list_sync_roots(conn)}


@router.post("/roots", status_code=201)
def register_sync_root(
    payload: RegisterSyncRootBody,
    conn: Any = Depends(get_conn),
    _ctx: auth.AuthContext = Depends(require_human),
) -> dict[str, Any]:
    try:
        return store.register_sync_root(conn, payload.name, payload.root_path)
    except ValueError as exc:
        raise ApiError(400, "E_INVALID", str(exc)) from exc
