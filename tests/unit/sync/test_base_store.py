"""Base store tests (task T5.1, spec §4.8 ``base_store``, §4.4 ``objects``/
``sync_files.base_hash``).

Covers, per this task's DoD and the user's holistic-testing requirement:
put/get round-trips canonical bytes; the sync-root association is retained
across the round-trip (get scoped to the right root); a fresh path returns
``None``; an unknown ``sync_root_id`` is rejected; non-canonical input
(CRLF, non-NFC, missing trailing newline) is canonicalized on the way in,
never stored verbatim; idempotent re-``put`` of identical bytes reuses the
same content-addressed ``objects`` row (no duplicate row / same hash).
"""

from __future__ import annotations

import unicodedata

import pytest

from akasha.kernel import store
from akasha.sync import base_store


def _fresh_conn(tmp_path):
    conn = store.connect(tmp_path / "store_base.db")
    store.run_migrations(conn)
    return conn


def _register_root(conn, name="vault-a", root_path="/vaults/a"):
    return store.register_sync_root(conn, name, root_path)["id"]


def test_put_get_round_trips_canonical_bytes(tmp_path):
    conn = _fresh_conn(tmp_path)
    root_id = _register_root(conn)

    base_store.put(conn, root_id, "notes/a.md", "hello world\n")

    assert base_store.get(conn, root_id, "notes/a.md") == "hello world\n"


def test_sync_root_association_is_retained_across_round_trip(tmp_path):
    conn = _fresh_conn(tmp_path)
    root_a = _register_root(conn, name="vault-a", root_path="/vaults/a")
    root_b = _register_root(conn, name="vault-b", root_path="/vaults/b")

    base_store.put(conn, root_a, "notes/a.md", "content for root a\n")

    # Same path, wrong root: must NOT read back root a's snapshot.
    assert base_store.get(conn, root_b, "notes/a.md") is None
    # Right root: reads back correctly.
    assert base_store.get(conn, root_a, "notes/a.md") == "content for root a\n"

    # The underlying sync_files row records the association durably.
    row = conn.execute(
        "SELECT sync_root_id FROM sync_files WHERE path=?", ("notes/a.md",)
    ).fetchone()
    assert row[0] == root_a


def test_fresh_path_returns_none(tmp_path):
    conn = _fresh_conn(tmp_path)
    root_id = _register_root(conn)

    assert base_store.get(conn, root_id, "notes/never-written.md") is None


def test_unknown_sync_root_id_is_rejected_on_put(tmp_path):
    conn = _fresh_conn(tmp_path)

    with pytest.raises(store.SyncRootNotFoundError):
        base_store.put(conn, "nonexistent-root", "notes/a.md", "hello\n")

    # Nothing should have been persisted.
    assert (
        conn.execute("SELECT 1 FROM sync_files WHERE path=?", ("notes/a.md",)).fetchone() is None
    )


def test_unknown_sync_root_id_on_get_reads_as_none(tmp_path):
    # Design decision (build-plan T5.1 Steps: validation is a `put()`
    # concern only — "put(...) validates the durable sync root"; `get()`'s
    # contract is just "returns last-agreed bytes or None"): an unknown
    # sync_root_id on a read path behaves exactly like a fresh/unassociated
    # path rather than raising, since there is nothing to protect against
    # writing.
    conn = _fresh_conn(tmp_path)

    assert base_store.get(conn, "nonexistent-root", "notes/a.md") is None


def test_crlf_input_is_canonicalized_not_stored_verbatim(tmp_path):
    conn = _fresh_conn(tmp_path)
    root_id = _register_root(conn)

    base_store.put(conn, root_id, "notes/crlf.md", "line one\r\nline two\r\n")

    got = base_store.get(conn, root_id, "notes/crlf.md")
    assert got == "line one\nline two\n"
    assert "\r" not in got


def test_non_nfc_input_is_canonicalized_to_nfc(tmp_path):
    conn = _fresh_conn(tmp_path)
    root_id = _register_root(conn)

    # "e" + combining acute accent (NFD) rather than the precomposed "é" (NFC).
    nfd_text = "café note\n"
    assert not unicodedata.is_normalized("NFC", nfd_text)

    base_store.put(conn, root_id, "notes/nfd.md", nfd_text)

    got = base_store.get(conn, root_id, "notes/nfd.md")
    assert got == unicodedata.normalize("NFC", nfd_text)
    assert unicodedata.is_normalized("NFC", got)


def test_missing_trailing_newline_is_canonicalized(tmp_path):
    conn = _fresh_conn(tmp_path)
    root_id = _register_root(conn)

    base_store.put(conn, root_id, "notes/no-trailing-nl.md", "no trailing newline here")

    got = base_store.get(conn, root_id, "notes/no-trailing-nl.md")
    assert got == "no trailing newline here\n"
    assert got.endswith("\n") and not got.endswith("\n\n")


def test_idempotent_reput_of_identical_bytes_reuses_same_object(tmp_path):
    conn = _fresh_conn(tmp_path)
    root_id = _register_root(conn)

    base_store.put(conn, root_id, "notes/a.md", "stable content\n")
    hash_1 = conn.execute(
        "SELECT base_hash FROM sync_files WHERE path=?", ("notes/a.md",)
    ).fetchone()[0]
    object_count_1 = conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0]

    # Re-put the same (already-canonical) content again.
    base_store.put(conn, root_id, "notes/a.md", "stable content\n")
    hash_2 = conn.execute(
        "SELECT base_hash FROM sync_files WHERE path=?", ("notes/a.md",)
    ).fetchone()[0]
    object_count_2 = conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0]

    assert hash_1 == hash_2
    assert object_count_1 == object_count_2  # no duplicate objects row


def test_reput_with_different_content_advances_base_hash(tmp_path):
    conn = _fresh_conn(tmp_path)
    root_id = _register_root(conn)

    base_store.put(conn, root_id, "notes/a.md", "version one\n")
    hash_1 = conn.execute(
        "SELECT base_hash FROM sync_files WHERE path=?", ("notes/a.md",)
    ).fetchone()[0]

    base_store.put(conn, root_id, "notes/a.md", "version two\n")
    hash_2 = conn.execute(
        "SELECT base_hash FROM sync_files WHERE path=?", ("notes/a.md",)
    ).fetchone()[0]

    assert hash_1 != hash_2
    assert base_store.get(conn, root_id, "notes/a.md") == "version two\n"
    # The old object row is retained (content-addressed, append-only §4.4) —
    # base_store overwriting the *pointer* never deletes prior objects.
    assert conn.execute("SELECT 1 FROM objects WHERE hash=?", (hash_1,)).fetchone() is not None


def test_base_snapshot_object_is_gc_reachable(tmp_path):
    """sync_files.base_hash keeps its object alive across gc_objects (T1.7 precedent)."""
    conn = _fresh_conn(tmp_path)
    root_id = _register_root(conn)

    base_store.put(conn, root_id, "notes/a.md", "keep me\n")
    base_hash = conn.execute(
        "SELECT base_hash FROM sync_files WHERE path=?", ("notes/a.md",)
    ).fetchone()[0]

    store.gc_objects(conn)

    assert conn.execute("SELECT 1 FROM objects WHERE hash=?", (base_hash,)).fetchone() is not None
