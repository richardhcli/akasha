"""§7 metrics: counters + RSS/CPU sampling (build-plan task T9.2).

Implements every §7 counter (``facet_coverage``, ``review_inflow_7d``,
``review_resolved_7d``, ``inflow_variance_30d``, ``violation_rate``,
``auto_repairs{class}``, ``crossing_rate``, ``rss_bytes``, ``idle_cpu_pct``,
``sync_cycle_ms{p50,p95}``) via :func:`compute_metrics`, the pure(ish)
snapshot function ``api/routes/metrics.py`` (spec §4.11 ``GET /metrics``)
serializes to JSON. Every DB read goes through ``kernel/store.py`` (rule
0.4) via the read-only aggregation helpers added there for this task (see
that module's "T9.2 read-only metrics aggregation helpers" section) --
this module never issues SQL of its own.

# SPEC-QUESTION (T9.2): ``violation_rate`` (violations / sync cycles),
# ``auto_repairs{class}``, and ``sync_cycle_ms{p50,p95}`` each need a live
# producer that observes sync cycles as they happen -- ``sync/reconcile.py``'s
# ``Reconciler.on_change`` is the only call site that knows a cycle ran, how
# long it took, or which certain-repair codes it silently applied (spec
# §4.7's "Certain auto-repairs (silent, logged, undoable)"). No existing DB
# table records per-cycle events: §4.4's schema is frozen, and
# ``review_queue`` only ever records violations that need human review --
# never a *quiet* cycle, and never a certain-repair (which by definition
# never reaches the queue). ``reconcile.py``/``watcher.py`` are outside this
# task's Files list (`src/akasha/metrics.py`,
# `src/akasha/api/routes/health.py` (or metrics route),
# `tests/unit/test_metrics.py`) and are concurrently owned by other
# build-plan work in this run, so this module exposes
# ``record_sync_cycle_ms``/``record_auto_repair`` as the intended future
# call site (a minimal, additive follow-up -- no further schema/API
# decisions needed) and computes the three affected metrics from whatever
# has been recorded in-process so far: all-zero/empty until that wiring
# lands. That is still an honest, correctly-shaped value -- this task's
# literal DoD is "every §7 metric appears in /v1/metrics", which holds
# either way. See docs/spec-questions.md (T9.2 entry).
"""

from __future__ import annotations

import math
import os
import sqlite3
import sys
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any

from akasha.kernel import store

_REVIEW_INFLOW_WINDOW_DAYS = 7
_REVIEW_RESOLVED_WINDOW_DAYS = 7
_INFLOW_VARIANCE_WINDOW_DAYS = 30

# Ring-buffer bound for in-process sync-cycle duration samples: a
# long-running daemon (M9 soak target: 24h+) must never grow this
# unbounded. 2000 samples comfortably covers a busy day of edits while
# staying tiny in memory (spec's own RSS budget is < 150 MB).
_MAX_CYCLE_SAMPLES = 2000


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    """Same fixed-width, lexically-sortable ISO-8601 format as ``store._now()``."""
    return dt.isoformat(timespec="microseconds")


class _CycleRecorder:
    """Process-local, in-memory recorder for sync-cycle timing + auto-repair counts.

    Deliberately NOT backed by SQLite (rule 0.4 governs persistent
    *application* state; these are process-lifetime operational samples,
    the same category as ``rss_bytes``/``idle_cpu_pct`` below, not durable
    truth) and deliberately NOT wired to a live producer yet -- see the
    module-level SPEC-QUESTION. Thread-safe (a lock guards both
    collections): a future wiring would call ``record_cycle``/
    ``record_repair`` from the sync watcher's thread while the API server
    reads a snapshot from a request-handling thread.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._durations_ms: deque[float] = deque(maxlen=_MAX_CYCLE_SAMPLES)
        self._auto_repairs: dict[str, int] = {}

    def record_cycle(self, duration_ms: float) -> None:
        with self._lock:
            self._durations_ms.append(float(duration_ms))

    def record_repair(self, code: str) -> None:
        with self._lock:
            self._auto_repairs[code] = self._auto_repairs.get(code, 0) + 1

    def snapshot(self) -> tuple[list[float], dict[str, int]]:
        with self._lock:
            return list(self._durations_ms), dict(self._auto_repairs)

    def reset(self) -> None:
        with self._lock:
            self._durations_ms.clear()
            self._auto_repairs.clear()


_recorder = _CycleRecorder()


def record_sync_cycle_ms(duration_ms: float) -> None:
    """Record one completed sync cycle's wall-clock duration, in milliseconds.

    Intended call site: ``sync/reconcile.py``'s ``Reconciler.on_change``,
    once a future task wires it in (module SPEC-QUESTION above --
    ``reconcile.py`` is outside T9.2's Files list). Feeds
    ``sync_cycle_ms{p50,p95}`` and the denominator of ``violation_rate``.
    """
    _recorder.record_cycle(duration_ms)


def record_auto_repair(code: str) -> None:
    """Record one certain-repair application, keyed by its linter code.

    Intended call site: ``sync/reconcile.py``'s certain-repair application
    step (spec §4.7's ``E_LOST_ANCHOR``/``E_DUP_ID`` silent-repair
    branches), once wired in (module SPEC-QUESTION above). Feeds
    ``auto_repairs{class}``.
    """
    _recorder.record_repair(code)


def reset_recorder() -> None:
    """Test-only: clear all recorded sync-cycle/auto-repair state."""
    _recorder.reset()


def _percentile(samples: list[float], pct: float) -> float:
    """Linear-interpolation percentile (the common "sorted + interpolate" definition).

    Returns 0.0 for an empty sample set -- the same "no data yet"
    convention every other zero-inflow counter in this module uses (see
    the module SPEC-QUESTION on ``sync_cycle_ms`` having no producer wired
    yet), not a claim that cycles complete instantly.
    """
    if not samples:
        return 0.0
    ordered = sorted(samples)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (pct / 100.0)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[int(rank)]
    lower_val = ordered[int(lower)] * (upper - rank)
    upper_val = ordered[int(upper)] * (rank - lower)
    return lower_val + upper_val


def _population_variance(values: list[int]) -> float:
    """Population variance (divide by N, not N-1).

    # SPEC-QUESTION (T9.2): spec §7 names ``inflow_variance_30d`` but does
    # not say population vs. sample variance. Narrowest reading: the
    # 30-day window is treated as a complete, bounded population (every
    # calendar day in the window, zero-filled -- see ``_daily_counts``),
    # not a sample drawn from a larger population, so population variance
    # (N divisor) is used. See docs/spec-questions.md.
    """
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    return sum((v - mean) ** 2 for v in values) / len(values)


def _daily_counts(
    timestamps: list[str], window_start: datetime, window_end: datetime
) -> list[int]:
    """Zero-filled per-(UTC)-calendar-day counts of ``timestamps`` across the window.

    Every calendar day between ``window_start`` and ``window_end``
    (inclusive) gets an entry, including days with zero events --
    counting only *observed* days would silently drop quiet days from
    the variance calculation instead of counting them as 0, understating
    how bursty the inflow really is.
    """
    buckets: dict[str, int] = {}
    for ts in timestamps:
        day = ts[:10]  # ISO-8601 date prefix ("YYYY-MM-DD"), a plain lexical slice
        buckets[day] = buckets.get(day, 0) + 1
    counts: list[int] = []
    day = window_start.date()
    end_day = window_end.date()
    while day <= end_day:
        counts.append(buckets.get(day.isoformat(), 0))
        day += timedelta(days=1)
    return counts


def _sample_rss_bytes() -> int:
    """Current resident-set size of this process, in bytes.

    Tiered stdlib-only fallback (no new dependency -- pyproject.toml has
    no existing RSS/CPU library, and the task's own narrowest-reading
    preference is stdlib when "reasonably available" over adding a new
    dependency like ``psutil``):

    1. Linux: parse ``/proc/self/status``'s ``VmRSS`` line (kB -> bytes)
       -- the CURRENT (not peak) RSS, matching this metric's own name.
    2. Windows (no ``resource`` module at all): ``ctypes`` +
       ``psapi.GetProcessMemoryInfo``'s ``WorkingSetSize`` (the Windows
       analogue of current RSS).
    3. Other POSIX (e.g. macOS/BSD, no ``/proc``): ``resource.getrusage``'s
       ``ru_maxrss`` -- PEAK, not current, RSS (the closest stdlib proxy
       available without ``/proc``); already bytes on macOS/BSD.
    4. Any failure: 0 -- this endpoint must never fail a request just
       because sampling didn't work on some platform; the spec's DoD only
       requires RSS to be "sampled", not perfect on every OS.
    """
    try:
        if sys.platform.startswith("linux"):
            with open("/proc/self/status", encoding="ascii") as fh:
                for line in fh:
                    if line.startswith("VmRSS:"):
                        kb = int(line.split()[1])
                        return kb * 1024
            return 0
        if sys.platform == "win32":
            return _sample_rss_bytes_windows()
        import resource

        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except (OSError, ValueError):
        return 0


def _sample_rss_bytes_windows() -> int:  # pragma: no cover - Windows-only branch
    import ctypes
    from ctypes import wintypes

    class _ProcessMemoryCounters(ctypes.Structure):
        _fields_ = (
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        )

    counters = _ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(_ProcessMemoryCounters)
    handle = ctypes.windll.kernel32.GetCurrentProcess()  # type: ignore[attr-defined]
    ok = ctypes.windll.psapi.GetProcessMemoryInfo(  # type: ignore[attr-defined]
        handle, ctypes.byref(counters), counters.cb
    )
    return int(counters.WorkingSetSize) if ok else 0


_cpu_sample_lock = threading.Lock()
_last_cpu_sample: tuple[float, float] | None = None  # (wall_time, process_cpu_seconds)


def _process_cpu_seconds() -> float:
    """Total user+system CPU seconds consumed by this process so far (stdlib, cross-platform)."""
    times = os.times()
    return times.user + times.system


def _sample_idle_cpu_pct() -> float:
    """This process's CPU utilization (%) since the previous sample.

    ``idle_cpu_pct`` (spec §7; M9 DoD: "idle CPU ~= 0%") is the daemon's
    OWN CPU usage while otherwise idle, not system-wide CPU -- a single
    instantaneous read cannot express a rate, so this keeps
    ``(wall_time, process_cpu_seconds)`` from the previous call in
    module-level state and reports the delta ratio. The FIRST call in a
    process's lifetime (no prior sample) has no delta to report and
    returns 0.0 (documented "no data yet", not a real 0%-idle claim).
    Clamped to ``[0, 100]`` since measurement noise across a very short
    interval could otherwise nudge the ratio a hair outside that range.
    """
    global _last_cpu_sample
    now_wall = time.monotonic()
    now_cpu = _process_cpu_seconds()
    with _cpu_sample_lock:
        previous = _last_cpu_sample
        _last_cpu_sample = (now_wall, now_cpu)
    if previous is None:
        return 0.0
    prev_wall, prev_cpu = previous
    wall_delta = now_wall - prev_wall
    if wall_delta <= 0:
        return 0.0
    cpu_delta = max(now_cpu - prev_cpu, 0.0)
    pct = (cpu_delta / wall_delta) * 100.0
    return max(0.0, min(pct, 100.0))


def reset_cpu_sampler() -> None:
    """Test-only: clear the previous-sample state ``_sample_idle_cpu_pct`` keeps."""
    global _last_cpu_sample
    with _cpu_sample_lock:
        _last_cpu_sample = None


def _facet_coverage(conn: sqlite3.Connection) -> float:
    counts = store.facet_coverage_counts(conn)
    if counts["total"] == 0:
        return 0.0
    return counts["covered"] / counts["total"]


def _review_inflow_7d(conn: sqlite3.Connection) -> int:
    since = _iso(_now() - timedelta(days=_REVIEW_INFLOW_WINDOW_DAYS))
    return store.count_reviews_created_since(conn, since)


def _review_resolved_7d(conn: sqlite3.Connection) -> int:
    since = _iso(_now() - timedelta(days=_REVIEW_RESOLVED_WINDOW_DAYS))
    return store.count_reviews_resolved_since(conn, since)


def _inflow_variance_30d(conn: sqlite3.Connection) -> float:
    now = _now()
    window_start = now - timedelta(days=_INFLOW_VARIANCE_WINDOW_DAYS)
    timestamps = store.list_review_created_at_since(conn, _iso(window_start))
    counts = _daily_counts(timestamps, window_start, now)
    return _population_variance(counts)


def _crossing_rate(conn: sqlite3.Connection) -> float:
    """Nodes created ÷ day, since the earliest node this daemon ever minted (spec §7).

    # design note (T9.2): the spec gives the formula but not the window;
    # narrowest reading is "since inception" (the daemon's own minting
    # history), not a fixed trailing window like the review metrics get
    # (those are explicitly suffixed ``_7d``/``_30d`` in the spec text;
    # ``crossing_rate`` carries no such suffix).
    """
    total = store.count_nodes_created_since(conn)
    if total == 0:
        return 0.0
    earliest = store.earliest_node_created_at(conn)
    assert earliest is not None  # total > 0 implies at least one created_at row
    elapsed_days = (_now() - datetime.fromisoformat(earliest)).total_seconds() / 86400.0
    # Never divide by less than one full day: the spec pins no minimum
    # window, and a node minted seconds ago would otherwise spike the
    # rate to an implausible number on a freshly-started daemon.
    elapsed_days = max(elapsed_days, 1.0)
    return total / elapsed_days


def compute_metrics(conn: sqlite3.Connection) -> dict[str, Any]:
    """Compute every §7 counter + the RSS/CPU samples, ready for JSON (spec §4.11).

    Called once per ``GET /v1/metrics`` request (see
    ``api/routes/metrics.py``) -- one point-in-time snapshot per
    invocation, the same pattern ``GET /sync/status`` uses. Every
    top-level key below is exactly one §7 counter name.
    """
    durations, auto_repairs = _recorder.snapshot()
    sync_cycles = len(durations)
    violations = store.count_violations_total(conn)
    return {
        "facet_coverage": _facet_coverage(conn),
        "review_inflow_7d": _review_inflow_7d(conn),
        "review_resolved_7d": _review_resolved_7d(conn),
        "inflow_variance_30d": _inflow_variance_30d(conn),
        "violation_rate": (violations / sync_cycles) if sync_cycles else 0.0,
        "auto_repairs": auto_repairs,
        "crossing_rate": _crossing_rate(conn),
        "rss_bytes": _sample_rss_bytes(),
        "idle_cpu_pct": _sample_idle_cpu_pct(),
        "sync_cycle_ms": {"p50": _percentile(durations, 50), "p95": _percentile(durations, 95)},
    }
