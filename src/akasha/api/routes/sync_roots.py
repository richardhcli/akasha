"""Durable filesystem sync-root routes (task T4.10, spec §4.11).

A sync root is one registered directory watched by the daemon. In the MVP
that directory is an Obsidian vault, but “spoke” remains the integration
type and “Obsidian vault” remains user-facing terminology.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from akasha.api import auth
from akasha.api.deps import ApiError, get_conn, require_human
from akasha.kernel import store

router = APIRouter(prefix="/v1/sync", tags=["sync-roots"])
logger = logging.getLogger("akasha")


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
    request: Request,
    conn: Any = Depends(get_conn),
    _ctx: auth.AuthContext = Depends(require_human),
) -> dict[str, Any]:
    try:
        root = store.register_sync_root(conn, payload.name, payload.root_path)
    except ValueError as exc:
        raise ApiError(400, "E_INVALID", str(exc)) from exc
    # Debug-plan D11: start watching the folder BEFORE answering, so the caller's
    # follow-up rescan (watch first, scan second) leaves no window in which a
    # write is neither scanned nor seen. `app.state.watcher` is set by
    # `daemon.serve`; embedded/test apps have none, and the poll loop is the backstop.
    watcher = getattr(request.app.state, "watcher", None)
    if watcher is not None:
        try:
            watcher.watch_new_roots(store.list_sync_roots(conn))
        except Exception:
            logger.exception("could not watch %s yet; the poll loop will retry", payload.root_path)
    return root
