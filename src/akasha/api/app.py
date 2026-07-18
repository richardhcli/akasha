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
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from akasha.api import deps
from akasha.api.routes import edges, metrics, nodes, review, search, sync, sync_roots, tokens
from akasha.config import Config, default_db_path, load_config
from akasha.contract.grammar import CONTRACT_VERSION
from akasha.kernel import store

# Package-relative UI paths (never cwd-relative): api/ -> akasha/ -> ui/{static,templates}.
_UI_DIR = Path(__file__).resolve().parent.parent / "ui"
_STATIC_DIR = _UI_DIR / "static"
_TEMPLATES_DIR = _UI_DIR / "templates"


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


def create_app(config: Config | None = None, conn: sqlite3.Connection | None = None) -> FastAPI:
    """Build the daemon's FastAPI app, wiring ``/health`` and the ``/v1`` routes.

    ``config`` defaults to ``load_config()`` (per-OS default location); the
    resolved ``Config`` is stashed on ``app.state.config`` so the serving
    layer binds ``config.bind`` (``127.0.0.1`` by default, spec §3).

    ``conn`` is the single shared WAL connection the routes use
    (``app.state.conn``). Tests inject a migrated tmp-file connection; when
    omitted, the factory opens ``config.db_path`` (default
    ``tm-daemon/store.db``) with ``check_same_thread=False`` and runs
    migrations. All SQLite writes still route through ``kernel/store.py``
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
        # Production: request handling uses a fresh connection PER REQUEST from
        # this path (``deps.get_conn``) — WAL permits concurrent readers + one
        # writer, so the UI's parallel fetches are safe. Sharing one connection
        # across the ASGI threadpool corrupts reads under concurrency
        # (SPEC-QUESTION T8.5b, amending spec §3). ``app.state.conn`` below is
        # the startup connection, used ONLY by the pre-serving startup reconcile
        # (``daemon.py``), never for request handling.
        app.state.db_path = str(db_path)
    else:
        # Test/embedded injection: a single migrated connection is shared and
        # driven sequentially (TestClient), so it is safe; ``get_conn`` yields
        # it directly (``db_path is None`` selects that branch).
        app.state.db_path = None
    app.state.conn = conn

    deps.register_error_handlers(app)
    app.include_router(nodes.router)
    app.include_router(edges.router)
    app.include_router(search.router)
    app.include_router(tokens.router)
    app.include_router(sync_roots.router)
    app.include_router(sync.router)
    app.include_router(review.router)
    app.include_router(metrics.router)

    # Operational liveness is intentionally root-level and unauthenticated;
    # authenticated application resources are versioned under /v1.
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

    # UI shell (T8.1 / spec §4.13): static HTML, no auth, excluded from OpenAPI
    # so the /v1 contract snapshot stays unchanged.
    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def ui_shell() -> HTMLResponse:  # pyright: ignore[reportUnusedFunction]
        content = (_TEMPLATES_DIR / "base.html").read_bytes()
        return HTMLResponse(content=content, status_code=200)

    # Node view (T8.2 / spec §4.13): same pattern as the shell above -- a
    # static HTML page served as-is, no auth (the underlying /v1 data
    # fetches driven by app.js are authenticated), excluded from OpenAPI so
    # the /v1 contract snapshot stays unchanged.
    @app.get("/node", response_class=HTMLResponse, include_in_schema=False)
    def ui_node() -> HTMLResponse:  # pyright: ignore[reportUnusedFunction]
        content = (_TEMPLATES_DIR / "node.html").read_bytes()
        return HTMLResponse(content=content, status_code=200)

    # Review view (T8.3 / spec §4.13): same static-shell pattern as /node --
    # no auth (the /v1/review fetches driven by app.js are authenticated),
    # excluded from OpenAPI so the /v1 contract snapshot stays unchanged.
    @app.get("/review", response_class=HTMLResponse, include_in_schema=False)
    def ui_review() -> HTMLResponse:  # pyright: ignore[reportUnusedFunction]
        content = (_TEMPLATES_DIR / "review.html").read_bytes()
        return HTMLResponse(content=content, status_code=200)

    # Search view (T8.4 / spec §4.13): same static-shell pattern as /node --
    # no auth (the /v1/search fetch driven by app.js is authenticated),
    # excluded from OpenAPI so the /v1 contract snapshot stays unchanged.
    @app.get("/search", response_class=HTMLResponse, include_in_schema=False)
    def ui_search() -> HTMLResponse:  # pyright: ignore[reportUnusedFunction]
        content = (_TEMPLATES_DIR / "search.html").read_bytes()
        return HTMLResponse(content=content, status_code=200)

    # Sync view (T8.4 / spec §4.13): same static-shell pattern as /node --
    # no auth (the /v1/sync/status fetch driven by app.js is authenticated),
    # excluded from OpenAPI so the /v1 contract snapshot stays unchanged.
    @app.get("/sync", response_class=HTMLResponse, include_in_schema=False)
    def ui_sync() -> HTMLResponse:  # pyright: ignore[reportUnusedFunction]
        content = (_TEMPLATES_DIR / "sync.html").read_bytes()
        return HTMLResponse(content=content, status_code=200)

    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    return app
