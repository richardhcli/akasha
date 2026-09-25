"""Shared FastAPI dependencies and the standard error envelope.

``get_conn`` gives each request a WAL connection: a FRESH one per request in production (spec §3,
SPEC-QUESTION T8.5b, vision F14: one connection shared across the ASGI threadpool corrupts reads),
while callers that inject one via ``create_app(conn=...)`` keep the shared ``app.state.conn``.
``require_auth`` wraps ``auth.authenticate`` and audits every mutating request (T4.2);
``require_human`` enforces the human-only (∅) endpoints (§4.11). Every failure is rendered through
the §4.11 envelope ``{"error": {"code", "message", "detail"}}`` by ``register_error_handlers``.
``mutation_gate`` (T4.6) rewrites agent-class mutations on non-∅ endpoints into ``review_queue``
proposals; ∅ endpoints use ``require_human`` instead. A leaf module (imports only auth/store), so
``app.py`` and ``routes/*`` can both depend on it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from akasha.api import auth
from akasha.kernel import store
from akasha.kernel.canonical import canonical_json


class ApiError(Exception):
    """An error to render as the spec §4.11 envelope with a fixed HTTP status.

    Routes raise this (rather than FastAPI's ``HTTPException``) so every
    error body is the exact ``{"error": {code, message, detail}}`` shape the
    spec mandates. ``code`` is the stable machine code (e.g.
    ``E_NEEDS_REDIRECT``); ``detail`` is an optional JSON object.
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.detail: dict[str, Any] = detail or {}
        super().__init__(message)


def _envelope(status_code: int, code: str, message: str, detail: dict[str, Any]) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "detail": detail}},
    )


def register_error_handlers(app: FastAPI) -> None:
    """Register handlers that render errors as the spec §4.11 envelope."""

    @app.exception_handler(ApiError)
    async def _handle_api_error(  # pyright: ignore[reportUnusedFunction]
        _request: Request, exc: ApiError
    ) -> JSONResponse:
        return _envelope(exc.status_code, exc.code, exc.message, exc.detail)

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(  # pyright: ignore[reportUnusedFunction]
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Wrap FastAPI's own 422 body in the standard envelope so clients
        # only ever parse one error shape.
        return _envelope(
            422,
            "E_INVALID",
            "request validation failed",
            {"errors": jsonable_encoder(exc.errors())},
        )


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    """Yield the DB connection for one request. Production (``app.state.db_path`` set): a fresh WAL
    connection, closed when the request ends (concurrent readers plus one writer are safe; a shared
    connection is not). Injected (``db_path is None``): the shared migrated connection, driven
    sequentially.
    """
    db_path = getattr(request.app.state, "db_path", None)
    if db_path is None:
        yield request.app.state.conn
        return
    conn = store.connect(db_path, check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


def require_auth(
    request: Request, conn: sqlite3.Connection = Depends(get_conn)
) -> auth.AuthContext:
    """Authenticate the Bearer token and audit the request if it mutates. Every ``auth.AuthError``
    maps to the standard envelope: rate limit -> 429, anything else -> 401. Returns the
    ``AuthContext`` so routes can branch on ``token_class``.
    """
    header = request.headers.get("authorization")
    parts = header.split(None, 1) if header else []
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise ApiError(401, "E_AUTH", "missing or malformed 'Authorization: Bearer <token>' header")
    try:
        ctx = auth.authenticate(conn, parts[1])
    except auth.RateLimitExceededError as exc:
        raise ApiError(429, exc.code, str(exc)) from exc
    except auth.AuthError as exc:
        raise ApiError(401, exc.code, str(exc)) from exc

    # Audit every authenticated *mutating* request exactly once (T4.2).
    auth.record_mutation(conn, request.method, f"{request.method} {request.url.path}", ctx)
    return ctx


def require_human(ctx: auth.AuthContext = Depends(require_auth)) -> auth.AuthContext:
    """Reject agent-class tokens on human-only (∅) endpoints (spec §4.11)."""
    if ctx.token_class != "human":
        raise ApiError(403, "E_HUMAN_ONLY", "this endpoint accepts human-class tokens only")
    return ctx


def mutation_gate(
    conn: sqlite3.Connection,
    ctx: auth.AuthContext,
    request: Request,
    *,
    node_id: str | None,
    payload: Any = None,
) -> dict[str, Any] | None:
    """Agent-token proposal rewrite for non-∅ mutating endpoints (T4.6, spec §4.11).

    Call it from every non-∅ mutating route BEFORE the real store mutation. A human token returns
    ``None`` and the caller mutates as usual. An agent token does NOT mutate: it enqueues one
    ``review_queue`` row (``cause_kind="proposal"``, via ``store.enqueue_review``) recording the
    would-be request (method, path, JSON body) as canonical JSON, and returns that row for the
    route to render as its response.

    ``node_id`` is the affected node for a route on an existing node or edge (edge proposals use
    ``dst``). For ``POST /nodes`` pass ``None``: an unapproved proposal reserves no identity, and
    the review id correlates it until T7.5 mints the node on approval. Never call it from a ∅
    endpoint: those use ``require_human``, which rejects agent tokens outright (403
    ``E_HUMAN_ONLY``).
    """
    if ctx.token_class != "agent":
        return None
    cause_ref = canonical_json(
        {"method": request.method, "path": request.url.path, "body": payload}
    ).decode("utf-8")
    return store.enqueue_review(conn, node_id, "proposal", cause_ref=cause_ref)
