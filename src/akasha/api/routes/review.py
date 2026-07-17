"""Review routes: queue list + resolve (task T8.0).

Wires spec §4.11's ``/review*`` rows to ``kernel/store.py`` and
``tms/review.py``. ``GET /review`` is authenticated (``require_auth``) and
returns the UNCAPPED open set via ``store.find_open_reviews`` (the daily
cap of 10 is a read-side view in ``tms.review.active_queue``, not this
endpoint). ``POST /review/{id}/resolve`` is human-only (``require_human``,
∅) — agent tokens are rejected outright and never proposalized.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from akasha.api import auth
from akasha.api.deps import ApiError, get_conn, require_auth, require_human
from akasha.kernel import store
from akasha.kernel.model import Facet
from akasha.kernel.store import ReviewAlreadyResolvedError, ReviewNotFoundError
from akasha.tms import review as review_module
from akasha.tms.review import DismissalNotAllowedError

router = APIRouter(prefix="/v1", tags=["review"])


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
        return review_module.resolve_review(conn, review_id, body.resolution, **kwargs)
    except ReviewNotFoundError as exc:
        raise ApiError(404, "E_NOT_FOUND", str(exc)) from exc
    except ReviewAlreadyResolvedError as exc:
        raise ApiError(409, "E_CONFLICT", str(exc)) from exc
    except DismissalNotAllowedError as exc:
        raise ApiError(409, "E_CONFLICT", str(exc)) from exc
    except ValueError as exc:
        raise ApiError(422, "E_INVALID", str(exc)) from exc
