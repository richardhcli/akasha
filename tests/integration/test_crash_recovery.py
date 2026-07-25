"""Startup reconcile / crash recovery (task T5.6, spec §4.8, §6.2 E11).

Spec §4.8: "Startup: run ``on_change`` for every managed file (idempotent
-- this is also crash recovery)." This module drives
``akasha.sync.reconcile.reconcile_all`` DIRECTLY against a real (in-memory)
sqlite store and real temp-directory files -- no real ``uvicorn``/daemon
process is spun up (deterministic, fast; matches T5.5/T5.7's own
integration-test style).

Fixture helpers are copied verbatim from ``tests/unit/sync/test_reconcile.py``
/ ``tests/integration/test_conflict.py`` (those modules' own docstrings note
they exist precisely so sibling test files can reuse this pattern) rather
than imported, since ``tests/`` is not a package (no ``__init__.py`` files
anywhere under it).
"""

from __future__ import annotations

import sqlite3

from akasha.contract.parser import parse
from akasha.contract.render import render
from akasha.kernel import ids, store
from akasha.kernel.canonical import canonicalize_text
from akasha.kernel.ids import contract_anchor
from akasha.sync import base_store, reconcile
from akasha.sync.origin import OriginTracker


def _conn() -> sqlite3.Connection:
    conn = store.connect(":memory:", check_same_thread=False)
    store.run_migrations(conn)
    return conn


def _seed_node(
    conn: sqlite3.Connection,
    node_id: str,
    node_type: str,
    body: str,
    task_state: str | None = None,
) -> None:
    """Test-only fixture seeding: insert a node under a CHOSEN id (genesis C0)."""
    now = store._now()
    canonical_body = canonicalize_text(body)
    content = store._node_content(canonical_body, [], task_state)
    with conn:
        obj_hash = store._insert_object(conn, content, now)
        conn.execute(
            "INSERT INTO nodes (id, node_type, head_hash, maturity, status, vetted, "
            "created_at, updated_at) VALUES (?, ?, ?, 'S0', 'live', 0, ?, ?)",
            (node_id, node_type, obj_hash, now, now),
        )
        conn.execute("INSERT INTO nodes_fts (id, body) VALUES (?, ?)", (node_id, canonical_body))
        store._insert_commit(
            conn,
            node_id,
            parents=[],
            object_hash_=obj_hash,
            change_class="major",
            facets_touched=[],
            author="test",
            message="",
            now=now,
        )


def _managed(body: str) -> str:
    return canonicalize_text(f"---\ntm: 1\n---\n{body}")


def _register_root(conn: sqlite3.Connection, root_path) -> str:
    return store.register_sync_root(conn, "vault", str(root_path))["id"]


def _open_review_count(conn: sqlite3.Connection) -> int:
    return len(store.find_open_reviews(conn))


def test_crash_recovery_idempotent(tmp_path):
    """E11: a crash mid-``on_change`` converges on restart with NO lost blocks.

    Simulates the daemon dying AFTER it had already applied the vault
    edit's ``kernel_apply`` (updating the hub head) but BEFORE it reached
    the final canonical write-back / ``base_store.put`` -- the worst-case
    "mid-sync" crash point, since durable state (hub + base_store + the
    raw on-disk file) is now mutually inconsistent: the hub already knows
    about the edit, but ``base_store`` still points at the OLD base and
    the file on disk is the raw (non-canonically-rewritten) vault edit.
    """
    conn = _conn()
    root_id = _register_root(conn, tmp_path)

    x = ids.mint()
    _seed_node(conn, x, "claim", "line one")  # genesis C0

    base_text = render(parse(_managed(f"line one {contract_anchor(x)}\n")))
    path = tmp_path / "note.md"
    base_store.put(conn, root_id, str(path), base_text)
    # The file itself starts out agreeing with the base (a normal, already
    # -synced state) before the "vault edit while daemon was mid-sync"
    # below.
    path.write_text(base_text, encoding="utf-8")

    # --- the vault edit that was in flight when the daemon crashed --------
    vault_text = _managed(f"line two {contract_anchor(x)}\n")
    path.write_text(vault_text, encoding="utf-8")

    # --- simulate: on_change got as far as kernel_apply's commit_node for
    # the "modified" op (hub already reflects "line two") but crashed
    # before write_if_diff/base_store.put ran. base_store + the on-disk
    # bytes are therefore STALE relative to the hub.
    store.commit_node(
        conn, x, new_body="line two", change_class="patch", facets_touched=[], author="sync"
    )
    assert base_store.get(conn, root_id, str(path)) == base_text  # still stale (pre-crash)
    history_before_restart = store.history(conn, x)
    assert len(history_before_restart) == 2  # genesis + the in-flight commit

    # --- "restart": run the startup reconcile ------------------------------
    summary = reconcile.reconcile_all(conn, OriginTracker())
    assert summary == {"files_reconciled": 1, "files_missing": 0, "reviews_open": 0}

    # --- converges to a stable canonical state -----------------------------
    final_text = path.read_text(encoding="utf-8")
    assert base_store.get(conn, root_id, str(path)) == final_text
    hub_projection = render(reconcile.hub_state_for(conn, parse(final_text)))
    assert final_text == hub_projection

    # --- no lost blocks: the anchor survives, no duplicate/lost node -------
    assert contract_anchor(x) in final_text
    assert "line two" in final_text
    assert store.get_node(conn, x).body == "line two\n"

    # --- no double-application: the in-flight commit was NOT re-applied a
    # second time (the vault content already matched the hub head, so this
    # was a convergent no-op, not a conflict or a duplicate commit) --------
    history_after_restart = store.history(conn, x)
    assert len(history_after_restart) == 2
    assert history_after_restart[-1]["hash"] == history_before_restart[-1]["hash"]

    # --- zero conflicts, zero open reviews: this was a genuine convergent
    # edit, not a both-sides conflict ---------------------------------------
    assert _open_review_count(conn) == 0
    conflict_rows = conn.execute(
        "SELECT COUNT(*) FROM review_queue WHERE cause_kind='conflict'"
    ).fetchone()[0]
    assert conflict_rows == 0

    # --- idempotence: a second "restart" makes ZERO further writes --------
    base_before_second = base_store.get(conn, root_id, str(path))
    mtime_before_second = path.stat().st_mtime_ns
    history_before_second = store.history(conn, x)

    summary2 = reconcile.reconcile_all(conn, OriginTracker())
    assert summary2 == {"files_reconciled": 1, "files_missing": 0, "reviews_open": 0}

    assert path.read_text(encoding="utf-8") == final_text
    assert path.stat().st_mtime_ns == mtime_before_second
    assert base_store.get(conn, root_id, str(path)) == base_before_second
    assert store.history(conn, x) == history_before_second
    assert _open_review_count(conn) == 0


def test_crash_recovery_edits_while_daemon_down(tmp_path):
    """E11 (plain form): edits accumulate on disk while no daemon runs at all.

    No manual commit was ever made -- unlike the mid-sync scenario above,
    the hub never saw the edit before "restart". Startup reconcile must
    apply it via a genuine new commit exactly once, then be quiet.
    """
    conn = _conn()
    root_id = _register_root(conn, tmp_path)

    x = ids.mint()
    _seed_node(conn, x, "claim", "original")

    base_text = render(parse(_managed(f"original {contract_anchor(x)}\n")))
    path = tmp_path / "note.md"
    base_store.put(conn, root_id, str(path), base_text)
    path.write_text(base_text, encoding="utf-8")

    # Edited while the daemon was down; nothing observed this write yet.
    vault_text = _managed(f"edited while down {contract_anchor(x)}\n")
    path.write_text(vault_text, encoding="utf-8")

    assert len(store.history(conn, x)) == 1  # genesis only, pre-restart

    summary = reconcile.reconcile_all(conn, OriginTracker())
    assert summary == {"files_reconciled": 1, "files_missing": 0, "reviews_open": 0}

    assert store.get_node(conn, x).body == "edited while down\n"
    history = store.history(conn, x)
    assert len(history) == 2  # exactly one new commit applied
    assert contract_anchor(x) in path.read_text(encoding="utf-8")

    # Idempotent second pass: zero further writes.
    mtime = path.stat().st_mtime_ns
    reconcile.reconcile_all(conn, OriginTracker())
    assert path.stat().st_mtime_ns == mtime
    assert len(store.history(conn, x)) == 2


def test_reconcile_all_empty_is_noop():
    """No managed files at all -- ``reconcile_all`` is a no-op, no crash."""
    conn = _conn()
    summary = reconcile.reconcile_all(conn, OriginTracker())
    assert summary == {"files_reconciled": 0, "files_missing": 0, "reviews_open": 0}


def test_reconcile_all_skips_missing_file_without_crashing(tmp_path):
    """A tracked ``sync_files`` path whose file has vanished is counted, not fatal."""
    conn = _conn()
    root_id = _register_root(conn, tmp_path)

    x = ids.mint()
    _seed_node(conn, x, "claim", "line one")
    base_text = render(parse(_managed(f"line one {contract_anchor(x)}\n")))

    gone_path = tmp_path / "gone.md"
    base_store.put(conn, root_id, str(gone_path), base_text)
    # Never actually written to disk -- and a sibling file that DOES exist,
    # to confirm the missing one doesn't abort the whole pass.
    present_path = tmp_path / "present.md"
    y = ids.mint()
    _seed_node(conn, y, "claim", "hello")
    present_base = render(parse(_managed(f"hello {contract_anchor(y)}\n")))
    base_store.put(conn, root_id, str(present_path), present_base)
    present_path.write_text(present_base, encoding="utf-8")

    summary = reconcile.reconcile_all(conn, OriginTracker())
    assert summary["files_missing"] == 1
    assert summary["files_reconciled"] == 1


def test_reconcile_all_discovers_preexisting_files_never_seen_by_on_change(tmp_path):
    """T11.3: a freshly registered root's pre-existing ``.md`` files are discovered.

    Reproduces T11.1's exact empirical gap (see the ``docs/spec-questions.md``
    entry this task resolves): register a sync root pointing at a tmp dir
    that already contains a real ``.md`` file with a ``^tm-new`` marker --
    never passed through ``on_change``, so it has no ``sync_files`` row at
    all -- then call ``reconcile_all`` and confirm it is discovered and
    reconciled on the very first call.
    """
    conn = _conn()
    root_id = _register_root(conn, tmp_path)

    path = tmp_path / "preexisting.md"
    path.write_text(_managed("A brand new idea ^tm-new\n"), encoding="utf-8")

    # Never touched: no sync_files row, no base snapshot.
    assert store.list_sync_files(conn) == []

    summary = reconcile.reconcile_all(conn, OriginTracker())
    assert summary["files_reconciled"] == 1
    assert summary["files_missing"] == 0
    assert summary["reviews_open"] == 0  # a clean ^tm-new mint raises no review

    tracked = store.list_sync_files(conn)
    assert [f["path"] for f in tracked] == [str(path)]
    assert tracked[0]["sync_root_id"] == root_id

    final_text = path.read_text(encoding="utf-8")
    assert "^tm-new" not in final_text
    assert "A brand new idea" in final_text
    assert base_store.get(conn, root_id, str(path)) == final_text


def test_reconcile_all_discovery_is_idempotent_on_second_call(tmp_path):
    """A second ``reconcile_all`` call after discovery makes zero duplicate writes.

    No duplicate ``sync_files`` row, no duplicate node mint for the same
    ``^tm-new`` marker -- the file was already adopted by the first call.
    """
    conn = _conn()
    _register_root(conn, tmp_path)

    path = tmp_path / "preexisting.md"
    path.write_text(_managed("A brand new idea ^tm-new\n"), encoding="utf-8")

    summary1 = reconcile.reconcile_all(conn, OriginTracker())
    assert summary1["files_reconciled"] == 1
    assert summary1["reviews_open"] == 0

    tracked_after_first = store.list_sync_files(conn)
    assert len(tracked_after_first) == 1
    text_after_first = path.read_text(encoding="utf-8")
    node_ids_after_first = set(store.list_live_node_ids(conn))
    assert len(node_ids_after_first) == 1

    mtime_before_second = path.stat().st_mtime_ns
    summary2 = reconcile.reconcile_all(conn, OriginTracker())
    assert summary2["files_reconciled"] == 1
    assert summary2["files_missing"] == 0
    assert summary2["reviews_open"] == 0

    # No duplicate sync_files row.
    assert len(store.list_sync_files(conn)) == 1
    # No duplicate node mint: the same single node id survives.
    assert store.list_live_node_ids(conn) == node_ids_after_first
    # Zero further writes: the file on disk is untouched.
    assert path.stat().st_mtime_ns == mtime_before_second
    assert path.read_text(encoding="utf-8") == text_after_first


def test_daemon_serve_runs_startup_reconcile_inside_lock(tmp_path, monkeypatch):
    """``daemon.serve`` invokes the T5.6 startup reconcile before ``uvicorn.run``.

    Stubs ``uvicorn.run`` (no real server) and ``create_app`` (a minimal
    stand-in exposing ``state.conn``/``state.config``), and monkeypatches
    ``akasha.sync.reconcile.reconcile_all`` to record that it was called
    while the single-instance lock is already held, and that it ran BEFORE
    ``uvicorn.run``.
    """
    import types

    from akasha import daemon as daemon_module
    from akasha.config import Config
    from akasha.sync import reconcile as reconcile_module

    calls: list[str] = []

    fake_conn = object()

    def fake_create_app(config):
        app = types.SimpleNamespace()
        app.state = types.SimpleNamespace(conn=fake_conn, config=config)
        return app

    def fake_reconcile_all(conn, origin=None, *, projection=None):
        assert conn is fake_conn
        # the lock file must already exist (held) at this point
        lock_path = tmp_path / daemon_module.LOCK_FILE_NAME
        assert lock_path.exists()
        calls.append("reconcile_all")
        return {"files_reconciled": 0, "files_missing": 0, "reviews_open": 0}

    def fake_uvicorn_run(app, **kwargs):
        calls.append("uvicorn.run")

    monkeypatch.setattr("akasha.api.app.create_app", fake_create_app)
    monkeypatch.setattr(reconcile_module, "reconcile_all", fake_reconcile_all)

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", fake_uvicorn_run)

    config = Config(path=tmp_path / "config.toml", db_path=tmp_path / "store.db")
    daemon_module.serve(config)

    assert calls == ["reconcile_all", "uvicorn.run"]
    # lock released after serve() returns
    with daemon_module.single_instance_lock(tmp_path / daemon_module.LOCK_FILE_NAME):
        pass
