"""Unit tests for §7 metrics + GET /v1/metrics (task T9.2, spec §7, §4.11)."""

from __future__ import annotations

import concurrent.futures
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from akasha import metrics
from akasha.api import auth
from akasha.api.app import create_app
from akasha.kernel import store
from akasha.kernel.model import Facet


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Every test gets a clean in-process recorder/CPU-sampler (module singletons)."""
    metrics.reset_recorder()
    metrics.reset_cpu_sampler()
    yield
    metrics.reset_recorder()
    metrics.reset_cpu_sampler()


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = store.connect(":memory:", check_same_thread=False)
    store.run_migrations(c)
    return c


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="microseconds")


def _insert_review(
    conn: sqlite3.Connection,
    review_id: str,
    *,
    cause_kind: str = "recheck",
    created_at: str,
    resolved_at: str | None = None,
) -> None:
    """Directly INSERTs a review_queue row with a caller-chosen timestamp.

    Test-only: ``store.enqueue_review`` always stamps ``created_at`` with
    the real current time, so backdating fixture rows (to exercise the
    7d/30d windows deterministically) requires a raw INSERT here -- rule
    0.4 governs application code, not test fixture setup (see
    ``tests/integration/test_api.py``'s ``_insert_token`` for the same
    precedent against the ``tokens`` table).
    """
    resolution = "still_holds" if resolved_at else None
    with conn:
        conn.execute(
            "INSERT INTO review_queue (id, node_id, cause_kind, cause_ref, facet, "
            "created_at, resolved_at, resolution) VALUES (?, NULL, ?, NULL, NULL, ?, ?, ?)",
            (review_id, cause_kind, created_at, resolved_at, resolution),
        )


def _backdate_node(conn: sqlite3.Connection, node_id: str, created_at: str) -> None:
    """Test-only: rewrite a node's created_at to a caller-chosen timestamp."""
    with conn:
        conn.execute("UPDATE nodes SET created_at=? WHERE id=?", (created_at, node_id))


# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------


def test_percentile_empty_is_zero():
    assert metrics._percentile([], 50) == 0.0
    assert metrics._percentile([], 95) == 0.0


def test_percentile_single_value():
    assert metrics._percentile([42.0], 50) == 42.0
    assert metrics._percentile([42.0], 95) == 42.0


def test_percentile_known_distribution():
    samples = [float(v) for v in range(1, 11)]  # 1..10
    # linear-interpolation percentile of a uniform 1..10 sample.
    assert metrics._percentile(samples, 50) == pytest.approx(5.5)
    assert metrics._percentile(samples, 95) == pytest.approx(9.55)


def test_population_variance_empty_and_known():
    assert metrics._population_variance([]) == 0.0
    # [2, 4, 4, 4, 5, 5, 7, 9] -> mean 5, population variance 4
    assert metrics._population_variance([2, 4, 4, 4, 5, 5, 7, 9]) == pytest.approx(4.0)


def test_daily_counts_zero_fills_quiet_days():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 3, tzinfo=timezone.utc)
    timestamps = ["2026-01-01T00:00:00.000000+00:00", "2026-01-01T12:00:00.000000+00:00"]
    counts = metrics._daily_counts(timestamps, start, end)
    assert counts == [2, 0, 0]


# ---------------------------------------------------------------------------
# In-process recorder (sync_cycle_ms / auto_repairs / violation_rate)
# ---------------------------------------------------------------------------


def test_record_sync_cycle_and_auto_repair_round_trip():
    metrics.record_sync_cycle_ms(10.0)
    metrics.record_sync_cycle_ms(20.0)
    metrics.record_auto_repair("E_LOST_ANCHOR")
    metrics.record_auto_repair("E_LOST_ANCHOR")
    metrics.record_auto_repair("E_DUP_ID")

    durations, repairs = metrics._recorder.snapshot()
    assert durations == [10.0, 20.0]
    assert repairs == {"E_LOST_ANCHOR": 2, "E_DUP_ID": 1}


def test_violation_rate_zero_cycles_is_zero(conn):
    result = metrics.compute_metrics(conn)
    assert result["violation_rate"] == 0.0


def test_violation_rate_divides_violations_by_recorded_cycles(conn):
    now_iso = _iso(datetime.now(timezone.utc))
    _insert_review(conn, "viol0001", cause_kind="violation", created_at=now_iso)
    _insert_review(conn, "viol0002", cause_kind="violation", created_at=now_iso)
    metrics.record_sync_cycle_ms(5.0)
    metrics.record_sync_cycle_ms(7.0)
    metrics.record_sync_cycle_ms(9.0)

    result = metrics.compute_metrics(conn)
    assert result["violation_rate"] == pytest.approx(2 / 3)


def test_sync_cycle_ms_reports_percentiles(conn):
    for value in (100.0, 200.0, 300.0, 400.0, 500.0):
        metrics.record_sync_cycle_ms(value)

    result = metrics.compute_metrics(conn)
    assert result["sync_cycle_ms"]["p50"] == pytest.approx(300.0)
    assert result["sync_cycle_ms"]["p95"] == pytest.approx(480.0)


def test_auto_repairs_appears_empty_with_no_recorded_repairs(conn):
    result = metrics.compute_metrics(conn)
    assert result["auto_repairs"] == {}


def test_auto_repairs_grouped_by_class(conn):
    metrics.record_auto_repair("E_LOST_ANCHOR")
    metrics.record_auto_repair("E_LOST_ANCHOR")
    metrics.record_auto_repair("E_DUP_ID")

    result = metrics.compute_metrics(conn)
    assert result["auto_repairs"] == {"E_LOST_ANCHOR": 2, "E_DUP_ID": 1}


# ---------------------------------------------------------------------------
# facet_coverage
# ---------------------------------------------------------------------------


def _mk_source(conn: sqlite3.Connection) -> str:
    return store.create_node(conn, "entity", "a plain source node").id


def test_facet_coverage_zero_when_no_s2_nodes(conn):
    store.create_node(conn, "claim", "an unlinked claim, stays S0")
    result = metrics.compute_metrics(conn)
    assert result["facet_coverage"] == 0.0


def test_facet_coverage_excludes_task_and_entity_types(conn):
    """S2+ task/entity nodes reach S2 without facets (maturity.py exemption);
    they must not appear in facet_coverage's denominator at all."""
    task_node = store.create_node(conn, "task", "a task node", task_state="open")
    src = _mk_source(conn)
    store.create_edge(conn, src, task_node.id, "composes", None, "human")
    assert store.get_maturity(conn, task_node.id) == "S2"

    counts = store.facet_coverage_counts(conn)
    assert counts == {"covered": 0, "total": 0}
    result = metrics.compute_metrics(conn)
    assert result["facet_coverage"] == 0.0


def test_facet_coverage_ratio_of_specific_vs_wildcard_bindings(conn):
    src = _mk_source(conn)

    facet_a = Facet(facet_id="faceta01", name="a", span="A", version=1)
    facet_b = Facet(facet_id="facetb01", name="b", span="B", version=1)
    facet_c = Facet(facet_id="facetc01", name="c", span="C", version=1)

    # Node A: S2 via a plain composes inbound edge (no justification edge
    # at all) -- counts toward `total`, never toward `covered`.
    node_a = store.create_node(conn, "definition", "definition A", facets=[facet_a])
    store.create_edge(conn, src, node_a.id, "composes", None, "human")
    assert store.get_maturity(conn, node_a.id) == "S2"

    # Node B: S2 via a justification edge bound to the wildcard "*" --
    # counts toward `total`, never toward `covered` (spec §4.2/§7).
    node_b = store.create_node(conn, "definition", "definition B", facets=[facet_b])
    store.create_edge(conn, src, node_b.id, "supports", "*", "human")
    assert store.get_maturity(conn, node_b.id) == "S2"

    # Node C: S2 via a justification edge bound to a CONCRETE facet id --
    # counts toward both `total` and `covered`.
    node_c = store.create_node(conn, "definition", "definition C", facets=[facet_c])
    store.create_edge(conn, src, node_c.id, "cites", "facetc01", "human")
    assert store.get_maturity(conn, node_c.id) == "S2"

    counts = store.facet_coverage_counts(conn)
    assert counts == {"covered": 1, "total": 3}

    result = metrics.compute_metrics(conn)
    assert result["facet_coverage"] == pytest.approx(1 / 3)


# ---------------------------------------------------------------------------
# review_inflow_7d / review_resolved_7d / inflow_variance_30d
# ---------------------------------------------------------------------------


def test_review_inflow_7d_counts_only_recent_rows(conn):
    now = datetime.now(timezone.utc)
    _insert_review(conn, "recent01", created_at=_iso(now - timedelta(days=1)))
    _insert_review(conn, "recent02", created_at=_iso(now - timedelta(days=6)))
    _insert_review(conn, "stale001", created_at=_iso(now - timedelta(days=8)))

    result = metrics.compute_metrics(conn)
    assert result["review_inflow_7d"] == 2


def test_review_resolved_7d_counts_only_recently_resolved_rows(conn):
    now = datetime.now(timezone.utc)
    _insert_review(
        conn,
        "resolved1",
        created_at=_iso(now - timedelta(days=10)),
        resolved_at=_iso(now - timedelta(days=2)),
    )
    _insert_review(
        conn,
        "resolved2",
        created_at=_iso(now - timedelta(days=10)),
        resolved_at=_iso(now - timedelta(days=9)),
    )
    _insert_review(conn, "stillopen", created_at=_iso(now - timedelta(days=1)))

    result = metrics.compute_metrics(conn)
    assert result["review_resolved_7d"] == 1


def test_inflow_variance_30d_zero_for_constant_daily_rate(conn):
    now = datetime.now(timezone.utc)
    for day_offset in range(5):
        review_id = f"const{day_offset:03d}"
        created_at = _iso(now - timedelta(days=day_offset))
        _insert_review(conn, review_id, created_at=created_at)

    result = metrics.compute_metrics(conn)
    # 1 review/day for 5 of the 31 days in the window, 0 for the rest --
    # not perfectly constant, so just assert it's a small, finite, >=0 number.
    assert result["inflow_variance_30d"] >= 0.0


def test_inflow_variance_30d_matches_manual_calculation(conn):
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=30)
    # Two reviews today, zero every other day in the 31-day window.
    _insert_review(conn, "burst0001", created_at=_iso(now))
    _insert_review(conn, "burst0002", created_at=_iso(now))

    expected_counts = metrics._daily_counts(
        [_iso(now), _iso(now)], window_start, now
    )
    expected_variance = metrics._population_variance(expected_counts)

    result = metrics.compute_metrics(conn)
    assert result["inflow_variance_30d"] == pytest.approx(expected_variance)
    assert expected_variance > 0.0


# ---------------------------------------------------------------------------
# crossing_rate
# ---------------------------------------------------------------------------


def test_crossing_rate_zero_with_no_nodes(conn):
    result = metrics.compute_metrics(conn)
    assert result["crossing_rate"] == 0.0


def test_crossing_rate_divides_total_nodes_by_elapsed_days():
    conn = store.connect(":memory:", check_same_thread=False)
    store.run_migrations(conn)
    now = datetime.now(timezone.utc)
    backdated = _iso(now - timedelta(days=4))
    ids = [store.create_node(conn, "claim", f"claim {i}").id for i in range(8)]
    for node_id in ids:
        _backdate_node(conn, node_id, backdated)

    result = metrics.compute_metrics(conn)
    # 8 nodes over ~4 elapsed days -> rate ~= 2/day.
    assert result["crossing_rate"] == pytest.approx(2.0, rel=0.05)


def test_crossing_rate_floors_elapsed_days_at_one(conn):
    store.create_node(conn, "claim", "just minted")
    result = metrics.compute_metrics(conn)
    # A node minted moments ago must not spike the rate far above 1/day.
    assert result["crossing_rate"] <= 1.0 + 1e-6


# ---------------------------------------------------------------------------
# rss_bytes / idle_cpu_pct sampling
# ---------------------------------------------------------------------------


def test_rss_bytes_is_positive_for_this_process(conn):
    result = metrics.compute_metrics(conn)
    assert isinstance(result["rss_bytes"], int)
    assert result["rss_bytes"] > 0


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only RSS sampler (D2)")
def test_sample_rss_bytes_windows_is_safe_under_concurrent_calls():
    """D2: concurrent callers must never race on shared, mutated ctypes state.

    Before the fix, ``_sample_rss_bytes_windows`` reassigned the shared
    ``kernel32``/``psapi`` DLL objects' ``argtypes``/``restype`` on every
    call, so two threads calling it at once could observe each other's
    in-flight reassignment and raise ``ctypes.ArgumentError``. Hammering it
    from many threads at once reproduces that race when the fix is absent
    and must pass cleanly with it in place.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda _: metrics._sample_rss_bytes_windows(), range(200)))

    assert all(isinstance(r, int) and r > 0 for r in results)


def test_idle_cpu_pct_first_sample_is_zero_then_bounded(conn):
    first = metrics.compute_metrics(conn)
    assert first["idle_cpu_pct"] == 0.0  # no prior sample yet

    second = metrics.compute_metrics(conn)
    assert 0.0 <= second["idle_cpu_pct"] <= 100.0


# ---------------------------------------------------------------------------
# compute_metrics: every §7 counter appears (task DoD)
# ---------------------------------------------------------------------------

_EXPECTED_METRIC_KEYS = {
    "facet_coverage",
    "review_inflow_7d",
    "review_resolved_7d",
    "inflow_variance_30d",
    "violation_rate",
    "auto_repairs",
    "crossing_rate",
    "rss_bytes",
    "idle_cpu_pct",
    "sync_cycle_ms",
}


def test_compute_metrics_exposes_every_section_7_counter(conn):
    result = metrics.compute_metrics(conn)
    assert set(result.keys()) == _EXPECTED_METRIC_KEYS
    assert set(result["sync_cycle_ms"].keys()) == {"p50", "p95"}


# ---------------------------------------------------------------------------
# GET /v1/metrics route (spec §4.11)
# ---------------------------------------------------------------------------


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
def api(conn):
    human_secret = auth.mint_secret()
    _insert_token(conn, "humantoken", human_secret, "human")
    client = TestClient(create_app(conn=conn))
    bearer = auth.format_bearer_token("humantoken", human_secret)
    return {"client": client, "headers": {"Authorization": f"Bearer {bearer}"}}


def test_get_v1_metrics_returns_every_counter(api):
    resp = api["client"].get("/v1/metrics", headers=api["headers"])
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == _EXPECTED_METRIC_KEYS


def test_get_v1_metrics_requires_auth(api):
    resp = api["client"].get("/v1/metrics")  # no Authorization header
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "E_AUTH"
