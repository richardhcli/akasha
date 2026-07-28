"""Process lifecycle: structured logging (T0.6) + single-instance lock + serve (T4.9).

Spec §4.12 (``akasha daemon [--config PATH]``), M4 milestone text ("single-
instance lock; Task Scheduler XML + NSSM instructions in ``docs/``"). The
lock file lives in the config directory under a **neutral** filename
(build-plan rule 0.6 — the product name never appears in on-disk paths):
``tm-daemon.lock``, matching the existing neutral ``tm-daemon`` config-dir
name from ``config.py``.

Locking is cross-platform (spec §3: Windows is the release gate) via
``fcntl.flock`` on POSIX and ``msvcrt.locking`` on Windows, both acquired
in **non-blocking** mode so a second instance fails fast with a typed,
human-readable :class:`AlreadyRunningError` instead of hanging or dumping a
traceback.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import IO, TYPE_CHECKING

from akasha.config import DEFAULT_S0_GC_RETENTION_DAYS

if TYPE_CHECKING:
    from collections.abc import Generator

    from akasha.config import Config

LOCK_FILE_NAME = "tm-daemon.lock"
LOG_FILE_NAME = "daemon.log"

# T0.6 default rotation sizing, kept as module constants (rather than
# hardcoded literals in configure_logging's signature) so T9.3's tests can
# reference the production defaults by name. Neither the spec nor the
# build-plan Steps pin a rotation size/backup count -- narrowest-reading
# judgment call, not a SPEC-QUESTION: 10 MB keeps a single file well within
# "open in a text editor" territory, 5 backups (~50 MB worst case) is a sane
# bound for a local-first single-user daemon with no log-shipping story.
LOG_MAX_BYTES = 10_000_000
LOG_BACKUP_COUNT = 5


class JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        if record.exc_info:
            payload["traceback"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging(
    log_file: str | Path,
    level: int = logging.INFO,
    *,
    max_bytes: int = LOG_MAX_BYTES,
    backup_count: int = LOG_BACKUP_COUNT,
) -> logging.Logger:
    """Configure the shared ``"akasha"`` logger with a size-rotating file handler.

    ``max_bytes``/``backup_count`` (T9.3, keyword-only, defaulting to the
    original T0.6 hardcoded values) let tests drive real rotation with a
    tiny ``max_bytes`` instead of writing 10 MB of log lines; every existing
    production call site (``serve`` below) is unaffected since it never
    passes them.
    """
    logger = logging.getLogger("akasha")
    logger.setLevel(level)
    logger.handlers.clear()

    formatter = JsonLineFormatter()

    file_handler = RotatingFileHandler(log_file, maxBytes=max_bytes, backupCount=backup_count)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    return logger


# M9 "daily tick" (build-plan T9.3 Steps). Neither the spec nor the
# build-plan make this configurable, so a fixed default is the
# narrowest-reading judgment call (not a SPEC-QUESTION -- "daily tick" is
# prescriptive, not silent, about the cadence).
GC_INTERVAL_SECONDS = 24 * 60 * 60


class GcScheduler:
    """Runs the T1.7 ``kernel.store.gc_objects`` job on a background daily tick.

    Reuses ``gc_objects(conn) -> list[str]`` verbatim (rule 0.4 -- no new
    SQL lives here; this class only adds the *scheduling* layer M9/T9.3
    calls for). ``gc_objects``'s own invariant -- never removes an object
    still referenced by a commit, a node head, or a base snapshot -- is
    unchanged by running it on a timer instead of synchronously (see
    ``tests/unit/kernel/test_gc.py`` / T1.7).

    Each tick also runs the task T9.3b age-based S0 *node* retention GC
    (vision.md §14 A7: "S0 default GC retention 30 days (configurable); GC
    blocked at S1 automatically") -- the archived T1.7 resolution's
    two-step lifecycle: (1) hard-delete every live S0 node older than
    ``s0_gc_retention_days`` via the EXISTING, unchanged ``delete_node`` S0
    hard-delete branch (T1.6 -- no new deletion path), THEN (2) run
    ``gc_objects`` in the SAME tick, so the objects those node deletions
    just orphaned are reclaimed immediately rather than lagging a tick.
    Node deletion never touches S1+ nodes: ``store.list_expired_s0_node_ids``
    filters ``maturity='S0' AND status='live'`` exactly, and ``delete_node``
    itself only hard-deletes when the (freshly recomputed) maturity is S0.

    Each tick opens and closes its own short-lived connection to
    ``db_path`` (mirroring ``api/deps.py::get_conn``'s per-request
    pattern) rather than sharing ``app.state.conn`` with request handling
    or the startup reconcile -- the docstring on ``store.connect`` warns a
    single ``sqlite3.Connection`` is not safe under concurrent
    cross-thread access, and WAL mode is designed for exactly this
    "many short-lived connections" usage instead.

    :meth:`start` runs one tick immediately (so a freshly (re)started
    daemon reclaims anything orphaned since it last ran), then again every
    ``interval_seconds`` until :meth:`stop`. ``interval_seconds`` is
    injectable so tests can observe multiple ticks in milliseconds rather
    than real days; :meth:`run_once` is also public so a test (or a future
    manual "gc now" trigger) can run exactly one tick synchronously without
    the background thread at all.
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        interval_seconds: float = GC_INTERVAL_SECONDS,
        s0_gc_retention_days: int = DEFAULT_S0_GC_RETENTION_DAYS,
        logger: logging.Logger | None = None,
    ) -> None:
        self._db_path = db_path
        self._interval_seconds = interval_seconds
        self._s0_gc_retention_days = s0_gc_retention_days
        self._logger = logger if logger is not None else logging.getLogger("akasha")
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def run_once(self) -> list[str]:
        """Run a single GC tick against a fresh connection; returns deleted object hashes.

        Step order within the tick (T9.3b): expired S0 *node* deletion
        first, then ``gc_objects`` -- so objects orphaned by this tick's own
        node deletions are reclaimed now, not on a later tick.
        """
        from akasha.kernel import store

        conn = store.connect(self._db_path, check_same_thread=False)
        try:
            cutoff = (
                datetime.now(timezone.utc) - timedelta(days=self._s0_gc_retention_days)
            ).isoformat(timespec="microseconds")
            expired_node_ids = store.list_expired_s0_node_ids(conn, cutoff)
            for node_id in expired_node_ids:
                store.delete_node(conn, node_id)
            deleted = store.gc_objects(conn)
        finally:
            conn.close()
        if expired_node_ids:
            self._logger.info(
                f"gc tick complete: removed {len(expired_node_ids)} expired S0 node(s) "
                f"and {len(deleted)} orphaned object(s)"
            )
        else:
            self._logger.info(f"gc tick complete: removed {len(deleted)} orphaned object(s)")
        return deleted

    def start(self) -> None:
        """Start the background tick thread (no-op if already started)."""
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, name="akasha-gc-scheduler", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Signal the tick loop to stop and join it (clean, bounded shutdown)."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def _loop(self) -> None:
        while True:
            try:
                self.run_once()
            except Exception:
                # A failed tick must never crash the daemon's serving thread;
                # log and retry on the next scheduled tick instead.
                self._logger.exception("gc tick failed")
            if self._stop_event.wait(self._interval_seconds):
                return


class AlreadyRunningError(RuntimeError):
    """Raised when a single-instance lock is already held by another process.

    Carries the lock path so callers (the CLI) can render a clear,
    non-traceback message rather than an opaque ``OSError``.
    """

    def __init__(self, lock_path: Path) -> None:
        self.lock_path = lock_path
        super().__init__(
            f"another akasha daemon instance is already running (lock held at {lock_path})"
        )


def _acquire_posix(handle: IO[bytes], lock_path: Path) -> None:
    # Mirrors _acquire_windows's guard below: typeshed only declares fcntl's
    # POSIX-only members under `sys.platform != "win32"`, so this early
    # return keeps the rest of the function unreachable (hence unchecked) to
    # pyright when analyzing on a Windows host, matching the runtime reality
    # that this branch only ever executes on POSIX (caller-guarded too).
    if sys.platform == "win32":  # pragma: no cover - POSIX-only branch
        raise AssertionError("_acquire_posix called on Windows")
    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        raise AlreadyRunningError(lock_path) from exc


def _release_posix(handle: IO[bytes]) -> None:
    if sys.platform == "win32":  # pragma: no cover - POSIX-only branch
        raise AssertionError("_release_posix called on Windows")
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _acquire_windows(handle: IO[bytes], lock_path: Path) -> None:
    # Typeshed only declares msvcrt's members under `sys.platform ==
    # "win32"`; this early return makes the rest of the function
    # unreachable (hence unchecked) to pyright on non-Windows analysis
    # hosts, matching the CI/dev-host reality that this branch only ever
    # executes on Windows (runtime-guarded by the caller too).
    if sys.platform != "win32":  # pragma: no cover - Windows-only branch
        raise AssertionError("_acquire_windows called on a non-Windows platform")
    import msvcrt

    # msvcrt.locking locks a byte range starting at the current file
    # position; the file must actually contain that many bytes, so ensure
    # at least one byte exists before requesting a 1-byte non-blocking
    # exclusive lock (LK_NBLCK).
    #
    # The whole sequence -- not just the locking() call -- must be inside
    # the try: on real Windows, reading a byte range another handle already
    # holds via msvcrt.locking (e.g. a second acquisition attempt in the
    # same process, exercised by test_second_acquisition_fails_with_clear_
    # typed_error) raises PermissionError from handle.read(1) itself,
    # before locking() is ever reached. This was unreachable/unverified on
    # a Linux dev host (msvcrt doesn't exist there) and only surfaced when
    # actually run on Windows.
    try:
        handle.seek(0)
        if not handle.read(1):
            handle.seek(0)
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError as exc:
        raise AlreadyRunningError(lock_path) from exc


def _release_windows(handle: IO[bytes]) -> None:
    if sys.platform != "win32":  # pragma: no cover - Windows-only branch
        raise AssertionError("_release_windows called on a non-Windows platform")
    import msvcrt

    handle.seek(0)
    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


@contextmanager
def single_instance_lock(lock_path: str | Path) -> Generator[None]:
    """Hold an exclusive, non-blocking OS-level lock on ``lock_path``.

    Raises :class:`AlreadyRunningError` immediately (never blocks) if
    another process already holds the lock. Releases the lock on context
    exit (normal or exceptional) so a subsequent acquisition in the same
    or another process succeeds again -- this is the "clean shutdown frees
    the lock" behaviour required by the DoD.
    """
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    try:
        if sys.platform == "win32":
            _acquire_windows(handle, lock_path)
        else:
            _acquire_posix(handle, lock_path)
    except AlreadyRunningError:
        handle.close()
        raise

    try:
        yield
    finally:
        try:
            if sys.platform == "win32":
                _release_windows(handle)
            else:
                _release_posix(handle)
        finally:
            handle.close()


def _config_dir(config: Config) -> Path:
    """The directory holding this config's lock + log files.

    ``load_config`` always sets ``Config.path`` (to the resolved
    ``config.toml`` location, default or ``--config``-overridden), so its
    parent is the right per-instance directory to lock/log in even when a
    non-default ``--config`` path is used.
    """
    if config.path is not None:
        return Path(config.path).parent
    from akasha.config import default_config_dir

    return default_config_dir()


def _watcher_content_hash(path: str) -> str:
    """Hash a vault file's current on-disk content for echo suppression.

    Matches ``Reconciler.on_change``'s own ``origin.record_write(path,
    object_hash(text.encode("utf-8")))`` call exactly (same canonicalize-
    then-``object_hash`` pipeline) -- a genuine daemon self-write reads
    back byte-identical, so its hash always matches the recorded one and
    ``OriginTracker.is_echo`` correctly drops it; a real external edit
    produces different canonical bytes and is never suppressed. Lazy
    imports match this module's existing deferred-import style for
    sync-related dependencies (kept out of the CLI's other, lighter verbs).
    """
    from akasha.kernel.canonical import canonicalize_text, object_hash

    text = canonicalize_text(Path(path).read_text(encoding="utf-8"))
    return object_hash(text.encode("utf-8"))


def serve(config: Config) -> None:
    """Acquire the single-instance lock, then serve the API until shutdown.

    Binds ``config.bind``/``config.port`` (default ``127.0.0.1:7433``, spec
    §3) via uvicorn. Raises :class:`AlreadyRunningError` (uncaught here) if
    another instance already holds the lock -- the CLI (T4.8/`cli/main.py`)
    is responsible for catching it and mapping it to a clean exit rather
    than a traceback.

    Before serving any request, runs the task T5.6 startup reconcile
    (spec §4.8: "Startup: run ``on_change`` for every managed file
    (idempotent -- this is also crash recovery)") against the app's shared
    connection (``app.state.conn``). This runs INSIDE the single-instance
    lock, after it is acquired -- so two daemon processes can never
    reconcile the same vault concurrently -- and BEFORE ``uvicorn.run``
    starts handling requests, matching the build-plan's "on daemon start,
    reconcile every managed file" ordering. ``sync``/``reconcile`` are
    imported lazily here (matching this module's existing deferred-import
    style for ``uvicorn``/``create_app``) so the CLI's other verbs stay
    light.

    Also starts the task T9.3/T9.3b :class:`GcScheduler` (background daily
    S0-node-retention + ``gc_objects`` tick, configured with
    ``config.s0_gc_retention_days``) right before ``uvicorn.run`` -- inside the lock, so
    it can never race a second instance's own scheduler over the same DB --
    and stops it in the same ``finally`` as the "daemon shutting down" log,
    so a clean shutdown always joins the tick thread rather than leaking it.

    Also starts the task T9.6 live filesystem :class:`~akasha.sync.watcher.Watcher`
    right after the startup reconcile, so a running daemon actually reacts
    to a vault file being edited rather than relying solely on process
    restart or a manual ``POST /v1/sync/rescan``. Uses its OWN fresh
    ``OriginTracker`` + ``Reconciler`` pair (constructed ONCE here and held
    for the watcher's entire lifetime -- never a fresh one per event, or
    echo suppression / cross-file move tracking would silently stop
    working) -- deliberately NOT the startup reconcile's tracker, matching
    ``reconcile_all``'s own docstring rationale: "a startup/rescan run has
    no live filesystem watcher to share echo-suppression state with", and
    every ``on_change`` call is idempotent regardless, so an unshared
    tracker costs at most one redundant no-op cycle, never incorrect
    state. Stopped in the same ``finally`` as ``gc_scheduler``.
    """
    import uvicorn

    from akasha.api.app import create_app
    from akasha.config import default_db_path
    from akasha.sync import reconcile
    from akasha.sync.origin import OriginTracker
    from akasha.sync.watcher import Watcher

    config_dir = _config_dir(config)
    config_dir.mkdir(parents=True, exist_ok=True)
    logger = configure_logging(config_dir / LOG_FILE_NAME)
    lock_path = config_dir / LOCK_FILE_NAME
    # Same resolution `create_app` uses internally for its own connection
    # (api/app.py) -- computed independently here (rather than read back off
    # `app.state.db_path`) so `GcScheduler` doesn't depend on `create_app`'s
    # production-only attribute, which a stubbed/injected `app` (tests) need
    # not set.
    db_path = config.db_path if config.db_path is not None else default_db_path()

    with single_instance_lock(lock_path):
        logger.info(f"daemon starting on {config.bind}:{config.port}")
        gc_scheduler = GcScheduler(
            db_path, s0_gc_retention_days=config.s0_gc_retention_days, logger=logger
        )
        try:
            app = create_app(config)
            summary = reconcile.reconcile_all(app.state.conn, OriginTracker())
            logger.info(f"startup reconcile complete: {json.dumps(summary)}")
            gc_scheduler.start()

            watch_origin = OriginTracker()
            watch_reconciler = reconcile.Reconciler(app.state.conn, watch_origin)
            watcher = Watcher(
                app.state.conn,
                watch_reconciler.on_change,
                origin_tracker=watch_origin,
                content_hash_fn=_watcher_content_hash,
                logger=logger,
            )
            watcher.start()
            try:
                uvicorn.run(app, host=config.bind, port=config.port, log_level="warning")
            finally:
                watcher.stop()
        finally:
            gc_scheduler.stop()
            logger.info("daemon shutting down")
