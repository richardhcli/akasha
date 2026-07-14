"""Origin / echo-suppression: daemon-write tracking for the §4.8 watcher.

Spec §4.8: "Echo suppression: writes performed by the daemon record
``(path, hash)`` in ``origin.py``; a watcher event whose content hash
matches a recorded write is dropped." This closes the write-then-watch
loop: T5.4's reconcile pipeline performs a canonical write-back
(``write_if_diff``) to a managed file, which the OS filesystem watcher
(T5.3) will observe as its own filesystem-change event a few milliseconds
later; without this module that event would re-trigger a reconcile of a
change the daemon itself already applied.

This module is deliberately **process-local, in-memory, non-persistent**
— NOT a ``kernel/store.py`` concern (build-plan rule 0.4 governs
*truth-bearing* SQLite writes; echo-suppression bookkeeping is transient
operational state, exactly like ``api/auth.py``'s in-process rate-limit
call log: it resets on daemon restart, which is fine, since a restart
also means the watcher's observer thread restarts and has no stale
events to reconcile against). No SQLite, no ``pickle``/``eval``/``exec``.

``hash`` here is an opaque string — this module never computes a content
hash itself; callers (T5.4's write-back, T5.3's watcher reading the new
file bytes) pass whatever canonical content hash they already computed
(spec §4.3 canonicalization + §4.4 ``objects.hash`` scheme).

Bounded memory
---------------
Two independent bounds keep ``_pending`` from growing without limit if a
recorded write never echoes back (e.g. the watcher never fires — file
deleted externally before the FS event lands, or the platform watcher
misses an event):

- **Count bound** (``max_pending``, default 256): the oldest record is
  evicted once the pending queue would exceed this size. 256 comfortably
  covers a large multi-file reconcile burst (§6.2's scripted battery
  scenarios) without keeping unbounded history.
- **Age bound** (``ttl_seconds``, default 30.0): a record older than this
  is evicted (lazily, on the next ``record_write``/``is_echo`` call)
  regardless of count pressure. 30s is generously longer than the §4.8
  500ms debounce window plus OS-level FS-event latency (cloud-sync
  providers per M5's cloud-path detection can add real delay), so a
  genuine echo is never missed, while a write that never echoes doesn't
  linger indefinitely.

The clock is injectable (``now`` parameter, defaulting to
``time.monotonic()``) so tests are deterministic — mirrors
``api/auth.py``'s ``check_rate_limit(..., now=...)`` pattern.

Thread-safety: the watcher (T5.3) may call ``is_echo`` from a
``watchdog`` observer thread while the daemon's reconcile pipeline
(T5.4) calls ``record_write`` from its own thread/task. A single
``threading.Lock`` around the shared queue makes both operations safe to
interleave.
"""

from __future__ import annotations

import threading
import time
from collections import deque

# Bound-by-count: oldest pending record is evicted once the queue would
# exceed this many entries. See module docstring "Bounded memory".
DEFAULT_MAX_PENDING = 256

# Bound-by-age, in seconds (monotonic clock): a pending record older than
# this is evicted lazily. See module docstring "Bounded memory".
DEFAULT_TTL_SECONDS = 30.0


class OriginTracker:
    """Tracks recent daemon writes so the watcher can drop echoed FS events.

    Public API:

    - ``record_write(path: str, hash: str, *, now: float | None = None) -> None``
      Record that the daemon just wrote ``path`` with canonical content
      ``hash``.
    - ``is_echo(path: str, hash: str, *, now: float | None = None) -> bool``
      Return ``True`` iff ``(path, hash)`` matches a still-pending
      recorded write, **consuming** that record so an identical
      subsequent call (e.g. a genuine external write that happens to
      reproduce the same bytes) is NOT suppressed a second time. Matching
      requires both ``path`` and ``hash`` to be equal; a matching hash on
      a different path, or a different hash on the same path, is never an
      echo.

    One instance is meant to be held by the watcher/reconcile wiring
    (T5.3/T5.4) and shared between the thread that performs writes and
    the thread that observes filesystem events.
    """

    def __init__(
        self,
        *,
        max_pending: int = DEFAULT_MAX_PENDING,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
    ) -> None:
        self._max_pending = max_pending
        self._ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        # Insertion-ordered (path, hash, recorded_at) records; the left
        # end is always the oldest, so both eviction policies pop-left.
        self._pending: deque[tuple[str, str, float]] = deque()

    def record_write(self, path: str, hash: str, *, now: float | None = None) -> None:
        """Record a daemon write of ``path`` with canonical content ``hash``."""
        current = self._now(now)
        with self._lock:
            self._evict_expired_locked(current)
            self._pending.append((path, hash, current))
            while len(self._pending) > self._max_pending:
                self._pending.popleft()

    def is_echo(self, path: str, hash: str, *, now: float | None = None) -> bool:
        """Return True and consume the matching record iff this is a known echo."""
        current = self._now(now)
        with self._lock:
            self._evict_expired_locked(current)
            for index, (recorded_path, recorded_hash, _) in enumerate(self._pending):
                if recorded_path == path and recorded_hash == hash:
                    del self._pending[index]
                    return True
            return False

    def _evict_expired_locked(self, now: float) -> None:
        """Drop records older than ``ttl_seconds``. Caller must hold ``_lock``."""
        cutoff = now - self._ttl_seconds
        while self._pending and self._pending[0][2] < cutoff:
            self._pending.popleft()

    @staticmethod
    def _now(now: float | None) -> float:
        return time.monotonic() if now is None else now
