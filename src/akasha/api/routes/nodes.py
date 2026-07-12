"""Node routes: CRUD + history/neighborhood/split/merge/vet (task T4.4).

Wires spec §4.11's ``/nodes*`` rows to ``kernel/store.py``. Every endpoint is
authenticated (``require_auth``); ``/vet`` is human-only (``require_human``,
∅). All errors flow through the standard envelope via ``deps.ApiError``.

Agent-token proposal rewriting (task T4.6, spec §4.11: agent mutations to
non-∅ endpoints become ``review_queue`` proposals instead of mutating) is
wired in via ``deps.mutation_gate``, called at the top of every non-∅
mutating route here (create/patch/delete/split/merge) before the real
``kernel/store.py`` mutation. ``/vet`` stays exempt (∅, ``require_human``
rejects agents outright — never proposalized).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ValidationError

from akasha.api import auth
from akasha.api.deps import ApiError, get_conn, mutation_gate, require_auth, require_human
from akasha.kernel import store
from akasha.kernel.model import Facet
from akasha.kernel.store import NeedsRedirectError, NodeNotFoundError

router = APIRouter(prefix="/v1", tags=["nodes"])


class CreateNodeBody(BaseModel):
    node_type: str
    body: str
    facets: list[Facet] | None = None
    task_state: str | None = None
    message: str = ""


class PatchNodeBody(BaseModel):
    body: str | None = None
    facets: list[Facet] | None = None
    change_class: str
    facets_touched: list[str] = []
    message: str = ""


class DeleteNodeBody(BaseModel):
    redirect_to: list[str] | None = None
    tombstone: bool = False


class SplitBody(BaseModel):
    parts: list[dict[str, Any]]


class MergeBody(BaseModel):
    # Other node ids to merge into the path ``{node_id}`` survivor. The full
    # merge_nodes(ids) list is [path id, *ids]; survivor is the path id
    # (store.merge_nodes keeps ids[0]). See the T4.4 SPEC-QUESTION on shape.
    ids: list[str]


def _node_out(conn: Any, node: Any) -> dict[str, Any]:
    """Serialize a Node plus its current maturity (spec §4.11 'node + maturity')."""
    return {**node.model_dump(), "maturity": store.get_maturity(conn, node.id)}


def _not_found(exc: NodeNotFoundError) -> ApiError:
    return ApiError(404, "E_NOT_FOUND", str(exc))


def _proposal_response(response: Any, review: dict[str, Any]) -> dict[str, Any]:
    """Standard T4.6 response shape when ``mutation_gate`` proposalizes a request.

    202 Accepted (the request was accepted for human review, not applied)
    rather than the route's normal success status.
    """
    response.status_code = 202
    return {"proposed": True, "review": review}


@router.get("/nodes/{node_id}")
def get_node(
    node_id: str,
    as_of: str | None = Query(None),
    conn: Any = Depends(get_conn),
    _ctx: auth.AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    try:
        node = store.get_node(conn, node_id, as_of=as_of)
    except NodeNotFoundError as exc:
        raise _not_found(exc) from exc
    return _node_out(conn, node)


@router.post("/nodes", status_code=201)
def create_node(
    payload: CreateNodeBody,
    request: Request,
    response: Response,
    conn: Any = Depends(get_conn),
    ctx: auth.AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    if ctx.token_class == "agent":
        # No existing node yet: mint a collision-free, not-persisted
        # placeholder id for review_queue.node_id (NOT NULL) — see the
        # T4.6 SPEC-QUESTION on store.mint_unassigned_node_id.
        placeholder_id = store.mint_unassigned_node_id(conn)
        review = mutation_gate(
            conn, ctx, request, node_id=placeholder_id, payload=payload.model_dump()
        )
        assert review is not None  # ctx.token_class == "agent" guarantees this
        return _proposal_response(response, review)
    try:
        node = store.create_node(
            conn,
            payload.node_type,
            payload.body,
            payload.facets,
            payload.task_state,
            author=ctx.token_id,
            message=payload.message,
        )
    except (ValueError, ValidationError) as exc:
        raise ApiError(400, "E_INVALID", str(exc)) from exc
    return _node_out(conn, node)


@router.patch("/nodes/{node_id}")
def patch_node(
    node_id: str,
    payload: PatchNodeBody,
    request: Request,
    response: Response,
    conn: Any = Depends(get_conn),
    ctx: auth.AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    try:
        store.get_node(conn, node_id)  # existence check -> 404 before proposalizing/mutating
    except NodeNotFoundError as exc:
        raise _not_found(exc) from exc
    review = mutation_gate(conn, ctx, request, node_id=node_id, payload=payload.model_dump())
    if review is not None:
        return _proposal_response(response, review)
    try:
        node = store.commit_node(
            conn,
            node_id,
            payload.body,
            payload.facets,
            change_class=payload.change_class,
            facets_touched=payload.facets_touched,
            author=ctx.token_id,
            message=payload.message,
        )
    except NodeNotFoundError as exc:
        raise _not_found(exc) from exc
    except (ValueError, ValidationError) as exc:
        raise ApiError(400, "E_INVALID", str(exc)) from exc
    return _node_out(conn, node)


@router.delete("/nodes/{node_id}")
def delete_node(
    node_id: str,
    request: Request,
    response: Response,
    payload: DeleteNodeBody | None = None,
    conn: Any = Depends(get_conn),
    ctx: auth.AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    body = payload or DeleteNodeBody()
    try:
        store.get_node(conn, node_id)  # existence check -> 404 before proposalizing/mutating
    except NodeNotFoundError as exc:
        raise _not_found(exc) from exc
    review = mutation_gate(conn, ctx, request, node_id=node_id, payload=body.model_dump())
    if review is not None:
        return _proposal_response(response, review)
    try:
        store.delete_node(
            conn, node_id, redirect_to=body.redirect_to, tombstone=body.tombstone
        )
    except NeedsRedirectError as exc:
        raise ApiError(409, exc.code, str(exc), {"node_id": exc.node_id}) from exc
    except NodeNotFoundError as exc:
        raise _not_found(exc) from exc
    return {"id": node_id, "deleted": True}


@router.get("/nodes/{node_id}/history")
def node_history(
    node_id: str,
    conn: Any = Depends(get_conn),
    _ctx: auth.AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    try:
        store.get_node(conn, node_id)  # existence check -> 404 for unknown id
    except NodeNotFoundError as exc:
        raise _not_found(exc) from exc
    return {"history": store.history(conn, node_id)}


@router.get("/nodes/{node_id}/neighborhood")
def node_neighborhood(
    node_id: str,
    hops: int = Query(1, ge=0),
    conn: Any = Depends(get_conn),
    _ctx: auth.AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    try:
        store.get_node(conn, node_id)  # existence check -> 404 for unknown id
    except NodeNotFoundError as exc:
        raise _not_found(exc) from exc
    result = store.neighborhood(conn, node_id, hops=hops)
    return {
        "node_ids": result["node_ids"],
        "edges": [edge.model_dump() for edge in result["edges"]],
    }


@router.post("/nodes/{node_id}/split")
def split_node(
    node_id: str,
    payload: SplitBody,
    request: Request,
    response: Response,
    conn: Any = Depends(get_conn),
    ctx: auth.AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    try:
        store.get_node(conn, node_id)  # existence check -> 404 before proposalizing/mutating
    except NodeNotFoundError as exc:
        raise _not_found(exc) from exc
    review = mutation_gate(conn, ctx, request, node_id=node_id, payload=payload.model_dump())
    if review is not None:
        return _proposal_response(response, review)
    try:
        redirect = store.split_node(conn, node_id, payload.parts)
    except NodeNotFoundError as exc:
        raise _not_found(exc) from exc
    except (ValueError, ValidationError, KeyError) as exc:
        raise ApiError(400, "E_INVALID", str(exc)) from exc
    # The inbound-reassignment queue is a M7/T7.6 addition; store.split_node
    # already reassigns live inbound edges to the first successor (zero
    # dangling). Return the redirect map now; the queue layers on later.
    return {"redirect": redirect}


@router.post("/nodes/{node_id}/merge")
def merge_nodes(
    node_id: str,
    payload: MergeBody,
    request: Request,
    response: Response,
    conn: Any = Depends(get_conn),
    ctx: auth.AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    try:
        store.get_node(conn, node_id)  # existence check -> 404 before proposalizing/mutating
    except NodeNotFoundError as exc:
        raise _not_found(exc) from exc
    review = mutation_gate(conn, ctx, request, node_id=node_id, payload=payload.model_dump())
    if review is not None:
        return _proposal_response(response, review)
    try:
        redirect = store.merge_nodes(conn, [node_id, *payload.ids])
    except NodeNotFoundError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise ApiError(400, "E_INVALID", str(exc)) from exc
    return {"redirect": redirect}


@router.post("/nodes/{node_id}/vet")
def vet_node(
    node_id: str,
    conn: Any = Depends(get_conn),
    _ctx: auth.AuthContext = Depends(require_human),
) -> dict[str, Any]:
    try:
        node = store.vet_node(conn, node_id)
    except NodeNotFoundError as exc:
        raise _not_found(exc) from exc
    return _node_out(conn, node)
