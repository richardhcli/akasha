"""Review routes: queue list + resolve (task T8.0).

Wires spec §4.11's ``/review*`` rows to ``kernel/store.py`` and
``tms/review.py``. ``GET /review`` is authenticated (``require_auth``) and
returns the UNCAPPED open set via ``store.find_open_reviews`` (the daily
cap of 10 is a read-side view in ``tms.review.active_queue``, not this
endpoint). ``POST /review/{id}/resolve`` is human-only (``require_human``,
∅) — agent tokens are rejected outright and never proposalized.

Task T13.6: after a successful resolution, best-effort re-project the
managed vault file (if any) that owns the review's node, mirroring
``routes/nodes.py``'s ``_reproject`` (task T13.3) exactly -- same
``reconcile.project_node_change`` helper, same shared
``request.app.state.origin_tracker``, never a second projection mechanism.

# SPEC-QUESTION (T13.6): pre-mvp T8.0 wired this route to ``resolve_review``
# only, for the four standard resolutions (``still_holds|revised|retracted|
# dismissed``); ``tms.review.approve_proposal`` and
# ``tms.review.resolve_reassignment`` have never had an HTTP route (no task's
# Files list has ever included that wiring, and T13.6's Files list is this
# module + a test file, not a new dispatch mechanism). Narrowest reading:
# this task closes projection for the resolution surface that actually
# exists in production (``resolve_review``) and does not invent new routing
# behavior that would let this endpoint mint nodes -- a capability change
# §4.11 does not describe. The "approved create-proposal projects nothing"
# DoD item is verified at the layer where proposal approval actually lives
# (calling ``tms.review.approve_proposal`` directly, then
# ``reconcile.project_node_change`` on the minted id) -- see
# ``tests/integration/test_projection_writeback.py``.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from akasha.api import auth
from akasha.api.deps import ApiError, get_conn, require_auth, require_human
from akasha.kernel import store
from akasha.kernel.model import Facet
from akasha.kernel.store import ReviewAlreadyResolvedError, ReviewNotFoundError
from akasha.tms import review as review_module
from akasha.tms.review import DismissalNotAllowedError

router = APIRouter(prefix="/v1", tags=["review"])

# Shared "akasha" logger (matches daemon.py's configure_logging target) --
# a projection failure below is logged through it, never raised to the
# caller (mirrors routes/nodes.py's T13.3 discipline).
logger = logging.getLogger("akasha")


def _reproject(request: Request, conn: Any, node_ids: list[str]) -> None:
    """Best-effort re-projection of the managed file that owns ``node_ids`` (task T13.6).

    Called AFTER ``tms.review.resolve_review``'s own transaction(s) have
    already committed -- never from inside them -- so a spoke-projection
    failure can never roll back or fail a review resolution that already
    succeeded. Identical in shape to ``routes/nodes.py``'s ``_reproject``
    (task T13.3): same ``reconcile.project_node_change`` helper, same
    ``request.app.state.origin_tracker``, same swallow-and-log contract.
    Deferred import for the same reason nodes.py's does (avoids a
    module-import cycle at app-construction time).
    """
    from akasha.sync import reconcile

    try:
        reconcile.project_node_change(conn, node_ids, request.app.state.origin_tracker)
    except Exception:
        logger.exception(
            "projection writeback failed for node_ids=%r after a successful review resolution",
            node_ids,
        )


class ResolveReviewBody(BaseModel):
    resolution: str
    new_body: str | None = None
    facets: list[Facet] | None = None
    change_class: str | None = None
    facets_touched: list[str] | None = None
    message: str = ""


@router.get("/review")
def list_reviews(
    status: str = Query("open"),
    node: str | None = Query(None),
    conn: Any = Depends(get_conn),
    _ctx: auth.AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    if status != "open":
        raise ApiError(422, "E_INVALID", "only status=open is supported")
    return {"reviews": store.find_open_reviews(conn, node_id=node)}


@router.post("/review/{review_id}/resolve")
def resolve_review(
    review_id: str,
    body: ResolveReviewBody,
    request: Request,
    conn: Any = Depends(get_conn),
    _ctx: auth.AuthContext = Depends(require_human),
) -> dict[str, Any]:
    # Always pass change_class / facets_touched (required by the revised
    # branch via kwargs['...'] with no .get default); harmless no-ops for
    # still_holds / retracted / dismissed.
    kwargs: dict[str, Any] = {
        "author": "human",
        "message": body.message,
        "new_body": body.new_body,
        "facets": body.facets,
        "change_class": body.change_class,
        "facets_touched": body.facets_touched if body.facets_touched is not None else [],
    }
    try:
        result = review_module.resolve_review(conn, review_id, body.resolution, **kwargs)
    except ReviewNotFoundError as exc:
        raise ApiError(404, "E_NOT_FOUND", str(exc)) from exc
    except ReviewAlreadyResolvedError as exc:
        raise ApiError(409, "E_CONFLICT", str(exc)) from exc
    except DismissalNotAllowedError as exc:
        raise ApiError(409, "E_CONFLICT", str(exc)) from exc
    except ValueError as exc:
        raise ApiError(422, "E_INVALID", str(exc)) from exc
    # T13.6: best-effort re-projection of the resolved review's node, after
    # resolve_review's own transaction(s) have already committed. Resolutions
    # that commit no new content (still_holds/dismissed/retracted) still pass
    # through here -- the helper's own idempotence (write-if-diff) makes a
    # quiet no-op when the projected file is already current, so no
    # special-casing of "which resolutions changed content" is needed. A
    # review with no node (node_id is NULL -- currently only cause_kind
    # 'proposal', which this route never resolves; see the module-level
    # SPEC-QUESTION above) has nothing to project.
    node_id = result.get("node_id")
    if node_id is not None:
        _reproject(request, conn, [node_id])
    return result
