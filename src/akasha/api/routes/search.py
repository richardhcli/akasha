"""Search route: FTS over node bodies (task T4.5, spec §4.11 ``GET /search``).

Wires ``GET /search?q=`` to ``kernel.store.search`` (T1.4's FTS5 wiring over
``nodes_fts``, spec §4.4/§4.5). Not marked ∅ in the spec table, so open to
any authenticated token (``require_auth``); it's a read, so agent-token
proposal rewriting (T4.6) never applies to it (only mutations are
proposalized).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from akasha.api import auth
from akasha.api.deps import get_conn, require_auth
from akasha.kernel import store

router = APIRouter(prefix="/v1", tags=["search"])


@router.get("/search")
def search(
    q: str = Query(...),
    conn: Any = Depends(get_conn),
    _ctx: auth.AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    nodes = store.search(conn, q)
    return {"results": [node.model_dump() for node in nodes]}
