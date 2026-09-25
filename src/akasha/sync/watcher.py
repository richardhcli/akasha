"""Filesystem watcher: 500 ms debounce, cloud-path detection, echo suppression (spec §4.8; §6.2
E18/E19).

Three layers, each testable alone: :func:`detect_cloud_path` (a pure predicate); :class:`Debouncer`
(pure event coalescing with an injectable window and clock: tests pass ``at=`` timestamps, never
sleep); :class:`Watcher` (loads durable sync roots, flags cloud roots ``conservative`` with one
WARNING on the shared ``akasha`` logger, and on ``start()`` schedules a ``watchdog`` observer built
by an injectable ``observer_factory``). The watcher never imports ``sync.reconcile``: it calls the
``on_cycle(path)`` callback once a path's window has passed with no further activity.

Echo suppression is optional: with an ``OriginTracker`` and a ``content_hash_fn``, an event whose
content hash matches a recent daemon write is dropped before the debouncer.

``detect_cloud_path`` matches OneDrive/Dropbox markers as a case-insensitive substring of a path
SEGMENT (``OneDrive - Contoso``, ``Dropbox (Personal)``), per sync root, not per file.

Windows lock/AV tolerance (T9.1): :func:`is_transient_lock_error` and :func:`retry_with_backoff`
live here (``reconcile`` imports this module, never the reverse). ``reconcile`` wraps each OS read
and rename in the retry; :meth:`Debouncer.poll` additionally re-queues a path whose cycle still
raised a transient error after that budget. Both are plain Python, so tests fake
``OSError.winerror`` on any host.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import TYPE_CHECKING, Any, Protocol

from akasha.kernel import store
from akasha.sync.ignore import is_ignored, parse_patterns

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable

    from akasha.sync.origin import OriginTracker

# Spec §4.8: "on_change(path) after 500 ms debounce".
DEFAULT_DEBOUNCE_SECONDS = 0.5

# Matches ``Reconciler.write_if_diff``'s temp-file name (``.{name}.tmp-{16 hex}``) exactly. Every
# write-back creates and renames one under a watched root, so it is filtered before echo
# suppression and the debouncer: it is never a vault file and is usually gone by the time anything
# reads it (T9.6).
_RECONCILE_TEMP_FILE_RE = re.compile(r"^\..+\.tmp-[0-9a-f]{16}$")


# D7: the recursive watcher sees EVERY change under a root, not just Markdown. Opening the vault in
# Obsidian made ``.obsidian/workspace.json`` (rewritten on nearly every UI action) reach
# ``on_change`` and get a permanent ``sync_files`` row. Every other path into ``on_change`` is
# already ``*.md``-scoped, so a non-``.md`` path is never forwarded (case-insensitive suffix:
# Windows paths are).
def _is_managed_candidate(path: str) -> bool:
    return PurePath(path).suffix.lower() == ".md"


# D10: watchdog event types that never mean a content change (a plain read raises them on the
# inotify backend). Plain strings, not ``watchdog.events`` constants, to keep the module free of an
# import-time watchdog dependency; these three are watchdog's stable public values.
_NON_CONTENT_EVENT_TYPES = frozenset({"opened", "closed", "closed_no_write"})

# Case-insensitive marker substrings checked against each path *segment*
# (not the whole path) — see module docstring "Cloud-path detection".
_ONEDRIVE_MARKER = "onedrive"
_DROPBOX_MARKER = "dropbox"


def detect_cloud_path(path: str) -> str | None:
    """``"OneDrive"``, ``"Dropbox"`` or ``None``: a case-insensitive substring match of the
    provider markers against each path segment, so ``.../OneDrive - Contoso/vault`` is caught.
    Segment-level matching is the narrowest reading of "OneDrive/Dropbox markers" that needs no
    exact-name allowlist.
    """
    for part in PurePath(path).parts:
        lowered = part.lower()
        if _ONEDRIVE_MARKER in lowered:
            return "OneDrive"
        if _DROPBOX_MARKER in lowered:
            return "Dropbox"
    return None


# --- Windows lock / AV-noise tolerance (T9.1) --- ``OSError.winerror`` is only set on win32, so
# this never fires on POSIX; tests set it by hand. Codes: 5 ACCESS_DENIED (an AV scanner briefly
# holding a handle), 32 SHARING_VIOLATION, 33 LOCK_VIOLATION (e.g. ``daemon.py``'s own
# ``msvcrt.locking``).
TRANSIENT_WINDOWS_LOCK_ERRORS = frozenset({5, 32, 33})


def is_transient_lock_error(exc: BaseException) -> bool:
    """True iff ``exc`` is an ``OSError`` with a transient-lock ``winerror``: the default
    ``is_transient`` predicate of :func:`retry_with_backoff`, injectable so callers can narrow it.
    """
    return (
        isinstance(exc, OSError)
        and getattr(exc, "winerror", None) in TRANSIENT_WINDOWS_LOCK_ERRORS
    )


def retry_with_backoff[T](
    fn: Callable[[], T],
    *,
    attempts: int = 5,
    base_delay: float = 0.05,
    is_transient: Callable[[BaseException], bool] = is_transient_lock_error,
    sleep: Callable[[float], None] | None = None,
) -> T:
    """Call ``fn()``, retrying with exponential backoff while ``is_transient`` says so.

    At most ``attempts`` calls, sleeping ``base_delay * 2**n`` before retry n (defaults: 50, 100,
    200, 400 ms), then the last failure is raised. A non-transient exception is re-raised at once:
    this is a retry for a known-transient condition, not "swallow and hope". ``sleep`` is
    injectable.
    """
    if sleep is None:
        sleep = time.sleep
    for attempt in range(attempts):
        try:
            return fn()
        except OSError as exc:
            if attempt == attempts - 1 or not is_transient(exc):
                raise
            sleep(base_delay * (2**attempt))
    raise AssertionError("unreachable: retry_with_backoff always returns or raises")


# --- `.tmignore` deny-list (build-plan T18.10b, ruling M18-B) -----------------

# Neutral `tm` name (rule 6). The matcher itself is pure (`sync/ignore.py`);
# the file I/O lives here, the lower layer `sync.reconcile` already imports.
TMIGNORE_NAME = ".tmignore"


def load_tmignore(root_path: str, logger: logging.Logger | None = None) -> list[str]:
    """Read ``<root_path>/.tmignore`` into usable pattern lines.

    A missing or unreadable file means "defaults only" (an empty list), never an
    error: a vault without one must behave exactly as the built-in deny-list says.
    An unsupported line is skipped and logged as a warning, never guessed at.
    """
    try:
        text = (Path(root_path) / TMIGNORE_NAME).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    patterns, warnings = parse_patterns(text)
    for warning in warnings:
        (logger or logging.getLogger("akasha")).warning("%s: %s", root_path, warning)
    return patterns


def path_is_ignored(path: str, root_path: str, patterns: list[str]) -> bool:
    """True when ``path`` lies under ``root_path`` and the deny-list excludes it.

    A path outside the root is not this matcher's business (False), so callers
    keep their existing "no such sync root" handling.
    """
    try:
        rel = PurePath(path).relative_to(PurePath(root_path))
    except ValueError:
        return False
    return is_ignored(rel.as_posix(), patterns)


def iter_tracked_markdown(root_path: str, patterns: list[str]) -> list[str]:
    """Every non-ignored ``.md`` file under ``root_path``, ignored directories pruned unread."""
    found: list[str] = []
    root = str(root_path)
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = PurePath(dirpath).relative_to(PurePath(root)).as_posix()
        prefix = "" if rel_dir == "." else rel_dir + "/"
        dirnames[:] = sorted(
            d for d in dirnames if not is_ignored(prefix + d, patterns, is_dir=True)
        )
        for name in sorted(filenames):
            if not _is_managed_candidate(name):
                continue
            if not is_ignored(prefix + name, patterns):
                found.append(os.path.join(dirpath, name))
    return found


@dataclass
class WatchedRoot:
    """One durable sync root plus watcher-local state. ``conservative`` is runtime-only (recomputed
    from ``root_path`` by :meth:`Watcher.load_roots`), not a column.
    """

    id: str
    name: str
    root_path: str
    conservative: bool = False
    cloud_provider: str | None = None
    # `.tmignore` lines (T18.10b); reloaded live when that file changes.
    ignore_patterns: list[str] = field(default_factory=lambda: list[str]())


class _Scheduler(Protocol):
    """The slice of a ``watchdog`` ``BaseObserver`` this module uses, as a Protocol so tests can
    inject a spy without subclassing a watchdog class.
    """

    def schedule(
        self, event_handler: Any, path: str, *, recursive: bool = ...
    ) -> Any: ...

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def join(self, timeout: float | None = None) -> None: ...


class Debouncer:
    """Coalesces bursts of raw per-path events into one call per quiet window (trailing-edge
    debounce).

    Poll-based, with no threads or timers: :meth:`notify` records "event at time T"; :meth:`poll`
    fires every pending path whose latest event is at least ``debounce_seconds`` old, and only then
    clears the record, so a burst after a fire starts a fresh window and its own later cycle.
    :class:`Watcher` drives ``poll`` from a background thread; tests call it with explicit ``at=``
    timestamps.

    AV tolerance (T9.1): if ``on_cycle`` raises a transient lock error that outlasted its own
    retries, :meth:`poll` logs a warning and re-queues the path with a fresh window. Any other
    exception propagates.
    """

    def __init__(
        self,
        on_cycle: Callable[[str], None],
        *,
        debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS,
        now: Callable[[], float] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._on_cycle = on_cycle
        self._debounce_seconds = debounce_seconds
        self._clock = now if now is not None else _monotonic
        self._logger = logger if logger is not None else logging.getLogger("akasha")
        self._lock = threading.Lock()
        # path -> timestamp of its most recent event.
        self._pending: dict[str, float] = {}

    def notify(self, path: str, *, at: float | None = None) -> None:
        """Record a raw event for ``path``, (re)starting its debounce window."""
        current = self._current_time(at)
        with self._lock:
            self._pending[path] = current

    def poll(self, *, at: float | None = None) -> list[str]:
        """Fire ``on_cycle`` for every path whose window has elapsed; return the paths that fired
        successfully (a convenience for tests; a re-queued path is not included).
        """
        current = self._current_time(at)
        with self._lock:
            ready = [
                path
                for path, last_event_at in self._pending.items()
                if current - last_event_at >= self._debounce_seconds
            ]
            for path in ready:
                del self._pending[path]
        fired: list[str] = []
        for path in ready:
            try:
                self._on_cycle(path)
            except OSError as exc:
                if not is_transient_lock_error(exc):
                    raise
                self._logger.warning(
                    "on_cycle(%r) hit a transient Windows lock/AV-hold error "
                    "(winerror=%s); re-queuing for a later poll: %s",
                    path,
                    getattr(exc, "winerror", None),
                    exc,
                )
                with self._lock:
                    self._pending[path] = current
                continue
            fired.append(path)
        return fired

    def _current_time(self, at: float | None) -> float:
        return self._clock() if at is None else at


def _monotonic() -> float:
    import time

    return time.monotonic()


class _WatchdogEventHandler:
    """Bridges raw ``watchdog`` events into ``Watcher.notify_event``: a duck-typed adapter rather
    than a ``FileSystemEventHandler`` subclass, so the pure logic above has no import-time watchdog
    dependency.
    """

    def __init__(self, watcher: Watcher) -> None:
        self._watcher = watcher

    def dispatch(self, event: Any) -> None:
        """The method a real ``watchdog`` observer thread calls (T9.6 fix): its dispatch loop
        invokes ``handler.dispatch(event)``, never ``on_any_event``. Without it a real ``Observer``
        died on the first genuine event, undetected because every test drove ``on_any_event``
        through a spy. Every event kind goes through ``on_any_event``.
        """
        self.on_any_event(event)

    def on_any_event(self, event: Any) -> None:
        """Route a real ``watchdog`` event's path(s) to the debounce pipeline.

        Paths are normalized through ``str(PurePath(...))`` (T9.6, found live): a root registered
        as ``"C:/Users/x/vault"`` plus a ``ReadDirectoryChangesW`` name joined with a backslash
        gave ``"C:/Users/x/vault\\note.md"``, a different string than discovery's all-native form
        for the same file. ``sync_files.path`` is keyed on that string, so the file was
        double-tracked.
        """
        if getattr(event, "is_directory", False):
            return
        # D10: a plain file read still raises "opened"/"closed"/"closed_no_write" on this inotify
        # backend. Forwarding them was a self-sustaining loop: echo suppression reads the file to
        # hash it, that read raises its own open/close events, which re-enter ``notify_event``,
        # forever; every event re-armed the debounce, so a real edit never reconciled. Only these
        # three are dropped; a genuine write still raises "modified"/"created" alongside them.
        if getattr(event, "event_type", None) in _NON_CONTENT_EVENT_TYPES:
            return
        src_path = str(PurePath(str(event.src_path)))
        raw_dest = str(getattr(event, "dest_path", "") or "")
        dest_path = str(PurePath(raw_dest)) if raw_dest else ""
        for candidate in (src_path, dest_path):
            if not candidate:
                continue
            name = PurePath(candidate).name
            if name == TMIGNORE_NAME:
                # T18.10b: reload the deny-list and rescan; never itself reconciled.
                self._watcher.on_tmignore_event(candidate)
            elif (
                not _RECONCILE_TEMP_FILE_RE.match(name)
                and _is_managed_candidate(candidate)
                and self._watcher.is_tracked_path(candidate)
            ):
                self._watcher.notify_event(candidate)


def _default_observer_factory() -> _Scheduler:
    from watchdog.observers import Observer

    return Observer()


@dataclass
class Watcher:
    """Loads durable sync roots, watches them, and debounces raw filesystem events.

    ``Watcher(conn, on_cycle, *, debounce_seconds=0.5, now=None, origin_tracker=None,
    content_hash_fn=None, observer_factory=<watchdog Observer>, logger=None)``. ``conn`` is only
    read (``store.list_sync_roots``). ``on_cycle(path)`` is the reconcile seam. ``origin_tracker``
    and ``content_hash_fn`` enable echo suppression only when both are given. ``logger`` defaults
    to ``akasha``.

    ``load_roots()`` re-reads the roots and flags cloud paths (no observer needed; E19 tests use
    it); ``roots`` maps sync-root id to :class:`WatchedRoot`; ``start()`` / ``stop()`` run the
    observer and the poll thread; ``notify_event(path, at=)`` feeds one raw event (tests simulate
    FS events with it); ``poll(at=)`` fires elapsed windows.
    """

    conn: sqlite3.Connection
    on_cycle: Callable[[str], None]
    debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS
    now: Callable[[], float] | None = None
    origin_tracker: OriginTracker | None = None
    content_hash_fn: Callable[[str], str] | None = None
    observer_factory: Callable[[], _Scheduler] = field(default=_default_observer_factory)
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("akasha"))
    # T9.6: ``start()`` owns the poll thread that fires debounced events (before, nothing called
    # ``poll()`` on a timer, so no event ever fired in a real daemon). The interval is 5x the
    # window's worth of latency at most; not a kwarg since no caller has needed to tune it.
    poll_interval_seconds: float = 0.1

    def __post_init__(self) -> None:
        self._debouncer = Debouncer(
            self.on_cycle,
            debounce_seconds=self.debounce_seconds,
            now=self.now,
            logger=self.logger,
        )
        self._roots: dict[str, WatchedRoot] = {}
        # Serializes `watch_new_roots`: it is called from the poll thread AND, at
        # registration time, from an API request thread.
        self._roots_lock = threading.Lock()
        self._observer: _Scheduler | None = None
        self._handler: _WatchdogEventHandler | None = None
        self._poll_stop_event = threading.Event()
        self._poll_thread: threading.Thread | None = None

    @property
    def roots(self) -> dict[str, WatchedRoot]:
        return dict(self._roots)

    def _build_watched_root(self, row: Mapping[str, Any]) -> WatchedRoot:
        root_path = row["root_path"]
        provider = detect_cloud_path(root_path)
        conservative = provider is not None
        if conservative:
            self.logger.warning(
                "sync root %r (%s) at %r is under a %s-synced path; "
                "enabling conservative reconcile profile",
                row["name"],
                row["id"],
                root_path,
                provider,
            )
        return WatchedRoot(
            id=row["id"],
            name=row["name"],
            root_path=root_path,
            conservative=conservative,
            cloud_provider=provider,
            ignore_patterns=load_tmignore(root_path, self.logger),
        )

    def load_roots(self) -> list[WatchedRoot]:
        """Load durable sync roots and run cloud-path detection on each.

        Step 1 (load) + step 3 (cloud detection ⇒ warn + conservative
        flag) of this task's build-plan Steps. Read-only against SQLite
        (``kernel.store.list_sync_roots`` — rule 0.4).
        """
        rows = store.list_sync_roots(self.conn)
        self._roots = {row["id"]: self._build_watched_root(row) for row in rows}
        return list(self._roots.values())

    def watch_new_roots(self, rows: Sequence[Mapping[str, Any]] | None = None) -> None:
        """Watch any sync root registered AFTER :meth:`start` (T9.6; ``load_roots`` used to run
        once).

        Called every poll tick (one cheap query; zero new roots is the common case) and
        synchronously by ``POST /v1/sync/roots`` before it responds (D11): the tick alone left a
        window in which a file written right after registration was neither in the caller's rescan
        nor seen by an event, and was lost until restart. Watch first, scan second: anything
        earlier is found by the scan, anything later raises an event. ``rows`` are the roots as the
        CALLER read them: the request thread passes rows from its own connection so it never
        touches ``self.conn``, which the poll thread's cycles use.
        """
        if self._observer is None or self._handler is None:
            return
        with self._roots_lock:
            for row in store.list_sync_roots(self.conn) if rows is None else rows:
                if row["id"] in self._roots:
                    continue
                watched = self._build_watched_root(row)
                # Recorded BEFORE scheduling so the first event (delivered on
                # watchdog's own thread the instant the watch exists) finds its root.
                self._roots[watched.id] = watched
                try:
                    self._observer.schedule(self._handler, watched.root_path, recursive=True)
                except Exception:
                    del self._roots[watched.id]  # not watched: the next call retries it
                    raise

    def start(self) -> None:
        """Start watching every sync root's ``root_path`` and the poll thread that fires
        ``on_cycle`` (T9.6); idempotent.
        """
        if self._poll_thread is not None:
            return
        if not self._roots:
            self.load_roots()
        observer = self.observer_factory()
        handler = _WatchdogEventHandler(self)
        for root in self._roots.values():
            observer.schedule(handler, root.root_path, recursive=True)
        observer.start()
        self._observer = observer
        self._handler = handler

        self._poll_stop_event.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop, name="akasha-watcher-poll", daemon=True
        )
        self._poll_thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the poll thread first (no new cycles), then the observer. A path inside an
        unexpired window does not fire; the next startup ``reconcile_all`` picks it up like any
        edit made while down.
        """
        self._poll_stop_event.set()
        if self._poll_thread is not None:
            self._poll_thread.join(timeout=timeout)
            self._poll_thread = None
        if self._observer is not None:
            self._observer.stop()
            self._observer.join()
            self._observer = None
        self._handler = None

    def _poll_loop(self) -> None:
        while True:
            try:
                self.watch_new_roots()
                self.poll()
            except Exception:
                # A failed cycle must never crash the watcher's poll thread
                # (or, transitively, the daemon) -- log and keep polling.
                # `Debouncer.poll` itself already re-queues on a *transient*
                # lock/AV error (T9.1); this is the backstop for anything
                # else `on_cycle` might raise.
                self.logger.exception("watcher poll cycle failed")
            if self._poll_stop_event.wait(self.poll_interval_seconds):
                return

    def _root_of(self, path: str) -> WatchedRoot | None:
        """The loaded root whose ``root_path`` is the longest prefix of ``path``."""
        best: WatchedRoot | None = None
        # Snapshot: this runs on watchdog's dispatch thread while the poll thread's
        # `watch_new_roots` may insert a freshly registered root into the dict.
        for root in list(self._roots.values()):
            try:
                PurePath(path).relative_to(PurePath(root.root_path))
            except ValueError:
                continue
            if best is None or len(root.root_path) > len(best.root_path):
                best = root
        return best

    def is_tracked_path(self, path: str) -> bool:
        """False for a path the root's `.tmignore` deny-list excludes (T18.10b)."""
        root = self._root_of(path)
        return root is None or not path_is_ignored(path, root.root_path, root.ignore_patterns)

    def on_tmignore_event(self, path: str) -> None:
        """A root's ``.tmignore`` changed: reload its patterns and rescan the root through the
        debouncer, so a file just un-ignored is adopted and one just ignored is left alone
        (reconcile is idempotent).
        """
        for root in list(self._roots.values()):
            if PurePath(path) != PurePath(root.root_path) / TMIGNORE_NAME:
                continue
            root.ignore_patterns = load_tmignore(root.root_path, self.logger)
            for candidate in iter_tracked_markdown(root.root_path, root.ignore_patterns):
                self.notify_event(str(PurePath(candidate)))

    def notify_event(self, path: str, *, at: float | None = None) -> None:
        """Feed one raw filesystem-change ``path`` into the debounce pipeline.

        With echo suppression wired, an event matching a recent daemon write is dropped. This runs
        on watchdog's own dispatch thread with no backstop, so an uncaught exception would silently
        kill the observer for good. ``content_hash_fn`` reads the file and can race the filesystem
        (an editor's atomic-save temp file, a file deleted after the event); if the read fails we
        cannot prove an echo, so the event is forwarded (worst case: one idempotent zero-diff
        cycle).
        """
        if self.origin_tracker is not None and self.content_hash_fn is not None:
            try:
                content_hash = self.content_hash_fn(path)
            except OSError:
                content_hash = None
            if content_hash is not None and self.origin_tracker.is_echo(path, content_hash):
                return
        self._debouncer.notify(path, at=at)

    def poll(self, *, at: float | None = None) -> list[str]:
        """Fire ``on_cycle`` for every path whose debounce window has elapsed."""
        return self._debouncer.poll(at=at)
