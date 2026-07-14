"""Token routes: create/list/revoke (task T4.5, spec §4.11 ``/tokens``, human-only ∅).

Every verb in the spec table's ``GET/POST/DELETE /tokens`` row is marked
"human only ∅" — unlike ``/nodes``/``/edges`` (where only specific verbs like
``/vet`` are ∅), the *entire* ``/tokens`` row is closed to agent-class
tokens, so every route here depends on ``require_human`` (not just
``require_auth``), rejecting agent tokens outright (403 ``E_HUMAN_ONLY``)
rather than proposalizing them (∅ is exempt from T4.6's rewrite-to-proposal
behavior by definition, spec §4.11 intro).

Secret handling reuses ``api/auth.py``'s ``mint_secret``/``hash_secret``/
``format_bearer_token`` verbatim (never a second scheme, per this task's
constraints): a fresh raw secret is minted and returned to the caller
**exactly once**, at creation time, as a ready-to-use bearer value; only
its hash is ever persisted (``kernel/store.py``'s ``create_token``), and no
route here ever re-exposes a raw or hashed secret afterward (``list_tokens``
and the create response's serialized token both omit it).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from akasha.api import auth
from akasha.api.deps import ApiError, get_conn, require_human
from akasha.kernel import store
from akasha.kernel.store import TokenNotFoundError

router = APIRouter(prefix="/v1", tags=["tokens"])


class CreateTokenBody(BaseModel):
    name: str
    token_class: str
    rate_per_min: int | None = None


@router.get("/tokens")
def list_tokens(
    conn: Any = Depends(get_conn),
    _ctx: auth.AuthContext = Depends(require_human),
) -> dict[str, Any]:
    return {"tokens": store.list_tokens(conn)}


@router.post("/tokens", status_code=201)
def create_token(
    payload: CreateTokenBody,
    conn: Any = Depends(get_conn),
    _ctx: auth.AuthContext = Depends(require_human),
) -> dict[str, Any]:
    if payload.token_class not in ("human", "agent"):
        raise ApiError(400, "E_INVALID", "token_class must be 'human' or 'agent'")
    raw_secret = auth.mint_secret()
    token = store.create_token(
        conn,
        payload.name,
        payload.token_class,
        auth.hash_secret(raw_secret),
        rate_per_min=payload.rate_per_min,
    )
    return {**token, "bearer_token": auth.format_bearer_token(token["id"], raw_secret)}


@router.delete("/tokens/{token_id}")
def revoke_token(
    token_id: str,
    conn: Any = Depends(get_conn),
    _ctx: auth.AuthContext = Depends(require_human),
) -> dict[str, Any]:
    try:
        store.revoke_token(conn, token_id)
    except TokenNotFoundError as exc:
        raise ApiError(404, "E_NOT_FOUND", str(exc)) from exc
    return {"id": token_id, "revoked": True}
