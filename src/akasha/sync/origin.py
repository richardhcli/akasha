"""Origin / echo suppression: tracking the daemon's own writes for the §4.8 watcher.

Spec §4.8: writes the daemon performs record ``(path, hash)`` here, and a watcher event whose
content hash matches a recorded write is dropped. This closes the write-then-watch loop: the
canonical write-back (``write_if_diff``) is observed by the filesystem watcher a few milliseconds
later and would otherwise re-trigger a reconcile of a change the daemon already applied.

Process-local, in-memory, non-persistent, and not a ``kernel/store.py`` concern (rule 0.4 covers
truth-bearing writes; like ``api/auth.py``'s rate-limit log this resets on restart, which restarts
the observer too). ``hash`` is an opaque string: callers pass the canonical content hash they
already computed.

Bounded memory, in case a recorded write never echoes back (a file deleted before the event lands,
a missed platform event): ``max_pending`` (default 256) evicts the oldest record; ``ttl_seconds``
(default 30.0, well past the 500 ms debounce plus cloud-sync latency) evicts old records lazily on
the next ``record_write``/``is_echo``. The clock is injectable (``now``). One ``threading.Lock``
makes the watcher thread's ``is_echo`` and the reconcile thread's ``record_write`` safe to
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
    """Tracks recent daemon writes so the watcher can drop echoed filesystem events.

    ``record_write(path, hash, *, now=None)`` records that the daemon just wrote ``path`` with
    canonical content ``hash``. ``is_echo(path, hash, *, now=None)`` is True iff ``(path, hash)``
    matches a still-pending write, and CONSUMES it, so an identical later event (a genuine external
    write that reproduces the same bytes) is not suppressed again. Both ``path`` and ``hash`` must
    match. One instance is shared by the writing thread and the observing thread.
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
