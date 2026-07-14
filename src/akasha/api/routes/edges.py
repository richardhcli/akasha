"""Edge routes: create + retract (task T4.5, spec §4.11 ``/edges*``).

Wires the ``POST /edges`` / ``DELETE /edges/{id}`` rows of §4.11 to
``kernel/store.py``. Neither verb is marked ∅ (human-only) in the spec
table, so both are open to any authenticated token (``require_auth``);
agent-token proposal rewriting (task T4.6) is wired via
``deps.mutation_gate`` (see ``routes/nodes.py`` for the identical
precedent). For ``POST /edges`` the review item's ``node_id`` is the
would-be edge's ``dst`` because an inbound edge changes that target's
maturity/review-relevant state. ``DELETE /edges/{id}`` looks up the
existing edge's ``dst`` via ``store.get_edge_dst`` and uses that. Both ids
remain recoverable from the canonical proposal payload.

``POST /edges`` reuses the T1.2 ``Edge`` pydantic ``facet_binding``
validator via ``store.create_edge`` (never reimplemented here): a
justification edge type (``supports``/``contradicts``/``depends_on``/
``derived_from``/``cites``) requires a non-``None`` ``facet_binding``
(a facet id or ``"*"``); ``None`` is only legal for ``composes``/
``redirects_to``. A violation raises ``pydantic.ValidationError`` inside
``store.create_edge``, mapped here to ``400 E_INVALID``.

``DELETE /edges/{id}`` is a SOFT retract (``store.retract_edge`` sets
``retracted_at``; the row is never physically deleted — spec §4.4
append-only discipline for the ``edges`` table).

``POST /edges`` also accepts an optional ``facet_span`` (task T7.7, spec
§4.2 facets-from-spans capture): when present, a brand-new facet is
minted on the TARGET (``dst``) node from that highlighted span
(``store.mint_facet_from_span``) BEFORE the edge is created, and the
edge's ``facet_binding`` is forced to that new facet's id (a concrete
id8, never ``'*'``) -- any ``facet_binding`` the caller also passed is
ignored in that case. Omitting ``facet_span`` behaves exactly as before
(the caller's ``facet_binding`` is used verbatim, possibly ``None`` or
``'*'``).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ValidationError

from akasha.api import auth
from akasha.api.deps import ApiError, get_conn, mutation_gate, require_auth
from akasha.kernel import store
from akasha.kernel.store import EdgeNotFoundError, NodeNotFoundError

router = APIRouter(prefix="/v1", tags=["edges"])


class CreateEdgeBody(BaseModel):
    src: str
    dst: str
    edge_type: str
    facet_binding: str | None = None
    provenance: str
    mode: str = "track"
    pinned_commit: str | None = None
    facet_span: str | None = None


@router.post("/edges", status_code=201)
def create_edge(
    payload: CreateEdgeBody,
    request: Request,
    response: Response,
    conn: Any = Depends(get_conn),
    ctx: auth.AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    review = mutation_gate(conn, ctx, request, node_id=payload.dst, payload=payload.model_dump())
    if review is not None:
        response.status_code = 202
        return {"proposed": True, "review": review}
    facet_binding = payload.facet_binding
    try:
        if payload.facet_span is not None:
            # facets-from-spans capture (task T7.7, spec §4.2): mint a new
            # facet on the TARGET node from the highlighted span and force
            # the edge's binding to that concrete facet_id, never '*'.
            facet = store.mint_facet_from_span(
                conn, payload.dst, payload.facet_span, author=ctx.token_id
            )
            facet_binding = facet.facet_id
        edge = store.create_edge(
            conn,
            payload.src,
            payload.dst,
            payload.edge_type,  # type: ignore[arg-type]  # validated by Edge's pydantic model
            facet_binding,
            payload.provenance,
            mode=payload.mode,
            pinned_commit=payload.pinned_commit,
        )
    except NodeNotFoundError as exc:
        raise ApiError(404, "E_NOT_FOUND", str(exc)) from exc
    except ValidationError as exc:
        raise ApiError(400, "E_INVALID", str(exc)) from exc
    return edge.model_dump()


@router.delete("/edges/{edge_id}")
def delete_edge(
    edge_id: str,
    request: Request,
    response: Response,
    conn: Any = Depends(get_conn),
    ctx: auth.AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    try:
        dst = store.get_edge_dst(conn, edge_id)  # existence check + proposal target
    except EdgeNotFoundError as exc:
        raise ApiError(404, "E_NOT_FOUND", str(exc)) from exc
    review = mutation_gate(conn, ctx, request, node_id=dst, payload={"edge_id": edge_id})
    if review is not None:
        response.status_code = 202
        return {"proposed": True, "review": review}
    try:
        store.retract_edge(conn, edge_id)
    except EdgeNotFoundError as exc:
        raise ApiError(404, "E_NOT_FOUND", str(exc)) from exc
    return {"id": edge_id, "retracted": True}
