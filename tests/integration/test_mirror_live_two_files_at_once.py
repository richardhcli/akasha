"""Two mirrored files each edited, on different lines, within one debounce window (debug-plan D12).

The real production path (a real ``watchdog`` observer bound to ``Reconciler.on_change``, as in
``test_mirror_live.py``). Before D12 the first file's cycle brought the second up to date and,
in doing so, committed the second file's OWN pending edit -- which was then never relayed back,
so the two files stayed different forever with no review item and no error.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from pathlib import Path

from akasha.daemon import _watcher_content_hash
from akasha.kernel import store
from akasha.sync.origin import OriginTracker
from akasha.sync.reconcile import Reconciler
from akasha.sync.watcher import Watcher

_ANCHOR_RE = re.compile(r"\^tm-[0-9a-z]{8}")


def _wait_until(predicate: Callable[[], bool], *, timeout: float = 6.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_two_files_edited_at_once_on_different_mirrored_lines_converge(tmp_path: Path) -> None:
    conn = store.connect(str(tmp_path / "store.db"), check_same_thread=False)
    store.run_migrations(conn)
    vault = tmp_path / "vault"
    vault.mkdir()
    store.register_sync_root(conn, "v", str(vault))
    origin = OriginTracker()
    reconciler = Reconciler(conn, origin)
    watcher = Watcher(
        conn,
        reconciler.on_change,
        debounce_seconds=0.3,
        poll_interval_seconds=0.05,
        origin_tracker=origin,
        content_hash_fn=_watcher_content_hash,
    )
    watcher.start()
    try:
        a, b, c = vault / "a.md", vault / "b.md", vault / "c.md"
        a.write_text("- [ ] first thing ^tm-new\n- [ ] second thing ^tm-new\n", encoding="utf-8")
        assert _wait_until(lambda: len(_ANCHOR_RE.findall(a.read_text(encoding="utf-8"))) == 2)
        settled = a.read_text(encoding="utf-8")
        b.write_text(settled, encoding="utf-8")
        c.write_text(settled, encoding="utf-8")
        assert _wait_until(lambda: len(store.list_sync_files(conn)) == 3)
        time.sleep(0.6)

        # Two different lines, two different files, milliseconds apart.
        a.write_text(
            a.read_text(encoding="utf-8").replace("first thing", "FIRST"), encoding="utf-8"
        )
        b.write_text(
            b.read_text(encoding="utf-8").replace("second thing", "SECOND"), encoding="utf-8"
        )

        def converged() -> bool:
            return all(
                "] FIRST ^tm-" in p.read_text(encoding="utf-8")
                and "] SECOND ^tm-" in p.read_text(encoding="utf-8")
                for p in (a, b, c)
            )

        assert _wait_until(converged), {p.name: p.read_text(encoding="utf-8") for p in (a, b, c)}
        assert store.find_open_reviews(conn) == []
    finally:
        watcher.stop()
