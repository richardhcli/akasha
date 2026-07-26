"""T9.6: the live filesystem `Watcher` actually reconciles a real on-disk
edit, with no manual `POST /v1/sync/rescan` or `reconcile_all` call
(build-plan T9.6, spec mvp-spec.md architecture diagram: "watcher ->
sync/reconcile -> kernel").

Deliberately does NOT spin up a full ``uvicorn``/``daemon.serve()``
daemon -- ``test_daemon_lock_multiprocess.py``'s own docstring already
established that's flaky for this repo's test suite ("deliberately does
NOT spin up full uvicorn daemons"). Instead this drives the exact same
real classes ``daemon.serve()`` wires together (one persistent
``OriginTracker``/``Reconciler`` pair bound to a real ``Watcher``, using
the REAL ``watchdog`` observer -- not a spy, unlike every other Watcher
test in ``tests/unit/sync/test_watcher.py``), which proves the wiring
itself without the HTTP-server flakiness risk.
"""

from __future__ import annotations

import time
from pathlib import Path

from akasha.daemon import _watcher_content_hash
from akasha.kernel import store
from akasha.sync.origin import OriginTracker
from akasha.sync.reconcile import Reconciler
from akasha.sync.watcher import Watcher


def _wait_until(predicate, *, timeout: float = 5.0, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def test_live_edit_is_reconciled_with_no_manual_rescan(tmp_path: Path) -> None:
    conn = store.connect(str(tmp_path / "store.db"), check_same_thread=False)
    store.run_migrations(conn)

    vault = tmp_path / "vault"
    vault.mkdir()
    store.register_sync_root(conn, "live-vault", str(vault))
    note = vault / "note.md"

    origin = OriginTracker()
    reconciler = Reconciler(conn, origin)
    watcher = Watcher(
        conn,
        reconciler.on_change,
        debounce_seconds=0.1,
        poll_interval_seconds=0.05,
        origin_tracker=origin,
        content_hash_fn=_watcher_content_hash,
    )
    watcher.start()
    try:
        # The file is created AFTER the watcher is already running -- this
        # is the real event the watcher must react to on its own, not a
        # pre-existing file a startup scan would find (that's T11.3's
        # discover_untracked_files, a different mechanism/task).
        note.write_text(
            "---\ntm: 1\n---\n\nHello from the live watcher. ^tm-new\n", encoding="utf-8"
        )

        assert _wait_until(lambda: len(store.list_sync_files(conn)) == 1), (
            "watcher never reconciled the live on-disk edit -- no manual "
            "rescan or reconcile_all call was made in this test"
        )

        live_nodes = [
            n
            for n in (store.get_node(conn, nid) for nid in _all_claim_ids(conn))
        ]
        assert any("Hello from the live watcher." in n.body for n in live_nodes), (
            f"no live node has the expected body; got {[n.body for n in live_nodes]!r}"
        )

        written_back = note.read_text(encoding="utf-8")
        assert "^tm-new" not in written_back, (
            "the ^tm-new request should have been minted into a real ^tm-<id8> "
            "anchor and written back to the file by the live watcher's own cycle"
        )
    finally:
        watcher.stop()


def _all_claim_ids(conn) -> list[str]:
    return [row[0] for row in conn.execute("SELECT id FROM nodes WHERE status='live'")]
