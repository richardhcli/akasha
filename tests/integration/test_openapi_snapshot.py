"""OpenAPI snapshot + CI gate (task T4.7, spec §4.11 / §6.3 / PRD §7.12 rule 1).

"The generated OpenAPI JSON is snapshotted at ``docs/api-snapshot/openapi.json``;
CI fails if the served spec diverges without the snapshot being deliberately
updated in the same PR" (spec §4.11). PRD §7.12 rule 1 elevates this to the
Python->Rust migration boundary: "the API spec is the migration boundary ...
any server passing the [snapshot] suite is a valid daemon."

This module is BOTH the test and the regeneration tool: the same
``_canonical_snapshot_text()`` helper that the test compares against is what
regenerates the committed file, so the two can never silently drift apart.

To intentionally regenerate the snapshot after a deliberate route/schema
change, run from the repo root::

    uv run python -m tests.integration.test_openapi_snapshot

(or equivalently ``uv run python tests/integration/test_openapi_snapshot.py``)
which overwrites ``docs/api-snapshot/openapi.json`` with the freshly served
spec, canonically serialized. Review the diff before committing -- an
unreviewed diff here means an unreviewed API change (build-plan rule 0.3: this
file is a migration-contract artifact, never hand-edited).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from akasha.api.app import create_app
from akasha.kernel import store

SNAPSHOT_PATH = Path(__file__).resolve().parents[2] / "docs" / "api-snapshot" / "openapi.json"


def _app():
    """Build an app with an injected in-memory DB (mirrors test_health.py's
    ``_app()``): the OpenAPI schema generation itself needs no DB, but the
    ``create_app`` factory opens one eagerly, so inject ``:memory:`` to avoid
    touching ``$HOME`` during snapshot generation/verification.
    """
    conn = store.connect(":memory:", check_same_thread=False)
    store.run_migrations(conn)
    return create_app(conn=conn)


def _served_openapi_dict() -> dict[str, Any]:
    """The live OpenAPI schema dict served by the current app factory."""
    client = TestClient(_app())
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    result: dict[str, Any] = resp.json()
    return result


def _canonical_snapshot_text(spec: dict[str, Any]) -> str:
    """Deterministic, byte-stable JSON text for the snapshot file.

    Sorted keys + stable (comma/colon) separators + a fixed indent for
    human-reviewable diffs + exactly one trailing newline.
    """
    return json.dumps(spec, sort_keys=True, indent=2, separators=(",", ": ")) + "\n"


def _write_snapshot(spec: dict[str, Any]) -> None:
    # newline="" prevents Windows' default text-mode CRLF translation from
    # corrupting this LF-only canonical file on regeneration (same class of
    # bug as reconcile.py's write_if_diff -- see docs/acceptance.md's
    # 2026-07-24 Windows callout, fix #3).
    SNAPSHOT_PATH.write_text(_canonical_snapshot_text(spec), encoding="utf-8", newline="")


def test_served_spec_equals_committed_snapshot():
    """The served spec, canonically serialized, must byte-equal the committed
    snapshot. If this fails after a deliberate route/schema change, regenerate
    via ``uv run python -m tests.integration.test_openapi_snapshot`` and
    commit the resulting diff in the SAME change (build-plan rule 0.3).
    """
    served_text = _canonical_snapshot_text(_served_openapi_dict())
    assert SNAPSHOT_PATH.exists(), (
        f"{SNAPSHOT_PATH} is missing -- run "
        "`uv run python -m tests.integration.test_openapi_snapshot` to create it."
    )
    committed_text = SNAPSHOT_PATH.read_text(encoding="utf-8")
    assert served_text == committed_text, (
        "Served OpenAPI spec diverges from docs/api-snapshot/openapi.json. "
        "If this divergence is intentional, regenerate the snapshot with "
        "`uv run python -m tests.integration.test_openapi_snapshot` and commit "
        "it in the same change."
    )


def test_gate_actually_catches_drift():
    """Prove the gate is not vacuous: a mutated copy of the served spec (a
    fake extra path added) must NOT compare equal to the committed snapshot.
    """
    served = _served_openapi_dict()
    mutated = copy.deepcopy(served)
    mutated["paths"]["/v1/__deliberately_fake_drift_probe__"] = {
        "get": {
            "summary": "injected to prove the snapshot gate detects drift",
            "responses": {"200": {"description": "ok"}},
        }
    }

    committed_text = SNAPSHOT_PATH.read_text(encoding="utf-8")
    mutated_text = _canonical_snapshot_text(mutated)

    assert mutated_text != committed_text, (
        "Mutating the served spec produced text identical to the committed "
        "snapshot -- the comparison in this test would be vacuous."
    )

    # And the real (unmutated) served spec still matches, showing the failure
    # above is caused by the injected drift, not by canonicalization noise.
    served_text = _canonical_snapshot_text(served)
    assert served_text == committed_text


def test_snapshot_carries_no_product_name():
    """Rebrand invariant (rule 0.6): the on-disk snapshot must never leak the
    product name -- only the neutral ``tm-`` prefix is permitted.
    """
    committed_text = SNAPSHOT_PATH.read_text(encoding="utf-8")
    assert "akasha" not in committed_text.lower()


if __name__ == "__main__":
    _write_snapshot(_served_openapi_dict())
    print(f"Wrote {SNAPSHOT_PATH}")
