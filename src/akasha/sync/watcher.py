"""Filesystem watcher: 500 ms debounce + OneDrive/Dropbox cloud-path detection.

Task T5.3 (spec §4.8 ``on_change(path) after 500 ms debounce``; M5 milestone
text: "watcher with debounce + cloud-path detection (warn + conservative
profile when path is under OneDrive/Dropbox markers)"; §6.2 E18 rapid
modify bursts ⇒ debounce, single cycle; E19 vault under simulated OneDrive
path ⇒ warning + conservative profile).

Design: pure logic vs. I/O wiring
-----------------------------------
Per this task's testability requirement, the module is split into three
independently testable layers:

1. :func:`detect_cloud_path` — a pure predicate over a path string; no I/O.
2. :class:`Debouncer` — pure event-coalescing logic. Both the debounce
   window and the clock are injectable (mirrors ``api/auth.py``'s
   ``check_rate_limit(..., now=...)`` pattern and ``sync/origin.py``'s
   ``OriginTracker``): tests drive it with explicit timestamps via the
   ``at=`` keyword on :meth:`Debouncer.notify`/:meth:`Debouncer.poll`,
   never a real ``time.sleep``. It knows nothing about ``watchdog`` or
   SQLite.
3. :class:`Watcher` — the wiring layer. Loads durable sync roots via
   ``kernel.store.list_sync_roots`` (T4.10), detects cloud paths per root
   (step 3), and — only when :meth:`Watcher.start` is called — creates a
   ``watchdog`` ``Observer`` (via an injectable ``observer_factory``, so
   tests can substitute a spy instead of a real OS-level observer thread)
   that feeds raw filesystem events into the ``Debouncer``.

Reconcile routing (step 4)
----------------------------
T5.4's reconcile pipeline does not exist yet. The ``Watcher`` never
imports ``sync.reconcile``; instead its constructor takes an
``on_cycle: Callable[[str], None]`` callback invoked with the affected
file path once its debounce window has elapsed with no further activity.
T5.4 supplies the real callback (its ``on_change(path)`` entry point);
until then callers (and this task's tests) pass a spy.

Echo suppression (optional wiring)
------------------------------------
The ``Watcher`` optionally accepts a T5.2 ``OriginTracker`` plus a
``content_hash_fn: Callable[[str], str]``. When both are supplied, a raw
filesystem event first checks ``origin_tracker.is_echo(path, hash)``; a
matching echo (the daemon's own recent write) is dropped before it ever
reaches the debouncer, so it never produces a spurious reconcile cycle.
Without a hash function/tracker (the default), every raw event is
debounced and forwarded — echo suppression is opt-in, not required for
this task's DoD.

Cloud-path detection and the conservative profile
----------------------------------------------------
``detect_cloud_path`` matches on OneDrive/Dropbox markers appearing as a
path *segment* (case-insensitive substring of a path component — e.g.
``OneDrive``, ``OneDrive - Contoso``, ``Dropbox (Personal)`` all match),
never on marker text that merely appears inside an unrelated component
name, avoiding false positives such as a hub folder literally named
``MyOneDriveDoc.md``. ``Watcher.load_roots`` runs this once per
registered sync root's ``root_path`` (root granularity, per the
build-plan step "Detect cloud markers in an Obsidian vault path" — a
vault path *is* a sync root's ``root_path``, not a per-file check); a
match logs one WARNING via the shared ``akasha`` logger (the same
logger ``daemon.py::configure_logging`` configures — this module never
``print``s) and sets that root's ``conservative`` flag to ``True``. The
flag is exposed on the ``WatchedRoot`` dataclass so T5.4's reconcile
pipeline can read it (e.g. to be more cautious about certain-repairs
under a cloud-sync provider's own eventual-consistency window) without
this module needing to know anything about reconcile policy.

Windows locking-retry / AV-noise tolerance (build-plan T9.1)
----------------------------------------------------------------
:func:`is_transient_lock_error` and :func:`retry_with_backoff` live here
(the lower dependency layer — ``sync.reconcile`` already imports
:func:`detect_cloud_path` from this module, never the reverse) so both
this module and ``reconcile.py`` share one classifier/backoff
implementation. Split across the two files by WHERE the OS-level file
I/O each layer owns actually happens:

- ``reconcile.py``'s ``Reconciler.on_change``/``write_if_diff`` own the
  actual OS-level file reads/writes (spec §4.8's ``write_if_diff`` is the
  canonical write-back primitive) — those call sites wrap each
  individual read/replace in :func:`retry_with_backoff` for a short,
  tight retry budget (build-plan Step 1, "Retry-with-backoff on Windows
  sharing-violation/locked-file errors").
- This module's :class:`Debouncer` — the layer that actually *invokes*
  ``on_cycle`` (a full reconcile cycle) — additionally catches a
  transient-lock error that survives ``on_change``'s own short retry
  budget (e.g. an AV scan that outlasts it) and RE-QUEUES the path for
  the next debounce window instead of losing it or crashing whatever
  drives the poll loop (build-plan Step 2, "Tolerate transient AV-held
  handles"). See :meth:`Debouncer.poll`.

Both the classifier and the backoff loop are plain Python (no
``msvcrt``/platform import — matching this module's existing "pure logic
vs. I/O wiring" split), so they and the ``Debouncer`` re-queue behavior
are fully unit-testable on any host: a test constructs a fake
``OSError`` with ``.winerror`` set to a known Windows sharing-
violation/lock-violation/access-denied code (see
``tests/battery/test_windows.py``) rather than requiring a real Windows
filesystem.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import TYPE_CHECKING, Any, Protocol

from akasha.kernel import store

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable

    from akasha.sync.origin import OriginTracker

# Spec §4.8: "on_change(path) after 500 ms debounce".
DEFAULT_DEBOUNCE_SECONDS = 0.5

# Matches sync/reconcile.py's Reconciler.write_if_diff naming scheme
# EXACTLY (`f".{target.name}.tmp-{secrets.token_hex(8)}"`) -- build-plan
# T9.6: every real write-back the daemon makes creates-then-renames-away
# one of these under a watched root, so a live Watcher observes it
# constantly in normal operation, not as a rare edge case. Filtered out
# before it ever reaches notify_event/echo-suppression/the debouncer:
# there is nothing to reconcile about a transient temp file that is
# usually already gone (renamed to its final name) by the time anything
# downstream would try to read it, and it is never itself a managed
# vault file.
_RECONCILE_TEMP_FILE_RE = re.compile(r"^\..+\.tmp-[0-9a-f]{16}$")

# debug-plan Dx: the live watcher recursively observes EVERY filesystem
# change under a sync root's root_path (watchdog is scheduled with
# recursive=True over the whole tree, module-level in Watcher.start), not
# just managed contract files. Every OTHER path into on_change is already
# *.md-scoped: discover_untracked_files (T11.3) walks
# ``Path(root_path).rglob("*.md")`` and reconcile_all only replays rows
# already in ``sync_files`` (which themselves only ever entered via that
# same *.md-scoped discovery or an earlier *.md-filtered watcher event).
# This module's raw watchdog bridge was the one path with no such filter.
# Found via dogfooding: opening the fixture vault in Obsidian caused
# ``.obsidian/workspace.json`` (Obsidian's own app-state file, rewritten on
# nearly every UI interaction -- pane focus, scroll position, ...) to be
# forwarded straight to ``Reconciler.on_change``, which read + parsed it as
# contract text (an empty BlockSet, since it is JSON, not markdown) and
# permanently inserted a ``sync_files`` row for it -- polluting the Sync
# view's "files: N" count with an Obsidian-internal file the daemon has no
# business managing, and burning a real reconcile cycle (file read + parse
# + hub_state_for + write-back diff) on every one of Obsidian's own saves.
# The narrowest fix matching the existing *.md convention everywhere else
# in this module: never forward a non-``.md`` path past the watchdog
# boundary. Suffix compared case-insensitively since Windows paths (this
# project's primary dogfood platform, per README) are case-insensitive.
def _is_managed_candidate(path: str) -> bool:
    return PurePath(path).suffix.lower() == ".md"

# debug-plan D10: watchdog event-type strings that never represent a content
# change -- a plain read-without-write raises exactly these on this
# platform's inotify backend. Compared as plain strings (not
# ``watchdog.events.EVENT_TYPE_*`` constants) to keep this module's stated
# "no import-time dependency on watchdog beyond Watcher.start" design goal
# (see this module's own docstring) -- these three literal values are
# watchdog's own stable public API surface (``watchdog/events.py``), not an
# implementation detail likely to drift.
_NON_CONTENT_EVENT_TYPES = frozenset({"opened", "closed", "closed_no_write"})

# Case-insensitive marker substrings checked against each path *segment*
# (not the whole path) — see module docstring "Cloud-path detection".
_ONEDRIVE_MARKER = "onedrive"
_DROPBOX_MARKER = "dropbox"


def detect_cloud_path(path: str) -> str | None:
    """Return ``"OneDrive"``, ``"Dropbox"``, or ``None`` for an ordinary local path.

    Checks every path segment (``PurePath(path).parts``) for a
    case-insensitive substring match against the known provider markers,
    so both ``.../OneDrive/vault`` and ``.../OneDrive - Contoso/vault``
    (a real OneDrive-for-Business folder-naming convention) are detected,
    while a segment that merely contains the substring as part of an
    unrelated word (e.g. a file literally named ``dropboxes.md``) is
    still a match at the segment level by design — the spec only asks for
    "OneDrive/Dropbox markers", and segment-level substring matching is
    the narrowest reading that still catches the documented real-world
    folder-naming variants without requiring an exact-name allowlist the
    spec never specifies (§ narrowest reading, build-plan rule 0.2).
    """
    for part in PurePath(path).parts:
        lowered = part.lower()
        if _ONEDRIVE_MARKER in lowered:
            return "OneDrive"
        if _DROPBOX_MARKER in lowered:
            return "Dropbox"
    return None


# --- Windows locking-retry / AV-noise tolerance (build-plan T9.1) -----------
#
# See module docstring section of the same name for the reconcile.py vs.
# watcher.py split. ``OSError.winerror`` is only ever populated by the OS on
# win32; on POSIX no exception ever carries it, so this classifier never
# fires on a real Linux/macOS host. Tests simulate the Windows condition by
# constructing a plain ``OSError``/``PermissionError`` and setting
# ``.winerror`` manually -- a normal instance attribute, settable on any
# platform -- rather than requiring a real Windows filesystem.
#
# Codes covered: ERROR_ACCESS_DENIED (5, commonly surfaced when an AV
# scanner briefly holds an exclusive handle open on a just-changed file --
# this task's "AV noise"), ERROR_SHARING_VIOLATION (32, another process has
# the file open without FILE_SHARE_READ/WRITE), ERROR_LOCK_VIOLATION (33, a
# byte-range lock -- e.g. ``daemon.py``'s own ``msvcrt.locking`` -- is held
# by another handle). This is an implementation-level mapping of the
# build-plan's plain-English "sharing-violation/locked-file errors" text,
# not a new schema/grammar element (build-plan rule 0.2).
TRANSIENT_WINDOWS_LOCK_ERRORS = frozenset({5, 32, 33})


def is_transient_lock_error(exc: BaseException) -> bool:
    """True iff ``exc`` is an ``OSError`` carrying a transient-lock ``winerror``.

    The default ``is_transient=`` predicate for :func:`retry_with_backoff`;
    passed as a plain callable (not hardcoded into the retry loop) so a
    caller/test can substitute a narrower or wider classification without
    touching the loop itself.
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

    Up to ``attempts`` total calls to ``fn`` (i.e. up to ``attempts - 1``
    retries after the first attempt); sleeps ``base_delay * 2**n`` before
    retry number ``n`` (0-indexed) -- with the defaults: 50ms, 100ms, 200ms,
    400ms between the 5 attempts, then the final failure is raised. An
    exception ``is_transient`` classifies as NOT transient (e.g. a genuine
    permission error, or the file simply missing) is re-raised immediately
    on the very first occurrence, with no delay and no retry -- this is
    explicitly a retry for a known-transient condition, never a generic
    "swallow and hope" loop. ``sleep`` is injectable (defaults to
    ``time.sleep``) so a test can assert on the exact backoff schedule
    without a real wall-clock wait.
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


@dataclass
class WatchedRoot:
    """One durable sync root (T4.10 ``sync_roots`` row) plus watcher-local state.

    ``conservative`` is process-local, runtime-only state (not a
    ``sync_roots`` column — no migration needed for this task): it is
    recomputed from ``root_path`` every time :meth:`Watcher.load_roots`
    runs, exactly like ``sync/origin.py``'s in-memory bookkeeping is
    intentionally non-persistent (see that module's docstring).
    """

    id: str
    name: str
    root_path: str
    conservative: bool = False
    cloud_provider: str | None = None


class _Scheduler(Protocol):
    """Minimal surface of a ``watchdog`` ``BaseObserver`` this module needs.

    Declared as a ``Protocol`` (rather than importing ``watchdog.observers
    .api.BaseObserver`` directly for the type) so :class:`Watcher`'s
    ``observer_factory`` can be swapped for a lightweight test spy without
    that spy needing to subclass a real ``watchdog`` class.
    """

    def schedule(
        self, event_handler: Any, path: str, *, recursive: bool = ...
    ) -> Any: ...

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def join(self, timeout: float | None = None) -> None: ...


class Debouncer:
    """Coalesces a burst of raw per-path events into one call per quiet window.

    Poll-based (not ``threading.Timer``-based) by design: :meth:`notify`
    only records "this path had an event at time T"; :meth:`poll` is the
    single place a cycle actually fires, and it fires for every pending
    path whose *most recent* event is at least ``debounce_seconds`` in
    the past (i.e. no further events arrived during the window — a
    classic trailing-edge debounce, not a fixed-delay one-shot). This
    keeps the whole class free of real threads/timers/sleeps, so a test
    can call :meth:`notify` and :meth:`poll` with explicit ``at=``
    timestamps and get fully deterministic results (mirrors
    ``api/auth.py``'s ``check_rate_limit(..., now=...)`` injectable-clock
    pattern). :class:`Watcher` drives a real :meth:`poll` loop from a
    background thread in production (see :meth:`Watcher.start`); tests
    never need that thread.

    Because the record for a path is only cleared once it actually fires
    in :meth:`poll`, a fresh burst of events arriving *after* a fire is
    treated as a brand-new window producing its own, later cycle — the
    debounce resets rather than being a one-shot per path.

    AV-noise tolerance (build-plan T9.1, module docstring section of the
    same name): if invoking ``on_cycle`` itself raises a transient
    Windows lock/AV-hold error (:func:`is_transient_lock_error`) — i.e.
    one that survived ``on_cycle``'s OWN short retry budget (``reconcile
    .py``'s ``retry_with_backoff`` calls around its OS-level reads/
    writes) — :meth:`poll` catches it, logs a warning, and RE-QUEUES the
    path with a fresh debounce window starting now, rather than losing
    the path or propagating the exception out of the poll loop. A
    non-transient exception is never swallowed; it propagates exactly as
    before this task.
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
        """Fire ``on_cycle`` for every path whose window has quietly elapsed.

        Returns the list of paths that successfully fired ``on_cycle``
        this call (empty if none are ready yet), purely as a convenience
        for assertions in tests — production callers (:class:`Watcher`'s
        poll loop) can ignore it. A path re-queued after a transient
        lock/AV-hold error (see class docstring) is NOT included — it
        did not successfully complete this call.
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
    """Bridges raw ``watchdog`` filesystem events into ``Watcher.notify_event``.

    Kept as a tiny, untyped-against-watchdog-internals adapter (duck-typed
    ``on_any_event(event)``, the method ``watchdog.events
    .FileSystemEventHandler`` dispatches every event kind to) rather than
    subclassing ``FileSystemEventHandler`` directly, so this module's pure
    logic above never has an import-time dependency on ``watchdog``
    beyond :meth:`Watcher.start`, matching the "pure logic testable
    without it" design goal.
    """

    def __init__(self, watcher: Watcher) -> None:
        self._watcher = watcher

    def dispatch(self, event: Any) -> None:
        """The REAL entry point a ``watchdog`` observer thread calls (build-plan
        T9.6 fix): ``watchdog.observers.api``'s dispatch loop calls
        ``handler.dispatch(event)``, never ``on_any_event`` directly --
        that name only gets called BY ``FileSystemEventHandler.dispatch``'s
        own implementation. Since this class was never a
        ``FileSystemEventHandler`` subclass (see the class docstring's
        "no import-time dependency on watchdog" rationale), it never had a
        ``dispatch`` method at all -- a real ``Observer`` crashed with
        ``AttributeError: '_WatchdogEventHandler' object has no attribute
        'dispatch'`` on the very first genuine filesystem event, silently
        undetected because every existing test drove ``on_any_event``
        directly via a spy observer, never a real ``watchdog`` dispatch
        loop. Only ``on_any_event`` is implemented here (unlike the real
        ``FileSystemEventHandler.dispatch``, which also calls a per-type
        ``on_created``/``on_modified``/... method) since this class routes
        every event kind through the one method uniformly.
        """
        self.on_any_event(event)

    def on_any_event(self, event: Any) -> None:
        """Route a real ``watchdog`` event's path(s) to the debounce pipeline.

        Normalizes through ``str(PurePath(...))`` (build-plan T9.6, found
        via a real live-daemon manual check, not any automated test):
        a raw OS-reported event path mixes separators with the registered
        ``root_path`` in a way ``Path.rglob``-based discovery
        (``reconcile.discover_untracked_files``, T11.3) never produces for
        the SAME physical file -- e.g. a root registered as
        ``"C:/Users/.../vault"`` (forward slashes, exactly as a client's
        JSON ``POST /v1/sync/roots`` body supplied it) plus a Windows
        ``ReadDirectoryChangesW``-reported filename joined with a
        backslash yields ``"C:/Users/.../vault\\note.md"`` -- a different
        string than discovery's all-native-separator
        ``"C:\\Users\\...\\vault\\note.md"`` for the identical file.
        ``sync_files.path`` is keyed on this literal string, so the
        mismatch silently double-tracked (and double-reconciled) the same
        file under two rows the first time this was live-tested. ``str(
        PurePath(p))`` renders both forms identically (native separators,
        matching what ``discover_untracked_files`` already produces),
        closing the mismatch at the one place both path sources converge.
        """
        if getattr(event, "is_directory", False):
            return
        # debug-plan D10: a plain file *read* (no content change) still
        # raises a watchdog event on this platform's inotify backend --
        # ``event_type`` "opened"/"closed"/"closed_no_write", never
        # "created"/"modified"/"moved"/"deleted". Forwarding those to
        # ``notify_event`` was not just pointless extra debounce/reconcile
        # work (the AV-noise class T9.1 already tolerates) -- it was a
        # genuine self-sustaining feedback loop: ``notify_event``'s own
        # echo-suppression reads the file via ``content_hash_fn`` (a plain
        # ``Path.read_text``) to compute its hash, that read raises its own
        # open+close-no-write event under the SAME recursively-scheduled
        # observer, which re-enters ``on_any_event`` -> ``notify_event`` ->
        # another read -> another event, forever. Confirmed live: a real
        # edit under this loop never reconciled at all, because every fresh
        # "opened" event kept re-arming the debounce window before it could
        # elapse (`tests/integration/test_watcher_wiring.py::
        # test_live_edit_is_reconciled_with_no_manual_rescan`, previously
        # timing out after 5s with zero files reconciled). Only these three
        # non-content event types are excluded here -- every event type this
        # module already handles (created/modified/moved/deleted) is
        # unaffected, and a genuine external editor write still raises a
        # "modified"/"created" event on top of the harmless open/close pair
        # it also raises, so this never suppresses a real edit.
        if getattr(event, "event_type", None) in _NON_CONTENT_EVENT_TYPES:
            return
        src_path = str(PurePath(str(event.src_path)))
        if (
            not _RECONCILE_TEMP_FILE_RE.match(PurePath(src_path).name)
            and _is_managed_candidate(src_path)
        ):
            self._watcher.notify_event(src_path)
        raw_dest = str(getattr(event, "dest_path", "") or "")
        dest_path = str(PurePath(raw_dest)) if raw_dest else ""
        if (
            dest_path
            and not _RECONCILE_TEMP_FILE_RE.match(PurePath(dest_path).name)
            and _is_managed_candidate(dest_path)
        ):
            self._watcher.notify_event(dest_path)


def _default_observer_factory() -> _Scheduler:
    from watchdog.observers import Observer

    return Observer()


@dataclass
class Watcher:
    """Loads durable sync roots, watches them, and debounces raw FS events.

    Constructor / public API (T5.4 wiring contract)
    ---------------------------------------------------
    ``Watcher(conn, on_cycle, *, debounce_seconds=0.5, now=None,
    origin_tracker=None, content_hash_fn=None,
    observer_factory=<real watchdog Observer>, logger=None)``

    - ``conn``: an open ``sqlite3.Connection`` (read-only use here — only
      ``kernel.store.list_sync_roots`` is called; no writes, per rule
      0.4).
    - ``on_cycle: Callable[[str], None]`` — **the T5.4 reconcile-routing
      seam.** Called with exactly one file path once that path's 500 ms
      debounce window has elapsed with no further activity. T5.4 passes
      its real ``reconcile.on_change`` (or an equivalent adapter);
      nothing in this module imports ``sync.reconcile``.
    - ``debounce_seconds`` / ``now``: forwarded to the internal
      :class:`Debouncer` — see its docstring for the injectable-clock
      testing contract. ``logger`` is also forwarded to it (build-plan
      T9.1's AV-noise re-queue warning).
    - ``origin_tracker`` (T5.2 ``OriginTracker``) + ``content_hash_fn``:
      optional echo suppression — see module docstring. Both must be
      supplied together to take effect; either omitted disables
      suppression (every raw event is debounced and forwarded).
    - ``observer_factory``: zero-arg callable returning a ``watchdog``
      ``BaseObserver``-shaped object (``schedule``/``start``/``stop``/
      ``join``); defaults to a real ``watchdog.observers.Observer``.
      Overridable so tests can inject a spy instead of a real OS watcher
      thread.
    - ``logger``: defaults to ``logging.getLogger("akasha")`` — the same
      logger ``daemon.py::configure_logging`` sets handlers on, so the
      E19 cloud-path warning lands wherever the daemon's other logs do.

    Public methods
    ----------------
    - ``load_roots() -> list[WatchedRoot]`` — (re)reads
      ``store.list_sync_roots``, runs :func:`detect_cloud_path` against
      each ``root_path`` (logging + flagging ``conservative`` on a
      match), and returns the resulting :class:`WatchedRoot` list. Safe
      to call without ever starting a real observer (pure DB read + pure
      predicate) — this is what T5.3's tests exercise for E19.
    - ``roots -> dict[str, WatchedRoot]`` (property) — the most recently
      loaded roots, keyed by sync-root id.
    - ``start() -> None`` — calls ``load_roots()`` if not already loaded,
      creates an observer via ``observer_factory``, schedules a watch
      (``recursive=True``) on every root's ``root_path``, and starts it.
    - ``stop() -> None`` — stops and joins the observer, if one is
      running.
    - ``notify_event(path, *, at=None) -> None`` — feed one raw
      filesystem-change path into the pipeline (applies echo suppression
      if wired, then forwards to the internal ``Debouncer``). Called by
      the internal ``watchdog`` handler in production; tests call it
      directly to simulate raw FS events without a real observer thread.
    - ``poll(*, at=None) -> list[str]`` — drives the debounce window
      check; returns the list of paths whose ``on_cycle`` fired this
      call. Production runs this from a background thread started
      alongside the observer; tests call it directly with explicit
      ``at=`` timestamps for deterministic E18 assertions.
    """

    conn: sqlite3.Connection
    on_cycle: Callable[[str], None]
    debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS
    now: Callable[[], float] | None = None
    origin_tracker: OriginTracker | None = None
    content_hash_fn: Callable[[str], str] | None = None
    observer_factory: Callable[[], _Scheduler] = field(default=_default_observer_factory)
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("akasha"))
    # build-plan T9.6: this class's own docstring (see Debouncer's, "Watcher
    # drives a real poll loop from a background thread in production (see
    # Watcher.start)") always claimed start() owns this, but until T9.6 the
    # code never actually did -- poll() existed and was fully tested, but
    # nothing production called it on a timer, so no debounced event ever
    # fired in a real running daemon. Fixed by actually spawning the thread
    # this docstring already promised. Default chosen so a real event fires
    # within roughly one debounce window of going quiet (5x/window), not
    # exposed as a Watcher(...) kwarg beyond this since no caller has needed
    # to tune it yet -- lower this if a future test needs tighter latency.
    poll_interval_seconds: float = 0.1

    def __post_init__(self) -> None:
        self._debouncer = Debouncer(
            self.on_cycle,
            debounce_seconds=self.debounce_seconds,
            now=self.now,
            logger=self.logger,
        )
        self._roots: dict[str, WatchedRoot] = {}
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

    def _watch_new_roots(self) -> None:
        """Pick up any sync root registered AFTER :meth:`start` already ran
        (build-plan T9.6): :meth:`load_roots` used to run exactly once,
        inside ``start()`` -- a root registered via ``POST /v1/sync/roots``
        after the daemon is already serving (the realistic common case:
        registering a vault is normally the very next thing a human does
        once the daemon is up, not something that happens before it starts)
        would otherwise never be watched for the rest of the process's
        life, no matter how long it kept running. Called from the same
        poll loop that already drives debounce -- ``list_sync_roots`` is a
        small, cheap query (a handful of rows, no vault content), so a
        separate timer/cadence is not worth the added complexity. A no-op
        (zero new roots) is the overwhelmingly common case per tick.
        """
        if self._observer is None or self._handler is None:
            return
        for row in store.list_sync_roots(self.conn):
            if row["id"] in self._roots:
                continue
            watched = self._build_watched_root(row)
            self._roots[watched.id] = watched
            self._observer.schedule(self._handler, watched.root_path, recursive=True)

    def start(self) -> None:
        """Start watching every loaded (or freshly-loaded) sync root's ``root_path``.

        Also starts the background poll thread that actually fires
        ``on_cycle`` once a path's debounce window elapses (build-plan
        T9.6) -- without it, raw events would accumulate in the debouncer
        forever and nothing would ever reconcile. No-op if already started
        (idempotent, matching ``GcScheduler.start``'s convention).
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
        """Stop the poll thread first (no more new cycles fire), then the observer.

        Any path still inside an unexpired debounce window at shutdown
        simply does not fire this run -- the daemon's own startup
        ``reconcile_all`` (T5.6) picks it up on next launch regardless,
        same as any other missed-while-down edit.
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
                self._watch_new_roots()
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

    def notify_event(self, path: str, *, at: float | None = None) -> None:
        """Feed one raw filesystem-change ``path`` into the debounce pipeline.

        Applies echo suppression first (step: drop events matching a
        recent daemon write) when both ``origin_tracker`` and
        ``content_hash_fn`` are configured; otherwise every event is
        forwarded straight to the debouncer.

        This runs on ``watchdog``'s OWN internal dispatch thread (build-plan
        T9.6), not this class's poll thread -- there is no backstop above
        it the way ``_poll_loop`` backstops ``poll()``, so an uncaught
        exception here would kill the real ``Observer``'s dispatch loop
        outright (silently, from the daemon's perspective: the watcher
        object still exists, it just never reacts to another filesystem
        event again). ``content_hash_fn`` reads the file to hash it, and a
        real filesystem races this constantly even outside the known
        write-back-temp-file case this module already filters (an external
        editor's own atomic-save temp file, a file deleted between the
        event firing and this call, ...): if the read fails, we cannot
        prove this was an echo, so the safe default is to NOT suppress it
        -- forward to the debouncer like any other real event (worst case:
        one extra idempotent, zero-diff reconcile cycle later; on_change's
        own retry_with_backoff/T9.1 tolerance handles a still-transient
        condition by the time it actually fires).
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
