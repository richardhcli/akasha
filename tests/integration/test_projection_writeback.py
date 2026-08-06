"""Projection write-back integration test (task T13.3/T13.6, spec §4.8/§1/§4.11).

Closes the audit's flagship gap: after a mutation through ``/v1/nodes*``
(the API surface every non-vault client -- CLI, Web UI, plugin, an
agent-approved proposal -- ultimately goes through), the managed vault file
that projects the affected node must be refreshed WITHIN THE SAME REQUEST,
never waiting for a daemon restart, a filesystem event, or a manual
``POST /v1/sync/rescan``.

Drives a REAL managed file through a REAL app (a genuine ``TestClient``
against ``api.app.create_app``, a real temp-directory file, a real sqlite
connection) -- no direct calls into ``sync.reconcile`` bypassing HTTP. See
``docs/spec-questions.md``'s T13.3 entry for the binding narrowest reading
this test locks in (only the ``/v1/nodes*`` mutating rows).

Task T13.6 extends this same file (never a second one) with the review-
resolution surface: ``POST /v1/review/{id}/resolve``'s ``revised``/
``still_holds`` resolutions, driven for real over HTTP against a real
managed file. An approved create-proposal is asserted at the layer where
proposal approval actually lives (``tms.review.approve_proposal`` has no
HTTP route -- see the ``# SPEC-QUESTION`` at the top of
``src/akasha/api/routes/review.py``), not over HTTP.

Fixture helpers mirror ``tests/integration/test_api.py``'s own ``api``
fixture (copied, not imported -- ``tests/`` is not a package, no
``__init__.py`` anywhere under it, matching every sibling integration test's
existing convention).
"""

from __future__ import annotations

import logging
import sqlite3

import pytest
from fastapi.testclient import TestClient

from akasha.api import auth
from akasha.api.app import create_app
from akasha.kernel import ids, store
from akasha.kernel.canonical import canonical_json, canonicalize_text
from akasha.kernel.ids import contract_anchor


@pytest.fixture(autouse=True)
def _reset_rate_limit_state():
    auth._call_log.clear()
    yield
    auth._call_log.clear()


def _insert_token(conn: sqlite3.Connection, token_id: str, secret: str, cls: str) -> None:
    conn.execute(
        "INSERT INTO tokens (id, name, class, secret_hash, rate_per_min, created_at, "
        "revoked_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            token_id,
            token_id,
            cls,
            auth.hash_secret(secret),
            None,
            "2026-01-01T00:00:00.000000+00:00",
            None,
        ),
    )
    conn.commit()


@pytest.fixture
def api(tmp_path):
    # The daemon's own db/wal files live under a SEPARATE directory from any
    # vault a test registers as a sync root -- otherwise "no files written
    # anywhere under the root" (Steps (d)) would spuriously see the
    # daemon's own db/wal churn. A real daemon never nests its db under a
    # user's vault either.
    db_dir = tmp_path / "_db"
    db_dir.mkdir()
    conn = store.connect(db_dir / "api.db", check_same_thread=False)
    store.run_migrations(conn)
    human_secret = auth.mint_secret()
    _insert_token(conn, "humantoken", human_secret, "human")
    client = TestClient(create_app(conn=conn))
    human_bearer = auth.format_bearer_token("humantoken", human_secret)
    return {
        "client": client,
        "conn": conn,
        "human": {"Authorization": f"Bearer {human_bearer}"},
    }


def _managed(body: str) -> str:
    return canonicalize_text(f"---\ntm: 1\n---\n{body}")


@pytest.fixture
def vault_dir(tmp_path):
    """A vault root SIBLING to (never nested under) the fixture's own db/wal
    dir, so "no files written anywhere under the root" (Steps (d)) never
    spuriously trips over the daemon's own db/wal churn.
    """
    d = tmp_path / "vault"
    d.mkdir()
    return d


def _seed_filed_task(
    client, headers, vault_dir, *, body: str = "original body"
) -> tuple[str, object]:
    """Create a task node, register a sync root, and reconcile a real vault
    file that already contains an anchored line for it -- a genuine
    ``POST /v1/sync/rescan`` cycle, not a hand-seeded ``base_store`` shortcut.

    Returns ``(node_id, path)``.
    """
    node = client.post(
        "/v1/nodes",
        json={"node_type": "task", "body": body, "task_state": "open"},
        headers=headers,
    ).json()
    x = node["id"]
    client.post(
        "/v1/sync/roots", json={"name": "vault", "root_path": str(vault_dir)}, headers=headers
    )
    path = vault_dir / "note.md"
    path.write_text(_managed(f"- [ ] {body} {contract_anchor(x)}\n"), encoding="utf-8")
    rescan = client.post("/v1/sync/rescan", headers=headers)
    assert rescan.status_code == 200
    assert rescan.json()["files_reconciled"] == 1
    assert contract_anchor(x) in path.read_text(encoding="utf-8")
    return x, path


def test_patch_body_writes_back_to_the_real_managed_file_same_request(api, vault_dir):
    client, h = api["client"], api["human"]
    x, path = _seed_filed_task(client, h, vault_dir)

    resp = client.patch(
        f"/v1/nodes/{x}",
        json={"body": "revised body", "change_class": "patch", "facets_touched": []},
        headers=h,
    )
    assert resp.status_code == 200

    on_disk = path.read_text(encoding="utf-8")
    assert "revised body" in on_disk
    assert "original body" not in on_disk
    assert contract_anchor(x) in on_disk


def test_patch_task_state_done_flips_the_real_checkbox_same_request(api, vault_dir):
    client, h = api["client"], api["human"]
    x, path = _seed_filed_task(client, h, vault_dir)

    resp = client.patch(
        f"/v1/nodes/{x}",
        json={"task_state": "done", "change_class": "patch", "facets_touched": []},
        headers=h,
    )
    assert resp.status_code == 200

    on_disk = path.read_text(encoding="utf-8")
    assert "- [x]" in on_disk
    assert "- [ ]" not in on_disk


def test_writeback_is_canonical_and_lf_only(api, vault_dir):
    client, h = api["client"], api["human"]
    x, path = _seed_filed_task(client, h, vault_dir)

    resp = client.patch(
        f"/v1/nodes/{x}",
        json={"body": "canonical check", "change_class": "patch", "facets_touched": []},
        headers=h,
    )
    assert resp.status_code == 200

    raw_bytes = path.read_bytes()
    assert b"\r" not in raw_bytes
    on_disk = path.read_text(encoding="utf-8")
    assert canonicalize_text(on_disk) == on_disk


def test_node_in_no_managed_file_writes_nothing_under_the_root(api, vault_dir):
    client, h = api["client"], api["human"]
    client.post(
        "/v1/sync/roots", json={"name": "vault", "root_path": str(vault_dir)}, headers=h
    )
    node = client.post(
        "/v1/nodes", json={"node_type": "claim", "body": "unfiled"}, headers=h
    ).json()

    before = sorted(str(p) for p in vault_dir.rglob("*") if p.is_file())
    assert before == []

    resp = client.patch(
        f"/v1/nodes/{node['id']}",
        json={"body": "still unfiled", "change_class": "patch", "facets_touched": []},
        headers=h,
    )
    assert resp.status_code == 200

    after = sorted(str(p) for p in vault_dir.rglob("*") if p.is_file())
    assert after == []


def test_review_queue_is_unchanged_by_the_projection_itself(api, vault_dir):
    client, h, conn = api["client"], api["human"], api["conn"]
    x, path = _seed_filed_task(client, h, vault_dir)

    before = len(store.find_open_reviews(conn))
    resp = client.patch(
        f"/v1/nodes/{x}",
        json={"body": "line two", "change_class": "patch", "facets_touched": []},
        headers=h,
    )
    assert resp.status_code == 200
    after = len(store.find_open_reviews(conn))
    assert after == before


def test_projection_failure_never_fails_the_mutation(api, vault_dir, monkeypatch, caplog):
    """T13.3 Steps (f): the single most likely silent regression -- assert
    the swallow, not just claim it. Forces ``project_node_change`` to raise
    and asserts the mutation still returns its normal 2xx, the node is
    still committed, and the failure was logged.
    """
    client, h = api["client"], api["human"]
    x, path = _seed_filed_task(client, h, vault_dir)

    def _boom(*_args: object, **_kwargs: object) -> list[str]:
        raise RuntimeError("synthetic projection failure (T13.3 swallow test)")

    monkeypatch.setattr("akasha.sync.reconcile.project_node_change", _boom)

    with caplog.at_level(logging.ERROR, logger="akasha"):
        resp = client.patch(
            f"/v1/nodes/{x}",
            json={"body": "still committed", "change_class": "patch", "facets_touched": []},
            headers=h,
        )

    # The mutation itself must succeed -- a projection error is best-effort,
    # never surfaced as an HTTP error.
    assert resp.status_code == 200
    assert resp.json()["body"].strip() == "still committed"

    # The node's commit is real and durable, independent of the swallowed
    # projection failure.
    committed = client.get(f"/v1/nodes/{x}", headers=h).json()
    assert committed["body"].strip() == "still committed"

    # The vault file was NOT rewritten this cycle (the only writer --
    # project_node_change -- was made to raise before doing anything).
    on_disk = path.read_text(encoding="utf-8")
    assert "original body" in on_disk

    # The failure was logged, not silently discarded.
    assert any(
        "projection writeback failed" in record.getMessage() for record in caplog.records
    )


# --- T13.6: review-resolution surface ---------------------------------------


def _seed_filed_definition(
    client, headers, vault_dir, *, body: str = "original subscriber body"
) -> tuple[str, object]:
    """Create a (non-task) node, register a sync root, and reconcile a real
    vault file that already contains an anchored paragraph line for it --
    mirrors ``_seed_filed_task`` but for a plain (non-checkbox) node, since
    the facet_break subscriber below is a ``definition``, not a ``task``.

    Returns ``(node_id, path)``.
    """
    node = client.post(
        "/v1/nodes", json={"node_type": "definition", "body": body}, headers=headers
    ).json()
    x = node["id"]
    client.post(
        "/v1/sync/roots", json={"name": "vault", "root_path": str(vault_dir)}, headers=headers
    )
    path = vault_dir / "def.md"
    path.write_text(_managed(f"{body} {contract_anchor(x)}\n"), encoding="utf-8")
    rescan = client.post("/v1/sync/rescan", headers=headers)
    assert rescan.status_code == 200
    assert rescan.json()["files_reconciled"] == 1
    assert contract_anchor(x) in path.read_text(encoding="utf-8")
    return x, path


def test_review_resolve_revised_writes_the_revised_body_back_same_request(api, vault_dir):
    """A real facet_break review, resolved 'revised' over HTTP, lands in the
    managed vault file within the same request -- canonically, LF-only.
    """
    from akasha.kernel.model import Facet

    client, h, conn = api["client"], api["human"], api["conn"]
    sub_id, path = _seed_filed_definition(client, h, vault_dir)

    target_facet = ids.mint()
    target = store.create_node(
        conn,
        "definition",
        "target definition",
        facets=[Facet(facet_id=target_facet, name="tdef", span="tdef", version=1)],
    )
    store.create_edge(
        conn,
        src=sub_id,
        dst=target.id,
        edge_type="supports",
        facet_binding=target_facet,
        provenance="human",
    )
    # Major commit touching the bound facet -> opens a facet_break review on
    # the subscriber (spec §4.9/§4.10) -- same recipe as
    # tests/integration/test_tms.py's ``_trigger_facet_break``.
    store.commit_node(
        conn,
        target.id,
        facets=[Facet(facet_id=target_facet, name="tdef-broken", span="tdef", version=2)],
        change_class="major",
        facets_touched=[target_facet],
        author="test",
    )
    reviews = store.find_open_reviews(conn, node_id=sub_id, cause_kind="facet_break")
    assert len(reviews) == 1
    review_id = reviews[0]["id"]

    resp = client.post(
        f"/v1/review/{review_id}/resolve",
        json={
            "resolution": "revised",
            "new_body": "revised subscriber body",
            "change_class": "minor",
            "facets_touched": [],
        },
        headers=h,
    )
    assert resp.status_code == 200

    on_disk = path.read_text(encoding="utf-8")
    assert "revised subscriber body" in on_disk
    assert "original subscriber body" not in on_disk
    assert contract_anchor(sub_id) in on_disk

    raw_bytes = path.read_bytes()
    assert b"\r" not in raw_bytes
    assert canonicalize_text(on_disk) == on_disk

    # The review itself is resolved, not left dangling.
    open_after = store.find_open_reviews(conn, node_id=sub_id, cause_kind="facet_break")
    assert open_after == []


def test_review_resolve_still_holds_leaves_the_file_byte_identical(api, vault_dir):
    client, h, conn = api["client"], api["human"], api["conn"]
    x, path = _seed_filed_task(client, h, vault_dir)

    # Establish the pre-state THROUGH the projection path (not the
    # hand-written seed file) so a still_holds no-op can't be mistaken for
    # "happened to already match": a real prior mutation forces a real
    # project_node_change write, and we snapshot exactly those bytes.
    resp = client.patch(
        f"/v1/nodes/{x}",
        json={"body": "settled body", "change_class": "patch", "facets_touched": []},
        headers=h,
    )
    assert resp.status_code == 200
    before = path.read_bytes()

    review = store.enqueue_review(conn, x, "conflict", cause_ref="unrelated conflict")

    resp = client.post(
        f"/v1/review/{review['id']}/resolve",
        json={"resolution": "still_holds"},
        headers=h,
    )
    assert resp.status_code == 200
    assert resp.json()["resolution"] == "still_holds"

    after = path.read_bytes()
    assert after == before


def test_review_resolve_approved_create_proposal_projects_nothing(api, vault_dir):
    """A freshly-approved create-proposal mints a node that belongs to no
    managed file -- projecting it must be a genuine no-op (unfiled stays
    unfiled). ``tms.review.approve_proposal`` has no HTTP route (see the
    module-level ``# SPEC-QUESTION`` in ``src/akasha/api/routes/review.py``),
    so this asserts the invariant directly at the layer where proposal
    approval actually lives, rather than through a route that does not
    exist.
    """
    from akasha.sync import reconcile
    from akasha.sync.origin import OriginTracker
    from akasha.tms import review as review_module

    client, h, conn = api["client"], api["human"], api["conn"]
    client.post(
        "/v1/sync/roots", json={"name": "vault", "root_path": str(vault_dir)}, headers=h
    )

    envelope = {
        "method": "POST",
        "path": "/v1/nodes",
        "body": {"node_type": "claim", "body": "a proposed node"},
    }
    cause_ref = canonical_json(envelope).decode("utf-8")
    item = store.enqueue_review(conn, None, "proposal", cause_ref=cause_ref)

    before = sorted(str(p) for p in vault_dir.rglob("*") if p.is_file())
    assert before == []

    node_id = review_module.approve_proposal(conn, item["id"])
    node = store.get_node(conn, node_id)
    assert node.node_type == "claim"

    reconcile.project_node_change(conn, [node_id], OriginTracker())

    after = sorted(str(p) for p in vault_dir.rglob("*") if p.is_file())
    assert after == []
