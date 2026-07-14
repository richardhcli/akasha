"""Unit tests for the filesystem watcher (task T5.3, spec §4.8; §6.2 E18/E19).

Covers the DoD holistically:
- E18: a burst of N raw events yields exactly ONE reconcile cycle, and the
  debounce RESETS (a later burst produces its own cycle) — not a one-shot.
- E19: a simulated OneDrive/Dropbox root sets the warning + conservative flag;
  an ordinary local root does not.
Plus: cloud-path predicate edge cases, sync-root loading from the store,
optional echo-suppression wiring, and observer start/stop via a spy factory.

All timing is deterministic via the injectable ``at=`` clock — no real sleeps.
"""

from __future__ import annotations

import logging
import sqlite3

import pytest

from akasha.kernel import store
from akasha.sync import watcher as watcher_mod
from akasha.sync.origin import OriginTracker
from akasha.sync.watcher import Debouncer, WatchedRoot, Watcher, detect_cloud_path


def _conn() -> sqlite3.Connection:
    conn = store.connect(":memory:", check_same_thread=False)
    store.run_migrations(conn)
    return conn


# --- detect_cloud_path (pure predicate, E19 core) --------------------------


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/home/u/OneDrive/vault", "OneDrive"),
        ("/home/u/OneDrive - Contoso/vault", "OneDrive"),  # OneDrive for Business
        ("C:/Users/u/Dropbox/vault", "Dropbox"),
        ("/home/u/Dropbox (Personal)/notes", "Dropbox"),
        ("/home/u/onedrive/vault", "OneDrive"),  # case-insensitive
        ("/home/u/Documents/vault", None),  # ordinary local path
        ("/home/u/notes", None),
    ],
)
def test_detect_cloud_path(path, expected):
    assert detect_cloud_path(path) == expected


# --- Debouncer (E18) -------------------------------------------------------


def test_debouncer_burst_coalesces_to_one_cycle():
    fired: list[str] = []
    d = Debouncer(fired.append, debounce_seconds=0.5)
    # A burst of 5 events for the same path within the window.
    for i in range(5):
        d.notify("/vault/a.md", at=100.0 + i * 0.05)  # 100.00 .. 100.20
    # Still inside the window from the last event (100.20 + 0.5 = 100.70).
    assert d.poll(at=100.60) == []
    assert fired == []
    # Window elapsed with no further events -> exactly one cycle.
    assert d.poll(at=100.71) == ["/vault/a.md"]
    assert fired == ["/vault/a.md"]


def test_debouncer_resets_after_fire_not_one_shot():
    fired: list[str] = []
    d = Debouncer(fired.append, debounce_seconds=0.5)
    d.notify("/vault/a.md", at=100.0)
    assert d.poll(at=100.6) == ["/vault/a.md"]  # first cycle
    # A brand-new burst after the fire produces its own, later cycle.
    d.notify("/vault/a.md", at=200.0)
    assert d.poll(at=200.3) == []  # still within the new window
    assert d.poll(at=200.6) == ["/vault/a.md"]  # second cycle
    assert fired == ["/vault/a.md", "/vault/a.md"]


def test_debouncer_distinct_paths_fire_independently():
    fired: list[str] = []
    d = Debouncer(fired.append, debounce_seconds=0.5)
    d.notify("/vault/a.md", at=100.0)
    d.notify("/vault/b.md", at=100.4)
    # a's window elapsed, b's has not yet.
    assert d.poll(at=100.6) == ["/vault/a.md"]
    assert d.poll(at=100.95) == ["/vault/b.md"]
    assert fired == ["/vault/a.md", "/vault/b.md"]


def test_debouncer_nothing_ready_returns_empty():
    fired: list[str] = []
    d = Debouncer(fired.append, debounce_seconds=0.5)
    d.notify("/vault/a.md", at=100.0)
    assert d.poll(at=100.1) == []
    assert fired == []


# --- Watcher.load_roots (E19 + step 1) -------------------------------------


def test_load_roots_flags_cloud_root_and_warns(caplog):
    conn = _conn()
    store.register_sync_root(conn, "cloudy", "/home/u/OneDrive/vault")
    store.register_sync_root(conn, "local", "/home/u/Documents/vault")

    w = Watcher(conn, lambda _p: None)
    with caplog.at_level(logging.WARNING, logger="akasha"):
        roots = w.load_roots()

    by_name = {r.name: r for r in roots}
    assert by_name["cloudy"].conservative is True
    assert by_name["cloudy"].cloud_provider == "OneDrive"
    assert by_name["local"].conservative is False
    assert by_name["local"].cloud_provider is None
    # exactly one warning, and it names the cloud root (not the local one).
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "OneDrive" in warnings[0].getMessage()

    # roots property is keyed by sync-root id.
    assert set(w.roots) == {r.id for r in roots}


def test_load_roots_no_warning_for_ordinary_paths(caplog):
    conn = _conn()
    store.register_sync_root(conn, "local", "/home/u/Documents/vault")
    w = Watcher(conn, lambda _p: None)
    with caplog.at_level(logging.WARNING, logger="akasha"):
        w.load_roots()
    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []


# --- Watcher.notify_event + poll (E18 end-to-end, no observer thread) ------


def test_watcher_notify_event_burst_yields_one_cycle():
    conn = _conn()
    fired: list[str] = []
    w = Watcher(conn, fired.append, debounce_seconds=0.5)
    for i in range(4):
        w.notify_event("/vault/a.md", at=10.0 + i * 0.05)
    assert w.poll(at=10.2) == []  # still within window
    assert w.poll(at=10.9) == ["/vault/a.md"]
    assert fired == ["/vault/a.md"]


# --- Echo suppression (optional wiring) ------------------------------------


def test_watcher_suppresses_echo_of_daemon_write():
    conn = _conn()
    fired: list[str] = []
    tracker = OriginTracker()
    # content_hash_fn maps a path to its "current content hash"; here the
    # daemon wrote a.md with hash "H".
    hashes = {"/vault/a.md": "H"}
    w = Watcher(
        conn,
        fired.append,
        debounce_seconds=0.5,
        origin_tracker=tracker,
        content_hash_fn=lambda p: hashes[p],
    )
    tracker.record_write("/vault/a.md", "H")  # daemon's own write recorded

    w.notify_event("/vault/a.md", at=1.0)  # the echo of that write
    assert w.poll(at=1.6) == []  # suppressed -> no cycle
    assert fired == []

    # A subsequent genuine external edit (different content) is NOT suppressed.
    hashes["/vault/a.md"] = "H2"
    w.notify_event("/vault/a.md", at=2.0)
    assert w.poll(at=2.6) == ["/vault/a.md"]
    assert fired == ["/vault/a.md"]


def test_watcher_without_tracker_forwards_every_event():
    conn = _conn()
    fired: list[str] = []
    w = Watcher(conn, fired.append, debounce_seconds=0.5)  # no tracker
    w.notify_event("/vault/a.md", at=1.0)
    assert w.poll(at=1.6) == ["/vault/a.md"]


# --- start()/stop() via a spy observer (no real OS watcher thread) ---------


class _SpyObserver:
    def __init__(self) -> None:
        self.scheduled: list[tuple[str, bool]] = []
        self.started = False
        self.stopped = False
        self.joined = False

    def schedule(self, event_handler, path, *, recursive=False):
        self.scheduled.append((path, recursive))
        return object()

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def join(self, timeout=None):
        self.joined = True


def test_start_schedules_a_recursive_watch_per_root_and_stop_joins():
    conn = _conn()
    store.register_sync_root(conn, "one", "/home/u/vault-one")
    store.register_sync_root(conn, "two", "/home/u/vault-two")
    spy = _SpyObserver()

    w = Watcher(conn, lambda _p: None, observer_factory=lambda: spy)
    w.start()

    assert spy.started is True
    watched = {path for path, _rec in spy.scheduled}
    assert watched == {"/home/u/vault-one", "/home/u/vault-two"}
    assert all(recursive for _p, recursive in spy.scheduled)  # recursive=True

    w.stop()
    assert spy.stopped is True and spy.joined is True


def test_watchdog_event_handler_routes_src_and_dest_but_skips_dirs():
    conn = _conn()
    seen: list[str] = []
    w = Watcher(conn, lambda _p: None)
    # Patch notify_event to observe routing without a real debounce timeline.
    w.notify_event = lambda path, *, at=None: seen.append(path)  # type: ignore[method-assign]
    handler = watcher_mod._WatchdogEventHandler(w)

    class _Evt:
        def __init__(self, src, dest="", is_directory=False):
            self.src_path = src
            self.dest_path = dest
            self.is_directory = is_directory

    handler.on_any_event(_Evt("/vault/a.md"))
    handler.on_any_event(_Evt("/vault/a.md", dest="/vault/b.md"))  # a move/rename
    handler.on_any_event(_Evt("/vault/subdir", is_directory=True))  # ignored

    assert seen == ["/vault/a.md", "/vault/a.md", "/vault/b.md"]


def test_watched_root_dataclass_defaults():
    r = WatchedRoot(id="x", name="n", root_path="/p")
    assert r.conservative is False and r.cloud_provider is None
