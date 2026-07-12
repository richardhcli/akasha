"""Integration tests for the app factory + /health (task T4.3, spec §4.11)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from akasha.api import schemas
from akasha.api.app import app_version, create_app
from akasha.config import Config
from akasha.contract.grammar import CONTRACT_VERSION
from akasha.kernel import model, store


def _app(config: Config | None = None):
    """Build an app with an injected in-memory DB so tests never touch $HOME."""
    conn = store.connect(":memory:", check_same_thread=False)
    store.run_migrations(conn)
    return create_app(config, conn=conn)


def test_health_returns_version_and_contract_version():
    client = TestClient(_app())
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"] == app_version()
    assert body["contract_version"] == CONTRACT_VERSION


def test_health_requires_no_auth():
    """No Authorization header at all still returns 200 (spec §4.11 'no auth')."""
    client = TestClient(_app())
    resp = client.get("/health")  # deliberately no headers
    assert resp.status_code == 200


def test_app_binds_localhost_only():
    """The factory records the localhost bind address (spec §3)."""
    app = _app(Config())
    assert app.state.config.bind == "127.0.0.1"


def test_app_title_carries_no_product_name():
    """Rebrand invariant (rule 0.6): OpenAPI title uses the neutral prefix."""
    app = _app(Config())
    assert "akasha" not in app.title.lower()
    assert app.title == "tm-daemon API"


def test_schemas_reexport_kernel_models_without_divergence():
    """api/schemas.py re-exports kernel model types verbatim (spec §8)."""
    assert schemas.Node is model.Node
    assert schemas.Edge is model.Edge
    assert schemas.Facet is model.Facet
    assert schemas.JUSTIFICATION_EDGE_TYPES is model.JUSTIFICATION_EDGE_TYPES
