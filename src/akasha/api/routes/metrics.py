"""Metrics route: GET /v1/metrics (task T9.2, spec §4.11 ``GET /metrics``, §7).

Wires the §4.11 ``GET /metrics`` row ("§7 counters (JSON)") to
``metrics.compute_metrics``. Not marked ∅ (human-only) in the spec table,
so open to any authenticated token (``require_auth``); it's a read, so
agent-token proposal rewriting (task T4.6) never applies -- only mutations
are proposalized (same reasoning as ``routes/search.py``).

Named ``metrics.py`` rather than ``health.py`` (the task's Files list
offers either): ``GET /health`` is deliberately unauthenticated and
root-level (spec §4.11) and already lives directly in ``api/app.py``, not
a ``routes/*`` module -- adding a ``health.py`` route file here would
duplicate that, not extend it. ``GET /metrics`` is an authenticated,
versioned ``/v1`` resource like every other ``routes/*`` module, so it
gets its own file, following the ``routes/search.py``/``routes/sync.py``
precedent.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from akasha import metrics
from akasha.api import auth
from akasha.api.deps import get_conn, require_auth

router = APIRouter(prefix="/v1", tags=["metrics"])


@router.get("/metrics")
def get_metrics(
    conn: Any = Depends(get_conn),
    _ctx: auth.AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    return metrics.compute_metrics(conn)
