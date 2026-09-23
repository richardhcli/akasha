"""Live transclusion: edit one file, the other changes (task T19.6, spec §4.7 "Mirrors").

The user-visible claim of milestone M19 -- "when I edit one section that is
transcluded, that same text is changed as soon as possible in the other file"
-- proven through the REAL production path, not ``Reconciler.on_change``
called directly (debug-plan D10 is the standing lesson for what direct-call
tests miss): a real ``watchdog`` observer thread, a persistent
``OriginTracker``/``Reconciler`` pair bound to a real ``Watcher`` (the same
classes ``daemon.serve()`` wires together), two real files on disk sharing one
``^tm-id`` anchor, and a real ``create_app`` for the hub-side leg.

Like ``test_watcher_wiring.py`` it deliberately does not start a full uvicorn
daemon (``test_daemon_lock_multiprocess.py``'s stated flakiness reason).

Measured latency (2026-09-23, development machine, ``pytest -s``, n=6): edit
written to A -> B's bytes changed on disk in 0.122-0.124 s with the watcher's
``debounce_seconds=0.1`` used here. The production default is the spec's 500 ms
debounce, so expect roughly half a second end to end. The assertion is a 3 s
ceiling, not a claim that it is that slow.
"""

from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from akasha.api import auth
from akasha.api.app import create_app
from akasha.daemon import _watcher_content_hash
from akasha.kernel import store
from akasha.sync.reconcile import Reconciler
from akasha.sync.watcher import Watcher

_ANCHOR_RE = re.compile(r"\^tm-([0-9a-z]{8})\b")
_LATENCY_CEILING_SECONDS = 3.0


def _wait_until(predicate, *, timeout: float = 5.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _insert_human_token(conn, secret: str) -> dict[str, str]:
    conn.execute(
        "INSERT INTO tokens (id, name, class, secret_hash, rate_per_min, created_at, "
        "revoked_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "humantoken",
            "humantoken",
            "human",
            auth.hash_secret(secret),
            None,
            "2026-01-01T00:00:00.000000+00:00",
            None,
        ),
    )
    conn.commit()
    return {"Authorization": f"Bearer {auth.format_bearer_token('humantoken', secret)}"}


def test_editing_one_file_changes_the_mirror_in_the_other_in_real_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = store.connect(str(tmp_path / "store.db"), check_same_thread=False)
    store.run_migrations(conn)
    headers = _insert_human_token(conn, auth.mint_secret())
    app = create_app(conn=conn)
    client = TestClient(app)

    vault = tmp_path / "vault"
    vault.mkdir()
    store.register_sync_root(conn, "live-vault", str(vault))
    a, b = vault / "a.md", vault / "b.md"

    # The daemon wires its watcher's reconciler to the app's OriginTracker
    # (T13.3) so a write on the request path is echo-suppressed; do the same.
    origin = app.state.origin_tracker
    reconciler = Reconciler(conn, origin)
    on_change_calls: list[str] = []

    def counting_on_change(path: str) -> None:
        on_change_calls.append(Path(path).name)
        reconciler.on_change(path)

    # Count what actually matters -- bytes the daemon WRITES -- not reconcile
    # cycles. One daemon write can raise more than one filesystem event and the
    # origin tracker's echo record is single-use, so the second event may reach
    # the reconciler as a QUIET cycle (no write, no propagation). That is
    # harmless and pre-existing; a duplicate WRITE would be the real ping-pong.
    writes: list[str] = []
    real_write = Reconciler.write_if_diff

    def spy_write(self, path: str, text: str) -> bool:
        wrote = real_write(self, path, text)
        if wrote:
            writes.append(Path(path).name)
        return wrote

    monkeypatch.setattr(Reconciler, "write_if_diff", spy_write)

    watcher = Watcher(
        conn,
        counting_on_change,
        debounce_seconds=0.1,
        poll_interval_seconds=0.05,
        origin_tracker=origin,
        content_hash_fn=_watcher_content_hash,
    )
    watcher.start()
    try:
        # --- set up the mirror the way a user does: mint in A, paste into B ---
        a.write_text("---\ntm: 1\n---\n- [ ] ship the release ^tm-new\n", encoding="utf-8")
        assert _wait_until(lambda: _ANCHOR_RE.search(a.read_text(encoding="utf-8")) is not None), (
            "the watcher never minted an anchor for A's ^tm-new line"
        )
        match = _ANCHOR_RE.search(a.read_text(encoding="utf-8"))
        assert match is not None
        x = match.group(1)
        anchor = f"^tm-{x}"

        b.write_text(f"---\ntm: 1\n---\n- [ ] ship the release {anchor}\n", encoding="utf-8")
        assert _wait_until(lambda: len(reconciler.projection.owners(x)) == 2)  # type: ignore[union-attr]
        # a copy-paste is a mirror, not a violation: nothing queued for review
        assert client.get("/v1/review?status=open", headers=headers).json()["reviews"] == []
        time.sleep(0.4)  # let both files fully settle before measuring anything

        # --- leg 1: edit A on disk -> B's bytes change, no rescan, no restart ---
        b_before = _sha(b)
        writes_at_edit = len(writes)
        started = time.monotonic()
        a.write_text(
            f"---\ntm: 1\n---\n- [ ] ship the RELEASE candidate {anchor}\n", encoding="utf-8"
        )
        assert _wait_until(
            lambda: "RELEASE candidate" in b.read_text(encoding="utf-8"),
            timeout=_LATENCY_CEILING_SECONDS,
        ), "B never received A's edit within the latency ceiling"
        latency = time.monotonic() - started
        print(f"mirror latency A->B: {latency:.3f}s")  # visible with `pytest -s`
        assert latency < _LATENCY_CEILING_SECONDS
        assert _sha(b) != b_before
        assert f"- [ ] ship the RELEASE candidate {anchor}" in b.read_text(encoding="utf-8")
        assert store.get_node(conn, x).body == "ship the RELEASE candidate\n"

        # --- leg 2: exactly ONE daemon write (to the mirror), then quiet --------
        time.sleep(0.6)  # long enough for any wrongly-forwarded event to debounce and fire
        assert writes[writes_at_edit:] == ["b.md"], (
            "expected exactly one daemon write (the mirror); A is the human's own "
            f"edit and nothing may be rewritten twice: {writes[writes_at_edit:]}"
        )
        assert client.get("/v1/review?status=open", headers=headers).json()["reviews"] == []
        settled = (_sha(a), _sha(b))
        time.sleep(0.4)
        assert (_sha(a), _sha(b)) == settled  # nothing keeps rewriting (no ping-pong)

        # --- leg 3: the other direction, and the checkbox --------------------
        b.write_text(
            f"---\ntm: 1\n---\n- [x] ship the RELEASE candidate {anchor}\n", encoding="utf-8"
        )
        assert _wait_until(
            lambda: f"- [x] ship the RELEASE candidate {anchor}" in a.read_text(encoding="utf-8"),
            timeout=_LATENCY_CEILING_SECONDS,
        ), "ticking the checkbox in B never reached A"
        assert store.get_node(conn, x).task_state == "done"

        # --- leg 4: non-Markdown files never reach the reconciler (D10) ------
        time.sleep(0.6)  # leg 3's own propagation write must have settled first
        calls_before = len(on_change_calls)
        (vault / "notes.txt").write_text(f"stray {anchor}\n", encoding="utf-8")
        time.sleep(0.5)
        assert len(on_change_calls) == calls_before

        # --- leg 5: a hub-side edit (CLI/UI/API) rewrites BOTH files ---------
        time.sleep(0.4)
        writes_before = len(writes)
        resp = client.patch(
            f"/v1/nodes/{x}",
            json={"body": "changed on the hub", "change_class": "patch", "facets_touched": []},
            headers=headers,
        )
        assert resp.status_code == 200
        for f in (a, b):  # rewritten within the request itself -- no waiting
            assert f"- [x] changed on the hub {anchor}" in f.read_text(encoding="utf-8")
        time.sleep(0.6)  # any echo-suppression failure would show up as extra writes now
        assert sorted(writes[writes_before:]) == ["a.md", "b.md"], (
            f"each mirror must be written exactly once by the API edit: {writes[writes_before:]}"
        )
    finally:
        watcher.stop()
