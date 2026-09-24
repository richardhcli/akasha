"""Debug-plan D11: `POST /v1/sync/roots` starts watching the folder before it answers.

Before, only the watcher's poll loop noticed a new root (up to one tick later). A file
written right after registering was then neither in the caller's follow-up rescan nor
seen by any filesystem event, and was lost until the daemon restarted -- so a script
running `akasha setup dir && edit dir/a.md` silently lost the edit, and a mirror written
in that window was never adopted.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from akasha.api import auth
from akasha.api.app import create_app
from akasha.daemon import _watcher_content_hash
from akasha.kernel import store
from akasha.sync.origin import OriginTracker
from akasha.sync.reconcile import Reconciler
from akasha.sync.watcher import Watcher


def _wait_until(predicate: Callable[[], bool], *, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def _human_headers(conn: Any) -> dict[str, str]:
    secret = auth.mint_secret()
    conn.execute(
        "INSERT INTO tokens (id, name, class, secret_hash, rate_per_min, created_at, "
        "revoked_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("humantoken", "humantoken", "human", auth.hash_secret(secret), None,
         "2026-01-01T00:00:00.000000+00:00", None),
    )  # fmt: skip
    conn.commit()
    return {"Authorization": f"Bearer {auth.format_bearer_token('humantoken', secret)}"}


class _SpyObserver:
    def __init__(self) -> None:
        self.scheduled: list[tuple[str, bool]] = []

    def schedule(self, event_handler: Any, path: str, *, recursive: bool = False) -> object:
        self.scheduled.append((path, recursive))
        return object()

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def join(self, timeout: float | None = None) -> None: ...


def test_the_route_schedules_the_watch_before_it_responds(tmp_path: Path) -> None:
    conn = store.connect(str(tmp_path / "store.db"), check_same_thread=False)
    store.run_migrations(conn)
    headers = _human_headers(conn)
    app = create_app(conn=conn)
    spy = _SpyObserver()
    # The poll loop's only chance to pick a root up is its first tick, which runs at
    # start(), before the root exists; after that it sleeps for an hour. So anything
    # scheduled below was scheduled by the route itself.
    watcher = Watcher(
        conn, lambda _p: None, poll_interval_seconds=3600.0, observer_factory=lambda: spy
    )
    app.state.watcher = watcher
    watcher.start()
    try:
        vault = tmp_path / "vault"
        vault.mkdir()
        response = TestClient(app).post(
            "/v1/sync/roots", json={"name": "v", "root_path": str(vault)}, headers=headers
        )
        assert response.status_code == 201, response.text
        assert spy.scheduled == [(str(vault), True)]
    finally:
        watcher.stop()


def test_a_watch_failure_does_not_fail_the_registration(tmp_path: Path) -> None:
    conn = store.connect(str(tmp_path / "store.db"), check_same_thread=False)
    store.run_migrations(conn)
    headers = _human_headers(conn)
    app = create_app(conn=conn)

    class _Broken:
        def watch_new_roots(self, rows: object = None) -> None:
            raise OSError("inotify watch limit reached")

    app.state.watcher = _Broken()
    vault = tmp_path / "vault"
    vault.mkdir()
    response = TestClient(app).post(
        "/v1/sync/roots", json={"name": "v", "root_path": str(vault)}, headers=headers
    )
    assert response.status_code == 201  # durably registered; the poll loop will retry
    assert [r["name"] for r in store.list_sync_roots(conn)] == ["v"]


def test_a_file_written_the_instant_registration_returns_is_reconciled(tmp_path: Path) -> None:
    """A REAL watcher: no sleep between the response and the write."""
    conn = store.connect(str(tmp_path / "store.db"), check_same_thread=False)
    store.run_migrations(conn)
    headers = _human_headers(conn)
    app = create_app(conn=conn)
    origin = OriginTracker()
    reconciler = Reconciler(conn, origin)
    cycles: list[str] = []

    def on_cycle(path: str) -> None:
        cycles.append(path)
        reconciler.on_change(path)

    watcher = Watcher(
        conn,
        on_cycle,
        debounce_seconds=0.05,
        poll_interval_seconds=0.5,  # slower than the write, as a busy machine would be
        origin_tracker=origin,
        content_hash_fn=_watcher_content_hash,
    )
    app.state.watcher = watcher
    watcher.start()
    try:
        time.sleep(0.6)  # let the poll loop's first tick pass, so it cannot rescue the write
        vault = tmp_path / "vault"
        vault.mkdir()
        response = TestClient(app).post(
            "/v1/sync/roots", json={"name": "v", "root_path": str(vault)}, headers=headers
        )
        assert response.status_code == 201
        note = vault / "note.md"
        note.write_text("---\ntm: 1\n---\n\nwritten at once. ^tm-new\n", encoding="utf-8")

        assert _wait_until(lambda: str(note) in cycles), (
            "the write right after registering was lost"
        )
        assert _wait_until(lambda: "^tm-new" not in note.read_text(encoding="utf-8"))
    finally:
        watcher.stop()
