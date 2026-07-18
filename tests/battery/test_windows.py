"""Windows battery: locking retry, AV noise, CRLF (build-plan T9.1, spec §6.2 E09).

This host is Linux (the ``Verify`` command's own doc comment in
``docs/build-plan.md`` says "Windows CI leg"); real Windows-only APIs
(``msvcrt``, an actual OS-raised ``ERROR_SHARING_VIOLATION``) are never
invoked here. Every test below instead exercises the REAL production retry
logic -- ``sync.watcher.retry_with_backoff``/``is_transient_lock_error``, the
``Debouncer`` re-queue, and ``sync.reconcile.Reconciler.write_if_diff``/
``on_change`` -- by injecting a fake exception carrying a Windows
``winerror`` code. ``OSError.winerror`` is a plain, settable instance
attribute on every platform (verified: setting it on Linux does not require
``sys.platform == "win32"``), so this is a faithful simulation of the OS-level
condition, not a mock of this task's own logic. See ``sync.watcher``'s module
docstring ("Windows locking-retry / AV-noise tolerance") for the full
reconcile.py/watcher.py design split this battery covers.

E-number coverage
------------------
E09 (CRLF arrives ⇒ canonicalized, no spurious diff) is ALREADY covered
end-to-end by the golden-fixture-driven
``tests/battery/test_edit_battery.py::test_e09_crlf_produces_zero_writes_and_zero_reviews``
(task T5.8) -- not re-derived here. ``test_crlf_arrival_produces_no_spurious_diff``
below instead confirms that this task's retry wrapping around
``Reconciler.on_change``/``write_if_diff`` does not disturb that behavior.
"""

from __future__ import annotations

import errno
import logging
import os
import sqlite3
from pathlib import Path

import pytest

from akasha.kernel import store
from akasha.kernel.canonical import canonicalize_text
from akasha.sync import base_store, reconcile
from akasha.sync.origin import OriginTracker
from akasha.sync.reconcile import Reconciler
from akasha.sync.watcher import (
    TRANSIENT_WINDOWS_LOCK_ERRORS,
    Debouncer,
    is_transient_lock_error,
    retry_with_backoff,
)


def _conn() -> sqlite3.Connection:
    conn = store.connect(":memory:", check_same_thread=False)
    store.run_migrations(conn)
    return conn


def _register_root(conn: sqlite3.Connection, root_path: Path) -> str:
    return store.register_sync_root(conn, "vault", str(root_path))["id"]


def _managed(body: str) -> str:
    return canonicalize_text(f"---\ntm: 1\n---\n{body}")


def _win_error(winerror: int, msg: str = "simulated") -> OSError:
    """A fake ``OSError`` carrying a Windows ``winerror`` (settable on any platform)."""
    exc = OSError(msg)
    exc.winerror = winerror
    return exc


# =================================================================================
# is_transient_lock_error (classifier)
# =================================================================================


@pytest.mark.parametrize("code", sorted(TRANSIENT_WINDOWS_LOCK_ERRORS))
def test_transient_windows_codes_are_classified_as_transient(code):
    assert is_transient_lock_error(_win_error(code)) is True


def test_unrelated_winerror_is_not_transient():
    assert is_transient_lock_error(_win_error(2)) is False  # ERROR_FILE_NOT_FOUND


def test_oserror_without_winerror_is_not_transient():
    # The everyday POSIX shape of "permission denied" -- no winerror at all.
    assert is_transient_lock_error(OSError(errno.EACCES, "denied")) is False


def test_non_oserror_is_never_transient():
    exc = ValueError("nope")
    exc.winerror = 32  # type: ignore[attr-defined]  # even if spoofed, wrong type
    assert is_transient_lock_error(exc) is False


# =================================================================================
# retry_with_backoff (pure retry loop, no real sleeps)
# =================================================================================


def test_retry_succeeds_after_bounded_transient_failures_with_exact_backoff():
    calls = {"n": 0}
    sleeps: list[float] = []

    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise _win_error(32, "sharing violation")  # ERROR_SHARING_VIOLATION
        return "ok"

    result = retry_with_backoff(flaky, sleep=sleeps.append)

    assert result == "ok"
    assert calls["n"] == 3
    # base_delay(0.05) * 2**0, 2**1 -- exactly two backoff sleeps before the
    # third (successful) attempt.
    assert sleeps == [0.05, 0.1]


def test_retry_gives_up_after_exhausting_attempts():
    calls = {"n": 0}

    def always_locked() -> None:
        calls["n"] += 1
        raise _win_error(33, "lock violation")  # ERROR_LOCK_VIOLATION

    with pytest.raises(OSError) as exc_info:
        retry_with_backoff(always_locked, attempts=3, sleep=lambda _s: None)

    assert calls["n"] == 3
    assert exc_info.value.winerror == 33


def test_retry_reraises_non_transient_error_immediately_with_no_sleep():
    calls = {"n": 0}
    sleeps: list[float] = []

    def not_locked() -> None:
        calls["n"] += 1
        raise OSError(errno.ENOENT, "missing")

    with pytest.raises(OSError):
        retry_with_backoff(not_locked, sleep=sleeps.append)

    assert calls["n"] == 1  # no retries at all for a non-transient error
    assert sleeps == []


def test_retry_custom_predicate_can_disable_retry():
    calls = {"n": 0}

    def flaky() -> None:
        calls["n"] += 1
        raise _win_error(32)

    with pytest.raises(OSError):
        retry_with_backoff(flaky, is_transient=lambda _exc: False, sleep=lambda _s: None)

    assert calls["n"] == 1  # predicate says "not transient" -> no retry


def test_retry_default_sleep_is_time_sleep(monkeypatch):
    """Without an injected ``sleep=``, the real backoff schedule uses ``time.sleep``."""
    recorded: list[float] = []
    monkeypatch.setattr("time.sleep", recorded.append)
    calls = {"n": 0}

    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 2:
            raise _win_error(5)  # ERROR_ACCESS_DENIED
        return "ok"

    assert retry_with_backoff(flaky) == "ok"
    assert recorded == [0.05]


# =================================================================================
# Debouncer AV-noise re-queue (build-plan Step 2: "tolerate transient AV-held
# handles" at the on_cycle-invocation layer, on top of retry_with_backoff's own
# short budget used inside reconcile.py's OS-level calls)
# =================================================================================


def test_debouncer_requeues_path_on_transient_error_without_raising(caplog):
    calls = {"n": 0}

    def flaky(_path: str) -> None:
        calls["n"] += 1
        if calls["n"] < 3:
            raise _win_error(5, "AV-held handle")  # ERROR_ACCESS_DENIED

    d = Debouncer(flaky, debounce_seconds=0.5)
    d.notify("/vault/a.md", at=100.0)

    with caplog.at_level(logging.WARNING, logger="akasha"):
        # First two polls hit the transient error: poll() does not raise,
        # and the path does not appear as "fired".
        assert d.poll(at=100.6) == []
        assert d.poll(at=101.2) == []
        # Third attempt succeeds.
        assert d.poll(at=101.8) == ["/vault/a.md"]

    assert calls["n"] == 3
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2
    assert all("transient" in r.getMessage().lower() for r in warnings)
    assert all("/vault/a.md" in r.getMessage() for r in warnings)


def test_debouncer_requeue_waits_a_fresh_debounce_window():
    """A re-queued path must wait a FULL fresh window, not fire on the next poll."""
    calls = {"n": 0}

    def flaky(_path: str) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise _win_error(32)

    d = Debouncer(flaky, debounce_seconds=0.5)
    d.notify("/vault/a.md", at=100.0)

    assert d.poll(at=100.6) == []  # transient failure -> re-queued at t=100.6
    assert d.poll(at=100.8) == []  # only 0.2s since re-queue -- not ready yet
    assert d.poll(at=101.15) == ["/vault/a.md"]  # 0.55s since re-queue -- fires

    assert calls["n"] == 2


def test_debouncer_never_swallows_a_non_transient_error():
    def always_fails(_path: str) -> None:
        raise OSError(errno.ENOENT, "genuinely missing, not a lock")

    d = Debouncer(always_fails, debounce_seconds=0.5)
    d.notify("/vault/a.md", at=1.0)

    with pytest.raises(OSError):
        d.poll(at=1.6)


def test_debouncer_distinct_paths_dont_interfere_with_requeue():
    fired: list[str] = []
    calls_a = {"n": 0}

    def on_cycle(path: str) -> None:
        if path == "/vault/a.md":
            calls_a["n"] += 1
            if calls_a["n"] < 2:
                raise _win_error(32)
        fired.append(path)

    d = Debouncer(on_cycle, debounce_seconds=0.5)
    d.notify("/vault/a.md", at=100.0)
    d.notify("/vault/b.md", at=100.0)

    # a fails transiently and is re-queued; b fires normally in the same poll.
    assert d.poll(at=100.6) == ["/vault/b.md"]
    assert fired == ["/vault/b.md"]
    # a's re-queued window elapses on a later poll.
    assert d.poll(at=101.2) == ["/vault/a.md"]
    assert fired == ["/vault/b.md", "/vault/a.md"]


# =================================================================================
# Reconciler.write_if_diff / on_change: locked-file writes retry and succeed (DoD)
# =================================================================================


def test_write_if_diff_retries_locked_replace_and_succeeds(tmp_path, monkeypatch):
    conn = _conn()
    _register_root(conn, tmp_path)
    path = tmp_path / "note.md"
    path.write_text("old\n", encoding="utf-8")

    calls = {"n": 0}
    real_replace = os.replace

    def flaky_replace(src: object, dst: object) -> None:
        calls["n"] += 1
        if calls["n"] < 3:
            raise _win_error(32, "sharing violation")
        real_replace(src, dst)

    monkeypatch.setattr(reconcile.os, "replace", flaky_replace)
    monkeypatch.setattr("time.sleep", lambda _s: None)

    reconciler = Reconciler(conn, OriginTracker())
    wrote = reconciler.write_if_diff(str(path), "new\n")

    assert wrote is True
    assert path.read_text(encoding="utf-8") == "new\n"
    assert calls["n"] == 3  # two locked attempts, then success


def test_write_if_diff_retries_locked_pre_write_read_and_detects_no_diff(tmp_path, monkeypatch):
    conn = _conn()
    _register_root(conn, tmp_path)
    path = tmp_path / "note.md"
    path.write_text("same\n", encoding="utf-8")

    calls = {"n": 0}
    real_read_text = Path.read_text

    def flaky_read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self == path:
            calls["n"] += 1
            if calls["n"] < 2:
                raise _win_error(5, "AV-held handle")
        return real_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", flaky_read_text)
    monkeypatch.setattr("time.sleep", lambda _s: None)

    reconciler = Reconciler(conn, OriginTracker())
    wrote = reconciler.write_if_diff(str(path), "same\n")

    assert wrote is False  # content identical once the retried read succeeds
    assert calls["n"] == 2


def test_write_if_diff_gives_up_after_persistent_lock_raises(tmp_path, monkeypatch):
    """The DoD is "retry and succeed", not "retry forever" -- a lock that never
    clears within the retry budget must still surface as a real error, never be
    silently swallowed as a successful write."""
    conn = _conn()
    _register_root(conn, tmp_path)
    path = tmp_path / "note.md"
    path.write_text("old\n", encoding="utf-8")

    def always_locked(_src: object, _dst: object) -> None:
        raise _win_error(32, "sharing violation")

    monkeypatch.setattr(reconcile.os, "replace", always_locked)
    monkeypatch.setattr("time.sleep", lambda _s: None)

    reconciler = Reconciler(conn, OriginTracker())
    with pytest.raises(OSError):
        reconciler.write_if_diff(str(path), "new\n")

    # No partial/garbled write: the original file is untouched.
    assert path.read_text(encoding="utf-8") == "old\n"


def test_on_change_retries_locked_vault_read_and_still_converges_quietly(tmp_path, monkeypatch):
    conn = _conn()
    root_id = _register_root(conn, tmp_path)
    text = _managed("plain prose, no anchors\n")
    path = tmp_path / "note.md"
    path.write_text(text, encoding="utf-8")
    base_store.put(conn, root_id, str(path), text)

    calls = {"n": 0}
    real_read_text = Path.read_text

    def flaky_read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self == path:
            calls["n"] += 1
            if calls["n"] < 2:
                raise _win_error(32, "sharing violation")
        return real_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", flaky_read_text)
    monkeypatch.setattr("time.sleep", lambda _s: None)

    reconciler = Reconciler(conn, OriginTracker())
    mtime_before = path.stat().st_mtime_ns

    reconciler.on_change(str(path))  # must not raise despite the transient read

    assert calls["n"] == 2
    assert path.read_text(encoding="utf-8") == text
    assert path.stat().st_mtime_ns == mtime_before  # quiet shortcut -- zero writes
    assert store.find_open_reviews(conn) == []


# =================================================================================
# CRLF end-to-end confirmation (E09), through the T9.1 retry-wrapped call sites
# =================================================================================


def test_crlf_arrival_produces_no_spurious_diff(tmp_path):
    """E09 (spec §6.2): a CRLF-line-ending vault file canonicalizes with zero
    writes/reviews. ``canonicalize_text`` already folds CRLF/CR to LF (spec
    §4.3) -- this confirms that fold still holds end-to-end through the
    retry-wrapped ``on_change``/``write_if_diff`` call sites this task added.
    See ``tests/battery/test_edit_battery.py``'s golden-fixture-driven
    ``test_e09_crlf_produces_zero_writes_and_zero_reviews`` for the original,
    independently-sourced coverage of this same DoD line.
    """
    conn = _conn()
    root_id = _register_root(conn, tmp_path)
    lf_text = _managed("plain prose, no anchors\n")
    path = tmp_path / "note.md"
    # Same content, CRLF line endings -- canonicalize_text must fold this
    # back to something identical to the LF base before any comparison.
    crlf_bytes = lf_text.replace("\n", "\r\n").encode("utf-8")
    path.write_bytes(crlf_bytes)
    base_store.put(conn, root_id, str(path), lf_text)

    reconciler = Reconciler(conn, OriginTracker())
    mtime_before = path.stat().st_mtime_ns

    reconciler.on_change(str(path))

    # Quiet shortcut: zero writes. The on-disk CRLF bytes are untouched --
    # canonicalization only affects the in-memory comparison, it never forces
    # a rewrite of a file whose content didn't actually change.
    assert path.stat().st_mtime_ns == mtime_before
    assert path.read_bytes() == crlf_bytes
    assert store.find_open_reviews(conn) == []
