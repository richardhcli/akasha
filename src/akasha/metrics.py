"""§7 metrics: counters and RSS/CPU sampling behind ``GET /metrics`` (spec §4.11).

:func:`compute_metrics` builds the snapshot (``facet_coverage``, ``review_inflow_7d``,
``review_resolved_7d``, ``inflow_variance_30d``, ``violation_rate``, ``auto_repairs{class}``,
``crossing_rate``, ``rss_bytes``, ``idle_cpu_pct``, ``sync_cycle_ms{p50,p95}``). Every DB read goes
through ``kernel/store.py`` helpers (rule 0.4); this module issues no SQL.

``violation_rate``, ``auto_repairs`` and ``sync_cycle_ms`` are fed live by ``Reconciler``
(``record_sync_cycle_ms`` on every cycle, ``record_auto_repair`` only when a repair is actually
applied silently, never when a conservative root routes it to review).
"""

from __future__ import annotations

import ctypes
import functools
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
    """Process-local, in-memory recorder of sync-cycle timings and auto-repair counts.

    Not in SQLite: these are process-lifetime operational samples like ``rss_bytes``, not durable
    truth (rule 0.4 covers application state). A lock guards both collections: the watcher thread
    records while a request thread reads a snapshot.
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
    """Population variance (divide by N).

    # SPEC-QUESTION (T9.2): §7 names ``inflow_variance_30d`` without population vs. sample.
    # Narrowest reading: the zero-filled 30-day window (``_daily_counts``) is a complete
    # population. See docs/spec-questions.md.
    """
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    return sum((v - mean) ** 2 for v in values) / len(values)


def _daily_counts(
    timestamps: list[str], window_start: datetime, window_end: datetime
) -> list[int]:
    """Zero-filled per-UTC-day counts of ``timestamps`` across the inclusive window. Quiet days
    count as 0; dropping them would understate how bursty the inflow is.
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
    """Current resident-set size of this process in bytes (stdlib only; no ``psutil`` dependency).

    Linux: ``VmRSS`` from ``/proc/self/status``. Windows: ``GetProcessMemoryInfo``'s
    ``WorkingSetSize`` via ``ctypes``. Other POSIX: ``ru_maxrss`` (PEAK, the closest stdlib proxy;
    already bytes on macOS/BSD). Any failure gives 0: the endpoint must never fail because sampling
    did.
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


@functools.lru_cache(maxsize=1)
def _windows_memory_api() -> tuple[Any, Any, type[ctypes.Structure]]:  # pragma: no cover
    """One-time (cached) setup of the Win32 ``GetProcessMemoryInfo`` binding.

    D2: this used to reassign the shared ``kernel32``/``psapi`` ``argtypes``/``restype`` on every
    call, so two concurrent ``GET /v1/metrics`` requests could race and raise
    ``ctypes.ArgumentError``. The ``lru_cache`` makes the setup run once (a first-call race just
    repeats the same assignments). The early guard mirrors ``daemon.py``'s: typeshed's ``windll``
    types only resolve under Windows, so without it pyright on another platform reports unknown
    members.
    """
    if sys.platform != "win32":
        raise AssertionError("_windows_memory_api called on a non-Windows platform")

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

    # ctypes defaults undeclared argtypes/restype to 32-bit C int, which
    # truncates GetCurrentProcess's 64-bit pseudo-handle (0xFFFF...FFFF) to
    # 0xFFFFFFFF -- GetProcessMemoryInfo then rejects it with
    # ERROR_INVALID_HANDLE and this whole function silently returns 0 on
    # every real Windows process. Declaring the true Win32 signatures fixes
    # the 64-bit handle marshaling (verified against a live GetLastError()
    # probe: 0 => 6 ERROR_INVALID_HANDLE before this fix, 1 => 0 after).
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    psapi = ctypes.windll.psapi  # type: ignore[attr-defined]
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetCurrentProcess.argtypes = []
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ProcessMemoryCounters),
        wintypes.DWORD,
    ]
    return kernel32, psapi, _ProcessMemoryCounters


def _sample_rss_bytes_windows() -> int:  # pragma: no cover - Windows-only branch
    if sys.platform != "win32":
        raise AssertionError("_sample_rss_bytes_windows called on a non-Windows platform")

    import ctypes

    kernel32, psapi, counters_cls = _windows_memory_api()
    counters = counters_cls()
    counters.cb = ctypes.sizeof(counters_cls)
    handle = kernel32.GetCurrentProcess()
    ok = psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
    return int(counters.WorkingSetSize) if ok else 0


_cpu_sample_lock = threading.Lock()
_last_cpu_sample: tuple[float, float] | None = None  # (wall_time, process_cpu_seconds)


def _process_cpu_seconds() -> float:
    """Total user+system CPU seconds consumed by this process so far (stdlib, cross-platform)."""
    times = os.times()
    return times.user + times.system


def _sample_idle_cpu_pct() -> float:
    """This process's CPU utilization (%) since the previous sample: ``idle_cpu_pct`` (spec §7) is
    the daemon's own usage while idle, not system-wide. A rate needs two samples, so the previous
    ``(wall_time, cpu_seconds)`` is module state; the first call returns 0.0 ("no data yet").
    Clamped to [0, 100] against short-interval noise.
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
    """Nodes created per day since the earliest node minted (spec §7).

    # design note (T9.2): the spec gives the formula but no window; narrowest reading is "since
    # inception", since only the review metrics carry a ``_7d``/``_30d`` suffix.
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
