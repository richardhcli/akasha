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
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import IO, TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator

    from akasha.config import Config

LOCK_FILE_NAME = "tm-daemon.lock"
LOG_FILE_NAME = "daemon.log"


class JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        return json.dumps(payload)


def configure_logging(log_file: str | Path, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger("akasha")
    logger.setLevel(level)
    logger.handlers.clear()

    formatter = JsonLineFormatter()

    file_handler = RotatingFileHandler(log_file, maxBytes=10_000_000, backupCount=5)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    return logger


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
    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        raise AlreadyRunningError(lock_path) from exc


def _release_posix(handle: IO[bytes]) -> None:
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
    handle.seek(0)
    if not handle.read(1):
        handle.seek(0)
        handle.write(b"\0")
        handle.flush()
    handle.seek(0)
    try:
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
    """
    import uvicorn

    from akasha.api.app import create_app
    from akasha.sync import reconcile
    from akasha.sync.origin import OriginTracker

    config_dir = _config_dir(config)
    config_dir.mkdir(parents=True, exist_ok=True)
    logger = configure_logging(config_dir / LOG_FILE_NAME)
    lock_path = config_dir / LOCK_FILE_NAME

    with single_instance_lock(lock_path):
        logger.info(f"daemon starting on {config.bind}:{config.port}")
        try:
            app = create_app(config)
            summary = reconcile.reconcile_all(app.state.conn, OriginTracker())
            logger.info(f"startup reconcile complete: {json.dumps(summary)}")
            uvicorn.run(app, host=config.bind, port=config.port, log_level="warning")
        finally:
            logger.info("daemon shutting down")
