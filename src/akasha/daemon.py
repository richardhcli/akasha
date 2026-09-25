"""Process lifecycle: structured logging, the single-instance lock, and serve (spec §4.12).

The lock file is ``tm-daemon.lock`` in the config directory (a neutral name, rule 0.6). Locking is
cross-platform (``fcntl.flock`` on POSIX, ``msvcrt.locking`` on Windows), non-blocking, so a second
instance fails fast with :class:`AlreadyRunningError` instead of hanging.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
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
# build-plan T18.3 (neutral name, rule 6): the running daemon's pid, written
# after the lock is held and removed on shutdown. `akasha down` reads it.
PID_FILE_NAME = "tm-daemon.pid"
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
    """Configure the shared ``"akasha"`` logger with a size-rotating file handler. ``max_bytes``
    and ``backup_count`` are keyword-only so tests can drive rotation with a tiny file.
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
    """Runs ``kernel.store.gc_objects`` and the age-based S0 retention GC on a background daily
    tick.

    Each tick (1) hard-deletes live S0 nodes older than ``s0_gc_retention_days`` through the
    existing ``delete_node`` S0 branch (never an S1+ node: ``list_expired_s0_node_ids`` filters on
    S0/live and ``delete_node`` re-checks maturity), then (2) runs ``gc_objects`` so the objects
    those deletions orphaned are reclaimed in the same tick. No SQL lives here (rule 0.4).

    A tick opens its own short-lived connection (like ``api/deps.py::get_conn``): one connection
    shared across threads is unsafe, and WAL is built for many short-lived ones. :meth:`start`
    ticks at once, then every ``interval_seconds`` (injectable) until :meth:`stop`;
    :meth:`run_once` runs one tick synchronously.
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

    # ``msvcrt.locking`` locks a byte range from the current position, so the file must hold at
    # least one byte first. The whole sequence, not just ``locking()``, must sit inside the
    # ``try``: on Windows even ``handle.read(1)`` raises ``PermissionError`` when another handle
    # holds the range (found only when run on Windows).
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
    """Hold an exclusive, non-blocking OS lock on ``lock_path``; raise :class:`AlreadyRunningError`
    at once if another process holds it. The lock is released on exit, normal or exceptional.
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
    """Hash a vault file's on-disk content for echo suppression, with the same canonicalize-then-
    ``object_hash`` pipeline ``Reconciler`` records writes with: a daemon self-write reads back
    identical and is dropped, a real external edit is not. Lazy imports keep the CLI's other verbs
    light.
    """
    from akasha.kernel.canonical import canonicalize_text, object_hash

    text = canonicalize_text(Path(path).read_text(encoding="utf-8"))
    return object_hash(text.encode("utf-8"))


def serve(config: Config) -> None:
    """Acquire the single-instance lock, then serve the API until shutdown.

    Binds ``config.bind``:``config.port`` via uvicorn. :class:`AlreadyRunningError` propagates (the
    CLI maps it to a clean exit). Inside the lock, before any request: the startup
    ``reconcile_all`` (spec §4.8; also crash recovery), so two daemons can never reconcile one
    vault at once. Then the :class:`GcScheduler` and the live
    :class:`~akasha.sync.watcher.Watcher`, both stopped in the same ``finally`` as the shutdown
    log.

    The watcher uses ONE ``Reconciler`` for its whole lifetime (a fresh one per event would lose
    cross-file move tracking), built around ``app.state.origin_tracker``: the same tracker the
    request path's re-projections write through (T13.3), so a ``PATCH /v1/nodes/{id}`` write-back
    is recognized as an echo and starts no second cycle. The startup reconcile deliberately uses
    its own tracker: it has no watcher to share state with, and an unsuppressed echo costs one
    idempotent no-op cycle.
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
        # T18.3: written only once the lock is ours, so a second instance (which
        # fails at the line above) can never overwrite or delete the first's file.
        pid_path = config_dir / PID_FILE_NAME
        pid_path.write_text(f"{os.getpid()}\n", encoding="utf-8")
        logger.info(f"daemon starting on {config.bind}:{config.port}")
        gc_scheduler = GcScheduler(
            db_path, s0_gc_retention_days=config.s0_gc_retention_days, logger=logger
        )
        try:
            app = create_app(config)
            summary = reconcile.reconcile_all(app.state.conn, OriginTracker())
            logger.info(f"startup reconcile complete: {json.dumps(summary)}")
            gc_scheduler.start()

            # T13.3: share ``app.state.origin_tracker`` instead of building a second tracker. The
            # ``getattr`` fallback only serves a stubbed ``app`` in tests; ``create_app`` always
            # sets it.
            watch_origin = getattr(app.state, "origin_tracker", None) or OriginTracker()
            watch_reconciler = reconcile.Reconciler(app.state.conn, watch_origin)
            watcher = Watcher(
                app.state.conn,
                watch_reconciler.on_change,
                origin_tracker=watch_origin,
                content_hash_fn=_watcher_content_hash,
                logger=logger,
            )
            # D11: `POST /v1/sync/roots` asks this watcher to start on a new root
            # synchronously, before it answers.
            app.state.watcher = watcher
            watcher.start()
            try:
                uvicorn.run(app, host=config.bind, port=config.port, log_level="warning")
            finally:
                watcher.stop()
        finally:
            gc_scheduler.stop()
            logger.info("daemon shutting down")
            pid_path.unlink(missing_ok=True)


# --- detached lifecycle: `akasha up` / `akasha down` (build-plan T18.3) ---------

UP_TIMEOUT_SECONDS = 30.0
DOWN_TIMEOUT_SECONDS = 10.0


def lock_is_held(lock_path: str | Path) -> bool:
    """True iff another process currently holds the single-instance lock.

    Probes by trying to take the lock and releasing it again at once. Never
    creates the config directory: a missing lock file means nobody holds it.
    """
    lock_path = Path(lock_path)
    if not lock_path.exists():
        return False
    try:
        with single_instance_lock(lock_path):
            return False
    except AlreadyRunningError:
        return True


def health_url(config: Config) -> str:
    return f"http://{config.bind}:{config.port}"


def is_healthy(config: Config, *, timeout: float = 1.0) -> bool:
    """True iff ``GET /health`` answers 200 at the config's bind:port."""
    import httpx

    try:
        return httpx.get(f"{health_url(config)}/health", timeout=timeout).status_code == 200
    except httpx.HTTPError:
        return False


def _spawn_command(config: Config) -> list[str]:
    config_path = str(config.path) if config.path is not None else None
    tail = ["daemon", *(["--config", config_path] if config_path else [])]
    if getattr(sys, "frozen", False):
        return [sys.executable, *tail]
    return [sys.executable, "-m", "akasha.cli.main", *tail]


def spawn_detached(config: Config) -> subprocess.Popen[bytes]:
    """Start ``akasha daemon`` detached from this terminal, stdio to the null device.

    The daemon writes its own rotating log in the config dir, so nothing is lost.
    """
    kwargs: dict[str, object] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(  # noqa: S603 - argv is our own interpreter + fixed verbs
        _spawn_command(config),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        **kwargs,  # type: ignore[arg-type]
    )


class UpResult:
    """Outcome of :func:`up`: ``status`` is ``already``/``started``/``conflict``/``failed``."""

    def __init__(self, status: str, url: str, log_path: Path, pid: int | None = None) -> None:
        self.status = status
        self.url = url
        self.log_path = log_path
        self.pid = pid


def up(config: Config, *, timeout: float = UP_TIMEOUT_SECONDS) -> UpResult:
    """Make sure a daemon is serving ``config``'s address; idempotent.

    ``already`` if ``/health`` answers; ``conflict`` if the lock is held but
    ``/health`` is silent (something else owns it -- spec §4.12's conflict
    class); otherwise spawn detached and poll ``/health`` for ``timeout``
    seconds (``started``, or ``failed`` if the child exits or never answers).
    """
    config_dir = _config_dir(config)
    url = health_url(config)
    log_path = config_dir / LOG_FILE_NAME
    if is_healthy(config):
        return UpResult("already", url, log_path, _read_pid(config_dir))
    if lock_is_held(config_dir / LOCK_FILE_NAME):
        return UpResult("conflict", url, log_path, _read_pid(config_dir))
    proc = spawn_detached(config)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_healthy(config):
            return UpResult("started", url, log_path, proc.pid)
        if proc.poll() is not None:
            return UpResult("failed", url, log_path)
        time.sleep(0.1)
    return UpResult("failed", url, log_path, proc.pid)


def _read_pid(config_dir: Path) -> int | None:
    try:
        return int((config_dir / PID_FILE_NAME).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def down(config: Config, *, timeout: float = DOWN_TIMEOUT_SECONDS) -> str:
    """Stop the detached daemon; returns ``not-running``, ``stopped`` or ``timeout``.

    The single-instance lock, held exactly as long as the daemon lives, is the source of truth for
    "is it running", not the pid file: a stale pid file (its number may have been reused) is
    deleted, never signalled. An abrupt stop is safe: startup reconcile is idempotent.
    """
    config_dir = _config_dir(config)
    lock_path = config_dir / LOCK_FILE_NAME
    pid_path = config_dir / PID_FILE_NAME
    if not lock_is_held(lock_path):
        pid_path.unlink(missing_ok=True)
        return "not-running"
    pid = _read_pid(config_dir)
    if pid is None:
        return "timeout"  # locked by something that left no pid: never guess at a target
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:  # already gone (ProcessLookupError, or a generic OSError on Windows)
        pass
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not lock_is_held(lock_path):
            pid_path.unlink(missing_ok=True)
            return "stopped"
        time.sleep(0.1)
    return "timeout"
