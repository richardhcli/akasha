"""FastAPI application factory and the unauthenticated ``/health`` (spec §4.11, §3).

``create_app`` is a factory, not a singleton, so each test builds its own app and the daemon builds
one from a loaded ``Config``. It opens no socket: the bind address (default ``127.0.0.1``, §3) is
kept on ``app.state.config`` for the serving layer. The app ``title`` uses the neutral
``tm-daemon`` prefix, never the product name, because the OpenAPI JSON is snapshotted to
``docs/api-snapshot/openapi.json`` (rule 0.6).
"""

from __future__ import annotations

import sqlite3
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from akasha.api import deps
from akasha.api.routes import edges, metrics, nodes, review, search, sync, sync_roots, tokens
from akasha.config import Config, default_db_path, load_config
from akasha.contract.grammar import CONTRACT_VERSION
from akasha.kernel import store
from akasha.sync.origin import OriginTracker

# Package-relative UI paths (never cwd-relative): api/ -> akasha/ -> ui/{static,templates}.
_UI_DIR = Path(__file__).resolve().parent.parent / "ui"
_STATIC_DIR = _UI_DIR / "static"
_TEMPLATES_DIR = _UI_DIR / "templates"

# CORS allow-list (D4): the spec is silent, so allow exactly the Obsidian desktop app's Electron
# origin (``app://obsidian.md``, the plugin's only browser-embedded client), never a wildcard: this
# daemon carries bearer tokens and §3 establishes a localhost-only posture. The web UI is
# same-origin, so it never needs the list.
_CORS_ALLOWED_ORIGINS = ["app://obsidian.md"]


def app_version() -> str:
    """Installed package version, read from distribution metadata so it cannot drift from
    ``pyproject.toml``. The ``PackageNotFoundError`` fallback only fires if ``akasha`` is not
    installed as a distribution.
    """
    try:
        return _pkg_version("akasha")
    except PackageNotFoundError:  # pragma: no cover - always installed under uv/CI
        return "0.0.0+unknown"


def create_app(config: Config | None = None, conn: sqlite3.Connection | None = None) -> FastAPI:
    """Build the daemon's FastAPI app: ``/health`` plus the ``/v1`` routes.

    ``config`` defaults to ``load_config()`` and is stored on ``app.state.config``. ``conn`` is the
    shared WAL connection (``app.state.conn``): tests inject a migrated one, otherwise the factory
    opens ``config.db_path`` with ``check_same_thread=False`` and migrates. Writes still go through
    ``kernel/store.py`` (rule 0.4).
    """
    cfg = config if config is not None else load_config()

    app = FastAPI(title="tm-daemon API", version=app_version())
    app.state.config = cfg

    # Debug-plan D4: without this, every fetch from the Obsidian plugin's
    # renderer origin fails the browser's CORS preflight before the request
    # ever reaches a route -- confirmed live against a real Obsidian vault
    # (status bar stuck on "offline" despite a correct URL/token). No
    # credentials (cookies) are ever used -- auth is a bearer token in the
    # `Authorization` header -- so `allow_credentials` stays False.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_CORS_ALLOWED_ORIGINS,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

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

    # Task T13.3: one OriginTracker for the app's whole lifetime -- shared by
    # every request-path re-projection (routes/nodes.py's mutating endpoints)
    # so a hub-side write's echo is correctly suppressed by the SAME live
    # watcher daemon.serve wires up (see that module's docstring for why a
    # per-request tracker would silently break echo suppression / cross-file
    # move tracking: it must be a single, long-lived instance, never
    # reconstructed per call).
    app.state.origin_tracker = OriginTracker()

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

    # Dashboard view (T10.1 / spec §7, §9 story 6): same static-shell pattern
    # as /node -- no auth (the /v1/metrics fetch driven by app.js is
    # authenticated), excluded from OpenAPI so the /v1 contract snapshot
    # stays unchanged.
    @app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
    def ui_dashboard() -> HTMLResponse:  # pyright: ignore[reportUnusedFunction]
        content = (_TEMPLATES_DIR / "dashboard.html").read_bytes()
        return HTMLResponse(content=content, status_code=200)

    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    return app
