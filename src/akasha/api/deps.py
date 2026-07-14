"""Shared FastAPI dependencies + the standard error envelope (task T4.4).

Approved app plumbing (see task-status T4.4 note): a single shared WAL
``sqlite3`` connection lives on ``app.state.conn`` (opened in ``app.py`` with
``check_same_thread=False``); ``get_conn`` hands it to routes. ``require_auth``
wraps ``auth.authenticate`` (T4.1) and records the audit row for every
mutating request (T4.2); ``require_human`` enforces the human-only (∅)
endpoints (spec §4.11). Every failure is rendered through the spec §4.11
envelope ``{"error": {"code", "message", "detail"}}`` by
``register_error_handlers`` — this module is a leaf (imports only auth/store),
so ``app.py`` and ``routes/*`` can both depend on it without a cycle.

``mutation_gate`` (task T4.6) is the shared cross-cutting dependency that
rewrites agent-class mutations on non-∅ endpoints into ``review_queue``
proposals instead of letting them mutate (spec §4.11: "agent-class tokens:
mutating endpoints are rewritten into proposals... unless the endpoint is
marked ∅"); ∅ endpoints never call it — they use ``require_human`` instead.
"""

from __future__ import annotations

import sqlite3
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


def get_conn(request: Request) -> sqlite3.Connection:
    """The daemon's single shared WAL connection (set on app.state in app.py)."""
    return request.app.state.conn


def require_auth(
    request: Request, conn: sqlite3.Connection = Depends(get_conn)
) -> auth.AuthContext:
    """Authenticate the Bearer token; audit the request if it mutates.

    Maps every ``auth.AuthError`` to the standard envelope: rate-limit →
    429, everything else (malformed / unknown / bad secret / revoked) → 401.
    On success, records exactly one ``audit_log`` row for a mutating HTTP
    method (T4.2 ``record_mutation`` no-ops for reads), then returns the
    ``AuthContext`` so routes can branch on ``token_class`` (human vs agent).
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
    """Agent-token proposal rewrite for non-∅ mutating endpoints (task T4.6, spec §4.11).

    Call this from every non-∅ mutating route (``POST/PATCH/DELETE /nodes``,
    ``POST/DELETE /edges``) BEFORE performing the real ``kernel/store.py``
    mutation:

    * **Human tokens** — returns ``None``; the caller proceeds to mutate as
      normal (unchanged behavior).
    * **Agent tokens** — does NOT mutate. Instead enqueues exactly one
      ``review_queue`` row with ``cause_kind="proposal"`` via
      ``kernel/store.py``'s ``enqueue_review`` (rule 0.4 — the raw INSERT
      lives in ``store.py``, never here), recording the would-be request
      (HTTP method, path, JSON body) as canonical JSON (spec §4.3
      ``canonical_json``; never pickle/eval) in ``cause_ref``. Returns that
      review row so the caller renders it as the response instead of the
      normal mutation response (routes use this to short-circuit before
      calling the mutating ``store`` function).

    ``node_id`` becomes the review item's affected node for a route targeting
    an existing node/edge (edge proposals use ``dst``). For ``POST /nodes``,
    pass ``None``: an unapproved proposal does not reserve a node identity;
    the review row id correlates it until T7.5 mints the real node on approval.

    Never call this from a ∅ (human-only) endpoint — those depend on
    ``require_human`` instead, which rejects agent tokens outright (403
    ``E_HUMAN_ONLY``) rather than proposalizing them (spec §4.11 intro:
    "...unless the endpoint is marked ∅").
    """
    if ctx.token_class != "agent":
        return None
    cause_ref = canonical_json(
        {"method": request.method, "path": request.url.path, "body": payload}
    ).decode("utf-8")
    return store.enqueue_review(conn, node_id, "proposal", cause_ref=cause_ref)
