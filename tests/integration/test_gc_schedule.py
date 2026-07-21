"""S0 GC scheduling + log rotation (tasks T9.3/T9.3b, spec §4.4/§4.5, M9 milestone).

Covers the three original T9.3 build-plan Steps directly:

1. ``daemon.GcScheduler`` runs the existing T1.7 ``kernel.store.gc_objects``
   job on a recurring background tick (not just once at startup) --
   ``interval_seconds`` is injected small so the test observes real
   recurrence in milliseconds rather than waiting a real day.
2. ``daemon.configure_logging``'s file handler is a real
   ``RotatingFileHandler`` that actually rotates once the configured size
   is exceeded (``max_bytes``/``backup_count`` are injected small so the
   test can trigger a real rotation without writing 10 MB of log lines).
3. GC scheduled via :class:`daemon.GcScheduler` still keeps every object
   referenced by a live node/commit/base-snapshot (the T1.7 invariant,
   reused verbatim -- see ``tests/unit/kernel/test_gc.py``), and only ever
   removes true orphans.

Plus T9.3b's Step 4 (vision.md §14 A7 age-based S0 node-retention GC):

4. A scheduled tick hard-deletes a live S0 node older than the configured
   ``s0_gc_retention_days`` threshold, leaves a younger S0 node alone,
   NEVER touches an S1+ node regardless of age, and reclaims that tick's
   own newly-orphaned objects via ``gc_objects`` in the SAME tick (no
   two-tick lag).
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from akasha import daemon
from akasha.kernel import store


def _fresh_conn(db_path):
    conn = store.connect(db_path, check_same_thread=False)
    store.run_migrations(conn)
    return conn


def _backdate_node(conn, node_id: str, created_at: str) -> None:
    """Test-only: rewrite a node's created_at to a caller-chosen timestamp.

    Mirrors ``tests/unit/test_metrics.py``'s ``_backdate_node`` precedent --
    rule 0.4 governs application code, not test fixture setup; there is no
    production write path that ever needs to move a node's created_at
    backwards, so a raw SQL UPDATE here is the correct way to deterministically
    exercise the age threshold without sleeping real days in a test.
    """
    with conn:
        conn.execute("UPDATE nodes SET created_at=? WHERE id=?", (created_at, node_id))


def _iso_days_ago(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(
        timespec="microseconds"
    )


def _make_orphan(conn) -> str:
    """Create then S0-hard-delete a node, leaving its object row orphaned."""
    node = store.create_node(conn, "claim", "orphan claim", author="alice")
    head_hash = conn.execute(
        "SELECT head_hash FROM nodes WHERE id=?", (node.id,)
    ).fetchone()[0]
    store.delete_node(conn, node.id)
    return head_hash


def test_run_once_removes_only_orphans_reusing_t1_7_invariant(tmp_path) -> None:
    """Step 3: GC keeps referenced objects (reuse T1.7 invariant)."""
    db_path = tmp_path / "gc_schedule.db"
    conn = _fresh_conn(db_path)

    src = store.create_node(conn, "claim", "referenced src", author="alice")
    dst = store.create_node(conn, "claim", "referenced dst", author="alice")
    store.create_edge(
        conn, src=src.id, dst=dst.id, edge_type="composes", facet_binding=None, provenance="human"
    )
    referenced_hash = conn.execute(
        "SELECT head_hash FROM nodes WHERE id=?", (dst.id,)
    ).fetchone()[0]

    orphan_hash = _make_orphan(conn)
    conn.close()

    scheduler = daemon.GcScheduler(db_path)
    deleted = scheduler.run_once()

    assert orphan_hash in deleted
    assert referenced_hash not in deleted

    check_conn = store.connect(db_path)
    assert check_conn.execute(
        "SELECT 1 FROM objects WHERE hash=?", (orphan_hash,)
    ).fetchone() is None
    assert check_conn.execute(
        "SELECT 1 FROM objects WHERE hash=?", (referenced_hash,)
    ).fetchone() is not None
    check_conn.close()


def test_scheduler_ticks_immediately_on_start(tmp_path) -> None:
    """Step 1: GC runs on a schedule -- the very first tick fires at start()."""
    db_path = tmp_path / "gc_schedule.db"
    conn = _fresh_conn(db_path)
    orphan_hash = _make_orphan(conn)
    conn.close()

    scheduler = daemon.GcScheduler(db_path, interval_seconds=60.0)
    try:
        scheduler.start()
        deadline = time.monotonic() + 5.0
        check_conn = store.connect(db_path)
        try:
            while time.monotonic() < deadline:
                row = check_conn.execute(
                    "SELECT 1 FROM objects WHERE hash=?", (orphan_hash,)
                ).fetchone()
                if row is None:
                    break
                time.sleep(0.02)
            else:
                raise AssertionError("scheduler did not GC the orphan within 5s of start()")
        finally:
            check_conn.close()
    finally:
        scheduler.stop()


def test_scheduler_recurs_on_a_tick_not_just_once(tmp_path) -> None:
    """Step 1: the tick is recurring -- an orphan created AFTER start() is
    still collected on a later tick, not only the immediate startup tick."""
    db_path = tmp_path / "gc_schedule.db"
    conn = _fresh_conn(db_path)
    conn.close()

    scheduler = daemon.GcScheduler(db_path, interval_seconds=0.1)
    try:
        scheduler.start()
        time.sleep(0.05)  # let the immediate startup tick pass (no-op: nothing to GC yet)

        conn = store.connect(db_path, check_same_thread=False)
        orphan_hash = _make_orphan(conn)
        conn.close()

        deadline = time.monotonic() + 5.0
        check_conn = store.connect(db_path)
        try:
            while time.monotonic() < deadline:
                row = check_conn.execute(
                    "SELECT 1 FROM objects WHERE hash=?", (orphan_hash,)
                ).fetchone()
                if row is None:
                    break
                time.sleep(0.02)
            else:
                raise AssertionError("scheduler did not GC the orphan on a later recurring tick")
        finally:
            check_conn.close()
    finally:
        scheduler.stop()


def test_stop_joins_the_background_thread(tmp_path) -> None:
    db_path = tmp_path / "gc_schedule.db"
    conn = _fresh_conn(db_path)
    conn.close()

    scheduler = daemon.GcScheduler(db_path, interval_seconds=60.0)
    scheduler.start()
    thread = scheduler._thread
    assert thread is not None
    assert thread.is_alive()

    scheduler.stop()

    assert not thread.is_alive()
    assert scheduler._thread is None


def test_start_is_idempotent(tmp_path) -> None:
    db_path = tmp_path / "gc_schedule.db"
    conn = _fresh_conn(db_path)
    conn.close()

    scheduler = daemon.GcScheduler(db_path, interval_seconds=60.0)
    try:
        scheduler.start()
        first_thread = scheduler._thread
        scheduler.start()
        assert scheduler._thread is first_thread
    finally:
        scheduler.stop()


def test_stop_before_start_is_a_no_op(tmp_path) -> None:
    db_path = tmp_path / "gc_schedule.db"
    scheduler = daemon.GcScheduler(db_path)
    scheduler.stop()  # must not raise


def test_configure_logging_rotates_file_at_configured_size(tmp_path) -> None:
    """Step 2: confirm the rotating file handler actually rotates."""
    log_file = tmp_path / "daemon.log"
    logger = daemon.configure_logging(log_file, max_bytes=500, backup_count=2)
    try:
        for i in range(200):
            logger.info(f"log line number {i} padded to force real rotation to occur soon")
    finally:
        for handler in logger.handlers:
            handler.close()
        logger.handlers.clear()

    assert log_file.exists()
    rotated = log_file.with_name(log_file.name + ".1")
    assert rotated.exists(), "expected at least one rotated backup file (daemon.log.1)"
    # backup_count=2 caps the *rotated* backups at 2 (daemon.log.1, daemon.log.2);
    # a .3 would mean the RotatingFileHandler isn't honoring backup_count.
    assert not log_file.with_name(log_file.name + ".3").exists()
    # every individual file (including the still-open current one) stays
    # near the configured size, proving rotation actually triggered instead
    # of one file silently growing unbounded.
    assert log_file.stat().st_size < 5_000


def test_configure_logging_default_sizing_matches_module_constants() -> None:
    """T0.6's original hardcoded 10 MB / 5-backup defaults are preserved."""
    assert daemon.LOG_MAX_BYTES == 10_000_000
    assert daemon.LOG_BACKUP_COUNT == 5


def test_configure_logging_still_uses_json_line_formatter(tmp_path) -> None:
    log_file = tmp_path / "daemon.log"
    logger = daemon.configure_logging(log_file)
    handlers = list(logger.handlers)
    try:
        logger.info("hello")
        assert any(isinstance(h.formatter, daemon.JsonLineFormatter) for h in handlers)
    finally:
        for handler in handlers:
            handler.close()
        logger.handlers.clear()


# ---------------------------------------------------------------------------
# T9.3b: age-based S0 node-retention GC (vision.md §14 A7).
# ---------------------------------------------------------------------------


def test_expired_s0_node_deleted_on_tick(tmp_path) -> None:
    """Step 4a: a live S0 node older than the retention threshold is gone
    after one scheduled tick."""
    db_path = tmp_path / "gc_schedule.db"
    conn = _fresh_conn(db_path)

    old_node = store.create_node(conn, "claim", "an old, never-linked S0 claim", author="alice")
    assert (
        conn.execute("SELECT maturity FROM nodes WHERE id=?", (old_node.id,)).fetchone()[0]
        == "S0"
    )
    _backdate_node(conn, old_node.id, _iso_days_ago(31))
    conn.close()

    scheduler = daemon.GcScheduler(db_path, s0_gc_retention_days=30)
    scheduler.run_once()

    check_conn = store.connect(db_path)
    assert (
        check_conn.execute("SELECT 1 FROM nodes WHERE id=?", (old_node.id,)).fetchone() is None
    )
    check_conn.close()


def test_young_s0_node_survives_tick(tmp_path) -> None:
    """Step 4b: an S0 node younger than the retention threshold survives."""
    db_path = tmp_path / "gc_schedule.db"
    conn = _fresh_conn(db_path)

    young_node = store.create_node(conn, "claim", "a fresh S0 claim", author="alice")
    _backdate_node(conn, young_node.id, _iso_days_ago(5))
    conn.close()

    scheduler = daemon.GcScheduler(db_path, s0_gc_retention_days=30)
    scheduler.run_once()

    check_conn = store.connect(db_path)
    row = check_conn.execute(
        "SELECT status FROM nodes WHERE id=?", (young_node.id,)
    ).fetchone()
    assert row is not None
    assert row[0] == "live"
    check_conn.close()


def test_s1_plus_node_never_deleted_regardless_of_age(tmp_path) -> None:
    """Step 4c (safety-critical): an S1+ node survives age-based GC no
    matter how old it is -- vision.md §14 A7's "GC blocked at S1
    automatically." A real live inbound edge is what promotes the node to
    S1 (spec §4.6: "S1 iff live inbound edge count >= 1"), then it's
    backdated well past the retention threshold before the tick runs."""
    db_path = tmp_path / "gc_schedule.db"
    conn = _fresh_conn(db_path)

    target = store.create_node(conn, "claim", "an old but linked claim", author="alice")
    linker = store.create_node(conn, "claim", "a claim that cites target", author="alice")
    store.create_edge(
        conn,
        src=linker.id,
        dst=target.id,
        edge_type="composes",
        facet_binding=None,
        provenance="human",
    )
    maturity = conn.execute(
        "SELECT maturity FROM nodes WHERE id=?", (target.id,)
    ).fetchone()[0]
    assert maturity == "S1", "test setup must actually promote target to S1 before proceeding"

    # Only backdate target far past the retention threshold -- age alone must
    # never be sufficient to delete an S1+ node. Deliberately leave `linker`
    # young (its own S0 age is irrelevant to this test): if it were backdated
    # too, `linker` itself would become age-expired and get hard-deleted this
    # same tick, which would delete the `linker -> target` edge as a side
    # effect of `delete_node`'s S0 branch -- confounding the assertion below
    # (target's `maturity` column would go stale rather than staying
    # genuinely, currently S1). Keeping `linker` young keeps the inbound edge
    # -- and therefore target's S1-ness -- real through and after the tick.
    _backdate_node(conn, target.id, _iso_days_ago(365))
    conn.close()

    scheduler = daemon.GcScheduler(db_path, s0_gc_retention_days=30)
    scheduler.run_once()

    check_conn = store.connect(db_path)
    row = check_conn.execute(
        "SELECT status, maturity FROM nodes WHERE id=?", (target.id,)
    ).fetchone()
    assert row is not None, "S1+ node must never be hard-deleted by age-based GC"
    assert row[0] == "live"
    assert row[1] == "S1"
    # The inbound edge (and its source node) must still be intact too --
    # confirms target is genuinely, currently S1 post-tick, not a stale
    # 'S1' maturity column left over from a deleted linker.
    assert (
        check_conn.execute("SELECT 1 FROM nodes WHERE id=?", (linker.id,)).fetchone()
        is not None
    )
    assert (
        check_conn.execute(
            "SELECT 1 FROM edges WHERE src=? AND dst=? AND retracted_at IS NULL",
            (linker.id, target.id),
        ).fetchone()
        is not None
    )
    check_conn.close()


def test_same_tick_reclaims_objects_orphaned_by_node_deletion(tmp_path) -> None:
    """Step 4d: node deletion runs BEFORE gc_objects in the same tick, so
    the object a just-deleted expired S0 node referenced is reclaimed
    immediately -- no two-tick lag."""
    db_path = tmp_path / "gc_schedule.db"
    conn = _fresh_conn(db_path)

    old_node = store.create_node(conn, "claim", "old S0 claim to expire", author="alice")
    orphan_hash = conn.execute(
        "SELECT head_hash FROM nodes WHERE id=?", (old_node.id,)
    ).fetchone()[0]
    _backdate_node(conn, old_node.id, _iso_days_ago(31))

    # A referenced object (from an unrelated live node) must survive the same tick.
    other = store.create_node(conn, "claim", "unrelated live claim", author="alice")
    referenced_hash = conn.execute(
        "SELECT head_hash FROM nodes WHERE id=?", (other.id,)
    ).fetchone()[0]

    assert (
        conn.execute("SELECT 1 FROM objects WHERE hash=?", (orphan_hash,)).fetchone()
        is not None
    ), "precondition: the object must exist before the tick"
    conn.close()

    scheduler = daemon.GcScheduler(db_path, s0_gc_retention_days=30)
    deleted = scheduler.run_once()

    assert orphan_hash in deleted, (
        "the object orphaned by this tick's own node deletion must be reclaimed "
        "in the SAME tick, not a later one"
    )

    check_conn = store.connect(db_path)
    assert (
        check_conn.execute("SELECT 1 FROM nodes WHERE id=?", (old_node.id,)).fetchone() is None
    )
    assert (
        check_conn.execute("SELECT 1 FROM objects WHERE hash=?", (orphan_hash,)).fetchone()
        is None
    )
    assert (
        check_conn.execute(
            "SELECT 1 FROM objects WHERE hash=?", (referenced_hash,)
        ).fetchone()
        is not None
    )
    check_conn.close()


def test_default_retention_matches_vision_a7() -> None:
    """vision.md §14 A7's stated default: 30 days."""
    from akasha.config import DEFAULT_S0_GC_RETENTION_DAYS

    assert DEFAULT_S0_GC_RETENTION_DAYS == 30

    scheduler = daemon.GcScheduler("/tmp/unused.db")
    assert scheduler._s0_gc_retention_days == 30
