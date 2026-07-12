"""FastAPI application factory + unauthenticated ``/health`` (task T4.3).

Spec §4.11 (``GET /health``: liveness, version, contract version, *no auth*),
§3 (the daemon binds ``127.0.0.1`` only), §8 (``schemas.py`` is the
re-exportable schema surface — see ``api/schemas.py``).

``create_app`` is a *factory* (not a module-level singleton) so tests can
build an app per-case and T4.9's daemon lifecycle can construct it with a
loaded ``Config``. The factory does not itself open a socket; binding to
``config.bind`` (default ``127.0.0.1``) happens when T4.9 hands this app to
uvicorn. The intended bind address is stored on ``app.state.config`` so the
serving layer (and this task's test) reads a single source of truth rather
than re-deriving the localhost invariant.

Rebrand invariant (build-plan rule 0.6): the product name must never appear
in an on-disk format, and the served OpenAPI JSON is snapshotted to
``docs/api-snapshot/openapi.json`` (T4.7), so the app ``title`` uses the
neutral ``tm-daemon`` prefix, never the product name.
"""

from __future__ import annotations

import sqlite3
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path

from fastapi import FastAPI

from akasha.api import deps
from akasha.api.routes import edges, nodes, search, tokens, vaults
from akasha.config import Config, default_db_path, load_config
from akasha.contract.grammar import CONTRACT_VERSION
from akasha.kernel import store


def app_version() -> str:
    """Installed package version (``pyproject`` ``version``), or a marker.

    Read from installed distribution metadata rather than hard-coded so the
    reported version can never silently drift from ``pyproject.toml``. The
    ``PackageNotFoundError`` fallback only fires if ``akasha`` isn't installed
    as a distribution (not the case under ``uv``/CI), so it's excluded from
    coverage.
    """
    try:
        return _pkg_version("akasha")
    except PackageNotFoundError:  # pragma: no cover - always installed under uv/CI
        return "0.0.0+unknown"


def create_app(
    config: Config | None = None, conn: sqlite3.Connection | None = None
) -> FastAPI:
    """Build the daemon's FastAPI app, wiring ``/health`` and the ``/v1`` routes.

    ``config`` defaults to ``load_config()`` (per-OS default location); the
    resolved ``Config`` is stashed on ``app.state.config`` so the serving
    layer binds ``config.bind`` (``127.0.0.1`` by default, spec §3).

    ``conn`` is the single shared WAL connection the routes use
    (``app.state.conn``). Tests inject a migrated tmp-file connection; when
    omitted, the factory opens ``config.db_path`` (default
    ``tm-daemon/store.db``) with ``check_same_thread=False`` — see the T4.4
    SPEC-QUESTION on the DB path and the shared-connection tradeoff — and
    runs migrations. All SQLite writes still route through ``kernel/store.py``
    (rule 0.4).
    """
    cfg = config if config is not None else load_config()

    app = FastAPI(title="tm-daemon API", version=app_version())
    app.state.config = cfg

    if conn is None:
        db_path = cfg.db_path if cfg.db_path is not None else default_db_path()
        # First run creates the neutral tm-daemon dir (spec §3) if absent.
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = store.connect(db_path, check_same_thread=False)
        store.run_migrations(conn)
    app.state.conn = conn

    deps.register_error_handlers(app)
    app.include_router(nodes.router)
    app.include_router(edges.router)
    app.include_router(search.router)
    app.include_router(tokens.router)
    app.include_router(vaults.router)

    # SPEC-QUESTION (T4.3): §4.11's intro says "All under /v1" but the endpoint
    # table cell for health writes the literal path "/health" (and health
    # checks are conventionally unversioned + unauthenticated). Narrowest
    # reading: mount at the literal "/health" the table shows; the versioned
    # routers (T4.4+) mount under "/v1". Logged in docs/spec-questions.md.
    @app.get("/health")
    def health() -> dict[str, str | int]:  # pyright: ignore[reportUnusedFunction]
        # No auth dependency here: /health is explicitly unauthenticated
        # (spec §4.11 "no auth"). Global auth wiring (T4.4+) must keep /health
        # exempt.
        return {
            "status": "ok",
            "version": app_version(),
            "contract_version": CONTRACT_VERSION,
        }

    return app
