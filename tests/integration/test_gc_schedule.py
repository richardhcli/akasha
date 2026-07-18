"""S0 GC scheduling + log rotation (task T9.3, spec §4.4/§4.5, M9 milestone).

Covers the three build-plan Steps directly:

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
"""

from __future__ import annotations

import time

from akasha import daemon
from akasha.kernel import store


def _fresh_conn(db_path):
    conn = store.connect(db_path, check_same_thread=False)
    store.run_migrations(conn)
    return conn


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
