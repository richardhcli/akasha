"""Edge routes: create + retract (task T4.5, spec §4.11 ``/edges*``).

Wires the ``POST /edges`` / ``DELETE /edges/{id}`` rows of §4.11 to
``kernel/store.py``. Neither verb is marked ∅ (human-only) in the spec
table, so both are open to any authenticated token (``require_auth``);
agent-token proposal rewriting (task T4.6) is wired via
``deps.mutation_gate`` (see ``routes/nodes.py`` for the identical
precedent). For ``POST /edges`` the review item's ``node_id`` is the
would-be edge's ``dst`` (the "narrowest defensible target" for a
create-with-no-prior-row case — same reasoning as T4.6's node-create
placeholder, except ``dst`` already exists here so no placeholder mint is
needed); ``DELETE /edges/{id}`` looks up the existing edge's ``dst`` via
``store.get_edge_dst`` and uses that. Both choices are logged as T4.6
SPEC-QUESTIONs (docs/spec-questions.md) since §4.11/§4.4 don't pin down
which side of an edge "is" the review item's node for this closed-enum
schema.

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
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ValidationError

from akasha.api import auth
from akasha.api.deps import ApiError, get_conn, mutation_gate, require_auth
from akasha.kernel import store
from akasha.kernel.store import EdgeNotFoundError

router = APIRouter(prefix="/v1", tags=["edges"])


class CreateEdgeBody(BaseModel):
    src: str
    dst: str
    edge_type: str
    facet_binding: str | None = None
    provenance: str
    mode: str = "track"
    pinned_commit: str | None = None


@router.post("/edges", status_code=201)
def create_edge(
    payload: CreateEdgeBody,
    request: Request,
    response: Response,
    conn: Any = Depends(get_conn),
    ctx: auth.AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    review = mutation_gate(
        conn, ctx, request, node_id=payload.dst, payload=payload.model_dump()
    )
    if review is not None:
        response.status_code = 202
        return {"proposed": True, "review": review}
    try:
        edge = store.create_edge(
            conn,
            payload.src,
            payload.dst,
            payload.edge_type,  # type: ignore[arg-type]  # validated by Edge's pydantic model
            payload.facet_binding,
            payload.provenance,
            mode=payload.mode,
            pinned_commit=payload.pinned_commit,
        )
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
