"""SQLite access layer: the only module that writes SQLite (rule 0.4).

Migration runner, node/commit-DAG store, edges, deletion/redirects/split/merge, maturity recompute,
review queue, tokens, sync roots and files, GC.

Content addressing: ``objects.hash`` and ``commits.hash`` are the sha256 of the canonical JSON of
their own content. A node's versioned content (body, facets, task_state) is one ``node_snapshot``
object; ``node_type``/``maturity``/``status``/``vetted`` live only on the ``nodes`` row.

# SPEC-QUESTION (T1.3): §4.4/§4.5 do not pin the blob layout, whether ``commits.hash`` is
# content-addressed, or ``history()`` order. Narrowest reading above; docs/spec-questions.md
# T1.3.
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, get_args

from akasha.kernel import ids, maturity
from akasha.kernel.canonical import canonical_json, canonicalize_text, object_hash
from akasha.kernel.model import (
    JUSTIFICATION_EDGE_TYPES,
    ChangeClass,
    Edge,
    EdgeType,
    Facet,
    Node,
    NodeType,
)


def _migrations_dir() -> Path:
    """Resolve ``migrations/``: the packaged copy (installed wheel), the PyInstaller bundle, else
    the repo root.

    # SPEC-QUESTION (T12.5): a frozen build has no repo root (everything is under
    # ``sys._MEIPASS``), so ``parents[3]`` would point at an arbitrary temp ancestor; check
    # ``sys.frozen`` first. Non-frozen callers are unaffected. See docs/spec-questions.md.
    """
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "migrations"  # type: ignore[attr-defined]
    packaged = Path(__file__).resolve().parents[1] / "migrations"
    if packaged.is_dir():
        return packaged
    return Path(__file__).resolve().parents[3] / "migrations"


MIGRATIONS_DIR = _migrations_dir()

_VALID_NODE_TYPES = frozenset(get_args(NodeType))
_VALID_CHANGE_CLASSES = frozenset(get_args(ChangeClass))
_MINT_RETRY_BOUND = 10


class NodeNotFoundError(Exception):
    """Raised when a node id (or as-of timestamp) has no matching row/commit."""


class EdgeNotFoundError(Exception):
    """Raised when an edge id has no matching row (spec §4.5)."""


class TokenNotFoundError(Exception):
    """Raised when a token id has no matching ``tokens`` row (task T4.5)."""


class SyncRootNotFoundError(Exception):
    """Raised when a ``sync_root_id`` has no matching ``sync_roots`` row (task T5.1).

    Used to reject writes (e.g. ``base_store.put``) scoped to a sync root
    that was never durably registered via ``register_sync_root`` (T4.10).
    """


class IdMintError(Exception):
    """Raised when minting a unique node id fails after the retry bound (spec §4.1)."""


class ReviewNotFoundError(Exception):
    """Raised when a ``review_queue`` id has no matching row (task T7.5)."""

    def __init__(self, review_id: str) -> None:
        self.review_id = review_id
        super().__init__(f"review {review_id!r} not found")


class ReviewAlreadyResolvedError(Exception):
    """Raised when resolving or approving an already-resolved review (task T7.5)."""

    def __init__(self, review_id: str) -> None:
        self.review_id = review_id
        super().__init__(f"review {review_id!r} is already resolved")


class NeedsRedirectError(Exception):
    """Raised by ``delete_node`` when an S1+ node is deleted without
    ``redirect_to`` or ``tombstone=True`` (spec §4.5/§4.6: "Deletion: S0 ->
    hard delete; S1+ -> require redirect_to successors or explicit
    tombstone; API returns 409 E_NEEDS_REDIRECT otherwise"). Carries
    ``.code`` so the (future) API layer can map it to the 409 response
    without re-deriving the error string.
    """

    code = "E_NEEDS_REDIRECT"

    def __init__(self, node_id: str) -> None:
        self.node_id = node_id
        super().__init__(
            f"node {node_id!r} is S1+ and requires redirect_to or tombstone=True to delete"
        )


def _now() -> str:
    """Current UTC instant as a fixed-width, lexically-sortable ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def connect(db_path: str | Path, *, check_same_thread: bool = True) -> sqlite3.Connection:
    """Open a WAL sqlite3 connection (spec §3 PRAGMAs).

    The daemon opens one connection per request (``api/deps.py::get_conn``): WAL allows concurrent
    readers plus one writer, and one connection shared across the ASGI threadpool corrupts reads
    (SPEC-QUESTION T8.5b). ``busy_timeout`` makes a writer wait for a held lock instead of raising
    ``SQLITE_BUSY``.
    """
    conn = sqlite3.connect(db_path, check_same_thread=check_same_thread)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _ensure_bookkeeping(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "filename TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now'))"
        ")"
    )


def applied_migrations(conn: sqlite3.Connection) -> set[str]:
    _ensure_bookkeeping(conn)
    rows = conn.execute("SELECT filename FROM schema_migrations").fetchall()
    return {row[0] for row in rows}


def run_migrations(
    conn: sqlite3.Connection, migrations_dir: str | Path = MIGRATIONS_DIR
) -> list[str]:
    """Apply pending .sql files from migrations_dir in filename order, once each.

    Refuses to re-run an already-applied file and never applies files out of
    the sorted-filename order (forward-only, per spec §4.4 note).
    """
    _ensure_bookkeeping(conn)
    applied = applied_migrations(conn)
    files = sorted(Path(migrations_dir).glob("*.sql"))
    newly_applied: list[str] = []
    for path in files:
        if path.name in applied:
            continue
        sql = path.read_text(encoding="utf-8")
        with conn:
            conn.executescript(sql)
            conn.execute("INSERT INTO schema_migrations (filename) VALUES (?)", (path.name,))
        newly_applied.append(path.name)
    return newly_applied


def _mint_unique_id(conn: sqlite3.Connection) -> str:
    """Mint an id not already present in ``nodes`` (spec §4.1: retry bound 10, then error)."""
    for _ in range(_MINT_RETRY_BOUND):
        candidate = ids.mint()
        row = conn.execute("SELECT 1 FROM nodes WHERE id=?", (candidate,)).fetchone()
        if row is None:
            return candidate
    raise IdMintError(f"failed to mint a unique node id after {_MINT_RETRY_BOUND} attempts")


def _mint_unique_edge_id(conn: sqlite3.Connection) -> str:
    """Mint an id not already present in ``edges`` (spec §4.1: retry bound 10, then error)."""
    for _ in range(_MINT_RETRY_BOUND):
        candidate = ids.mint()
        row = conn.execute("SELECT 1 FROM edges WHERE id=?", (candidate,)).fetchone()
        if row is None:
            return candidate
    raise IdMintError(f"failed to mint a unique edge id after {_MINT_RETRY_BOUND} attempts")


def _mint_unique_review_id(conn: sqlite3.Connection) -> str:
    """Mint an id not already present in ``review_queue`` (spec §4.1: retry bound 10)."""
    for _ in range(_MINT_RETRY_BOUND):
        candidate = ids.mint()
        row = conn.execute("SELECT 1 FROM review_queue WHERE id=?", (candidate,)).fetchone()
        if row is None:
            return candidate
    raise IdMintError(f"failed to mint a unique review id after {_MINT_RETRY_BOUND} attempts")


def get_edge_dst(conn: sqlite3.Connection, edge_id: str) -> str:
    """Read-only: return edge_id's ``dst`` node id. Raises ``EdgeNotFoundError`` if unknown.

    Used by the T4.6 agent-proposal gate for ``DELETE /edges/{id}``.
    ``dst`` is the affected node because inbound edges determine its
    maturity and review-relevant state.
    """
    row = conn.execute("SELECT dst FROM edges WHERE id=?", (edge_id,)).fetchone()
    if row is None:
        raise EdgeNotFoundError(edge_id)
    return row[0]


def enqueue_review(
    conn: sqlite3.Connection,
    node_id: str | None,
    cause_kind: str,
    *,
    cause_ref: str | None = None,
    facet: str | None = None,
) -> dict[str, Any]:
    """Append one ``review_queue`` row and return it as a dict (spec §4.4, §4.6, §4.11).

    ``cause_kind`` is the closed enum from the §4.4 DDL; it is not validated here (callers are
    trusted in-repo sites). ``node_id`` is NULL only for a create-node proposal that has no node
    yet.
    """
    with conn:
        return enqueue_review_within_transaction(
            conn, node_id, cause_kind, cause_ref=cause_ref, facet=facet
        )


def enqueue_review_within_transaction(
    conn: sqlite3.Connection,
    node_id: str | None,
    cause_kind: str,
    *,
    cause_ref: str | None = None,
    facet: str | None = None,
) -> dict[str, Any]:
    """``enqueue_review`` without its own transaction.

    sqlite3's ``with conn:`` commits on every block exit, so a function that runs inside another
    mutation's transaction (``tms/invalidate.py`` via ``commit_node``) must not open a nested one:
    it would commit the caller's pending writes early, breaking spec §4.9's atomic-with-the-commit
    rule.
    """
    now = _now()
    review_id = _mint_unique_review_id(conn)
    conn.execute(
        "INSERT INTO review_queue (id, node_id, cause_kind, cause_ref, facet, "
        "created_at, resolved_at, resolution) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)",
        (review_id, node_id, cause_kind, cause_ref, facet, now),
    )
    return {
        "id": review_id,
        "node_id": node_id,
        "cause_kind": cause_kind,
        "cause_ref": cause_ref,
        "facet": facet,
        "created_at": now,
        "resolved_at": None,
        "resolution": None,
    }


def find_open_reviews(
    conn: sqlite3.Connection,
    *,
    node_id: str | None = None,
    cause_kind: str | None = None,
    cause_ref: str | None = None,
) -> list[dict[str, Any]]:
    """Read-only: every open (``resolved_at IS NULL``) review row; the optional filters are ANDed.

    Also the idempotence gate for conflict replay (T5.6) and the source of ``/sync/status``.
    """
    clauses = ["resolved_at IS NULL"]
    params: list[Any] = []
    if node_id is not None:
        clauses.append("node_id=?")
        params.append(node_id)
    if cause_kind is not None:
        clauses.append("cause_kind=?")
        params.append(cause_kind)
    if cause_ref is not None:
        clauses.append("cause_ref=?")
        params.append(cause_ref)
    where = " AND ".join(clauses)
    rows = conn.execute(
        "SELECT id, node_id, cause_kind, cause_ref, facet, created_at, resolved_at, resolution "
        f"FROM review_queue WHERE {where}",
        params,
    ).fetchall()
    return [
        {
            "id": r[0],
            "node_id": r[1],
            "cause_kind": r[2],
            "cause_ref": r[3],
            "facet": r[4],
            "created_at": r[5],
            "resolved_at": r[6],
            "resolution": r[7],
        }
        for r in rows
    ]


def get_review(conn: sqlite3.Connection, review_id: str) -> dict[str, Any]:
    """Read-only: one ``review_queue`` row by id (task T7.5).

    Returns the row as a plain dict (same shape as ``find_open_reviews`` /
    ``enqueue_review``). Raises ``ReviewNotFoundError`` if ``review_id``
    does not exist. Includes resolved rows (unlike ``find_open_reviews``),
    so resolution/approval paths can reject already-resolved items.
    """
    row = conn.execute(
        "SELECT id, node_id, cause_kind, cause_ref, facet, created_at, resolved_at, resolution "
        "FROM review_queue WHERE id=?",
        (review_id,),
    ).fetchone()
    if row is None:
        raise ReviewNotFoundError(review_id)
    return {
        "id": row[0],
        "node_id": row[1],
        "cause_kind": row[2],
        "cause_ref": row[3],
        "facet": row[4],
        "created_at": row[5],
        "resolved_at": row[6],
        "resolution": row[7],
    }


_VALID_RESOLUTIONS = frozenset({"still_holds", "revised", "retracted", "dismissed"})


def resolve_review_within_transaction(
    conn: sqlite3.Connection,
    review_id: str,
    resolution: str,
) -> dict[str, Any]:
    """Body of ``resolve_review``, without opening its own transaction (task T7.5).

    Invariant: identical to ``resolve_review`` but assumes the caller already
    holds an open ``with conn:`` block — mirrors ``enqueue_review`` /
    ``enqueue_review_within_transaction``. sqlite3's ``with conn:`` commits
    on every block exit, so nested wrappers must never open a second one.
    """
    if resolution not in _VALID_RESOLUTIONS:
        raise ValueError(
            f"invalid resolution {resolution!r}; must be one of {_VALID_RESOLUTIONS}"
        )
    row = get_review(conn, review_id)
    if row["resolved_at"] is not None:
        raise ReviewAlreadyResolvedError(review_id)
    now = _now()
    conn.execute(
        "UPDATE review_queue SET resolved_at=?, resolution=? WHERE id=?",
        (now, resolution, review_id),
    )
    row["resolved_at"] = now
    row["resolution"] = resolution
    return row


def resolve_review(
    conn: sqlite3.Connection,
    review_id: str,
    resolution: str,
) -> dict[str, Any]:
    """Mark a review resolved in one transaction (spec §4.5, §4.9).

    Raises ``ReviewNotFoundError``, ``ReviewAlreadyResolvedError``, or ``ValueError`` for a
    resolution outside ``still_holds|revised|retracted|dismissed``. Policy (``dismissed`` only for
    violations) lives in ``tms.review``.
    """
    with conn:
        return resolve_review_within_transaction(conn, review_id, resolution)


def finalize_proposal_approval_within_transaction(
    conn: sqlite3.Connection,
    review_id: str,
    node_id: str,
    resolution: str,
) -> dict[str, Any]:
    """Body of ``finalize_proposal_approval``, without its own transaction (task T7.5).

    Records the minted ``node_id`` onto a create-node proposal review and
    marks it resolved in one write set. Assumes the caller holds an open
    ``with conn:`` (same nesting rule as ``enqueue_review_within_transaction``).
    """
    if resolution not in _VALID_RESOLUTIONS:
        raise ValueError(
            f"invalid resolution {resolution!r}; must be one of {_VALID_RESOLUTIONS}"
        )
    row = get_review(conn, review_id)
    if row["resolved_at"] is not None:
        raise ReviewAlreadyResolvedError(review_id)
    now = _now()
    conn.execute(
        "UPDATE review_queue SET node_id=?, resolved_at=?, resolution=? WHERE id=?",
        (node_id, now, resolution, review_id),
    )
    row["node_id"] = node_id
    row["resolved_at"] = now
    row["resolution"] = resolution
    return row


def finalize_proposal_approval(
    conn: sqlite3.Connection,
    review_id: str,
    node_id: str,
    resolution: str,
) -> dict[str, Any]:
    """Attach minted node_id to a proposal review and resolve it (task T7.5).

    Single transaction: ``UPDATE review_queue SET node_id=?, resolved_at=?,
    resolution=?``. Used by ``tms.review.approve_proposal`` after
    ``create_node`` has already minted (create_node is its own top-level
    transaction; this must not wrap it).
    """
    with conn:
        return finalize_proposal_approval_within_transaction(
            conn, review_id, node_id, resolution
        )


def _mint_unique_token_id(conn: sqlite3.Connection) -> str:
    """Mint an id not already present in ``tokens`` (spec §4.1: retry bound 10, then error).

    Token ids reuse the same id8 scheme as nodes/edges (spec §4.1 fixes one
    id format; this task does not invent a second one) even though a
    bearer token id is never rendered as a vault anchor.
    """
    for _ in range(_MINT_RETRY_BOUND):
        candidate = ids.mint()
        row = conn.execute("SELECT 1 FROM tokens WHERE id=?", (candidate,)).fetchone()
        if row is None:
            return candidate
    raise IdMintError(f"failed to mint a unique token id after {_MINT_RETRY_BOUND} attempts")


def _node_content(body: str, facets: list[Facet], task_state: str | None) -> dict[str, Any]:
    return {
        "body": body,
        "facets": [f.model_dump() for f in facets],
        "task_state": task_state,
    }


def _insert_object(conn: sqlite3.Connection, content: dict[str, Any], now: str) -> str:
    obj_bytes = canonical_json(content)
    obj_hash = object_hash(obj_bytes)
    # objects are content-addressed and append-only (spec §4.4): identical
    # content always hashes identically, so re-inserting an existing hash is
    # a safe no-op, never a mutation of an existing row.
    conn.execute(
        "INSERT OR IGNORE INTO objects (hash, kind, bytes, created_at) VALUES (?, ?, ?, ?)",
        (obj_hash, "node_snapshot", obj_bytes, now),
    )
    return obj_hash


def _insert_commit(
    conn: sqlite3.Connection,
    node_id: str,
    parents: list[str],
    object_hash_: str,
    change_class: str,
    facets_touched: list[str],
    author: str,
    message: str,
    now: str,
) -> str:
    facets_touched = sorted(facets_touched)
    commit_content = {
        "node_id": node_id,
        "parents": parents,
        "object_hash": object_hash_,
        "change_class": change_class,
        "facets_touched": facets_touched,
        "author": author,
        "message": message,
        "ts": now,
    }
    commit_hash = object_hash(canonical_json(commit_content))
    conn.execute(
        "INSERT INTO commits (hash, node_id, parents, object_hash, change_class, "
        "facets_touched, author, message, ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            commit_hash,
            node_id,
            canonical_json(parents).decode("utf-8"),
            object_hash_,
            change_class,
            canonical_json(facets_touched).decode("utf-8"),
            author,
            message,
            now,
        ),
    )
    return commit_hash


def _recompute_maturity(conn: sqlite3.Connection, node_id: str) -> None:
    """Recompute and persist ``nodes.maturity`` (spec §4.6) from the node's type, ``vetted`` flag,
    head facet count and live inbound edges, via the pure ``maturity.derive``.

    The caller must hold the transaction of the mutation that changed an input. No-op if the node
    is gone.
    """
    row = conn.execute(
        "SELECT node_type, head_hash, vetted FROM nodes WHERE id=?", (node_id,)
    ).fetchone()
    if row is None:
        return
    node_type, head_hash, vetted = row
    obj_row = conn.execute("SELECT bytes FROM objects WHERE hash=?", (head_hash,)).fetchone()
    facet_count = len(json.loads(obj_row[0])["facets"])
    edge_rows = conn.execute(
        "SELECT e.edge_type, n.node_type FROM edges e JOIN nodes n ON n.id = e.src "
        "WHERE e.dst=? AND e.retracted_at IS NULL",
        (node_id,),
    ).fetchall()
    inbound = [maturity.InboundEdge(edge_type=r[0], src_node_type=r[1]) for r in edge_rows]
    new_maturity = maturity.derive(node_type, facet_count, bool(vetted), inbound)
    conn.execute("UPDATE nodes SET maturity=? WHERE id=?", (new_maturity, node_id))


def _create_node_tx(
    conn: sqlite3.Connection,
    node_type: str,
    body: str,
    facets: list[Facet] | None,
    task_state: str | None,
    author: str,
    message: str,
    node_id: str | None = None,
) -> Node:
    """``create_node`` without its own transaction (``split_node`` mints several nodes inside one
    outer transaction; see ``enqueue_review_within_transaction``).
    """
    if node_type not in _VALID_NODE_TYPES:
        raise ValueError(f"invalid node_type {node_type!r}; must be one of {_VALID_NODE_TYPES}")
    facets = facets or []
    canonical_body = canonicalize_text(body)
    content = _node_content(canonical_body, facets, task_state)
    now = _now()

    if node_id is None:
        node_id = _mint_unique_id(conn)
    else:
        # M20-G: the sync engine ADOPTS an anchor the hub has never seen under its own id (a
        # restored backup, a second machine's hub, a reset hub) instead of minting a different
        # one, so transclusion links survive. Only a well-formed id no row uses is accepted.
        ids.validate(node_id)
        if conn.execute("SELECT 1 FROM nodes WHERE id=?", (node_id,)).fetchone() is not None:
            raise ValueError(f"node id {node_id!r} already exists")
    obj_hash = _insert_object(conn, content, now)
    conn.execute(
        "INSERT INTO nodes (id, node_type, head_hash, maturity, status, vetted, "
        "created_at, updated_at) VALUES (?, ?, ?, 'S0', 'live', 0, ?, ?)",
        (node_id, node_type, obj_hash, now, now),
    )
    # Keep nodes_fts in sync with the node body (spec §4.4: fts5 vtable
    # over (id UNINDEXED, body)) at creation time.
    conn.execute("INSERT INTO nodes_fts (id, body) VALUES (?, ?)", (node_id, canonical_body))
    _insert_commit(
        conn,
        node_id,
        parents=[],
        object_hash_=obj_hash,
        change_class="major",
        facets_touched=[f.facet_id for f in facets],
        author=author,
        message=message,
        now=now,
    )

    return Node(
        id=node_id,
        node_type=node_type,  # type: ignore[arg-type]  # validated against _VALID_NODE_TYPES above
        body=canonical_body,
        facets=facets,
        task_state=task_state,  # type: ignore[arg-type]  # Node validates open|done|None
        vetted=False,
        status="live",
    )


def create_node(
    conn: sqlite3.Connection,
    node_type: str,
    body: str,
    facets: list[Facet] | None = None,
    task_state: str | None = None,
    author: str = "system",
    message: str = "",
    node_id: str | None = None,
) -> Node:
    """Create a node with a genesis commit (spec §4.5, §4.1) in one transaction and return it.

    Mints an id (retrying on collision, bound 10, then ``IdMintError``) unless ``node_id`` is
    given: that adopts a well-formed, unused id (M20-G; ``ValueError`` if malformed or taken).
    Inserts the snapshot object, the ``nodes`` row (always ``S0``: no inbound edges yet), the
    genesis commit (``change_class="major"``) and the ``nodes_fts`` row.
    """
    with conn:
        return _create_node_tx(
            conn, node_type, body, facets, task_state, author, message, node_id
        )


class _Unset:
    """Sentinel type distinguishing "argument omitted" from ``None`` (task T5.4).

    ``commit_node``'s new ``task_state`` parameter needs three distinct
    meanings: "leave task_state exactly as it was" (omitted -> this
    sentinel), "explicitly clear it" (``None``), and "set it to this value"
    (``"open"``/``"done"``). A plain ``None`` default cannot express the
    first case without also matching the second.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debug aid only
        return "<UNSET>"


_UNSET_TASK_STATE = _Unset()


def _head_commit_hash(conn: sqlite3.Connection, node_id: str) -> str | None:
    """The commit that produced ``nodes.head_hash``, or ``None`` if the node has no commits.

    This is the newest commit whose object is the current head, which differs from the newest
    commit row once a conflict branch exists (branches never move ``head_hash``). Falls back to the
    newest commit (defensive).
    """
    row = conn.execute("SELECT head_hash FROM nodes WHERE id=?", (node_id,)).fetchone()
    if row is None:
        return None
    head_hash = row[0]
    commit_row = conn.execute(
        "SELECT hash FROM commits WHERE node_id=? AND object_hash=? ORDER BY rowid DESC LIMIT 1",
        (node_id, head_hash),
    ).fetchone()
    if commit_row is not None:
        return commit_row[0]
    fallback_row = conn.execute(
        "SELECT hash FROM commits WHERE node_id=? ORDER BY rowid DESC LIMIT 1", (node_id,)
    ).fetchone()
    return fallback_row[0] if fallback_row is not None else None


def record_conflict_branch(
    conn: sqlite3.Connection,
    node_id: str,
    branch_body: str | None,
    *,
    task_state: Literal["open", "done"] | None | _Unset = _UNSET_TASK_STATE,
    author: str = "sync",
    message: str = "",
) -> str:
    """Append the vault's conflicting version as a BRANCH commit (T5.5, spec §4.8) without moving
    the head.

    Both sides of a both-edited conflict stay on the commit DAG. The branch's parent is the current
    head commit (the true last-synced commit is not recoverable without a schema change; see the
    SPEC-QUESTION). Content: ``branch_body`` (or the head's body if ``None``), the head's facets,
    and ``task_state`` via the ``_UNSET_TASK_STATE`` sentinel like ``commit_node``. Idempotent for
    crash replay (T5.6): an existing commit of this node with the same object is returned and
    nothing is inserted. Never touches ``head_hash``, ``updated_at``, ``nodes_fts`` or maturity.
    Raises ``NodeNotFoundError``; returns the branch commit hash.
    """
    now = _now()
    with conn:
        row = conn.execute("SELECT head_hash FROM nodes WHERE id=?", (node_id,)).fetchone()
        if row is None:
            raise NodeNotFoundError(node_id)
        head_hash = row[0]

        current_obj = conn.execute(
            "SELECT bytes FROM objects WHERE hash=?", (head_hash,)
        ).fetchone()
        current_content = json.loads(current_obj[0])

        canonical_body = (
            canonicalize_text(branch_body)
            if branch_body is not None
            else current_content["body"]
        )
        facets = [Facet(**f) for f in current_content["facets"]]
        task_state_value = (
            current_content.get("task_state")
            if isinstance(task_state, _Unset)
            else task_state
        )

        content = _node_content(canonical_body, facets, task_state_value)
        obj_hash = _insert_object(conn, content, now)

        existing_commit = conn.execute(
            "SELECT hash FROM commits WHERE node_id=? AND object_hash=? "
            "ORDER BY rowid DESC LIMIT 1",
            (node_id, obj_hash),
        ).fetchone()
        if existing_commit is not None:
            return existing_commit[0]

        parent_hash = _head_commit_hash(conn, node_id)
        parents = [parent_hash] if parent_hash is not None else []

        commit_hash = _insert_commit(
            conn,
            node_id,
            parents=parents,
            object_hash_=obj_hash,
            change_class="patch",
            facets_touched=[],
            author=author,
            message=message,
            now=now,
        )

    return commit_hash


def commit_node(
    conn: sqlite3.Connection,
    node_id: str,
    new_body: str | None = None,
    facets: list[Facet] | None = None,
    *,
    task_state: Literal["open", "done"] | None | _Unset = _UNSET_TASK_STATE,
    change_class: str,
    facets_touched: list[str],
    author: str,
    message: str = "",
) -> Node:
    """Append a commit to node_id's DAG and move its head (spec §4.5), in one transaction.

    Inserts the new object (``new_body``/``facets`` if given, else the current ones) and a commit
    parented on the current head, moves ``head_hash``/``updated_at``, updates ``nodes_fts`` and
    recomputes maturity (a commit can change the facet count). Never mutates an existing row; the
    previous head stays reachable via ``history``.

    ``task_state`` is sentinel-guarded: omitted keeps the current state (so an ordinary body edit
    can never clobber it), ``None`` clears it, a value sets it (checkbox toggles, spec §4.8).
    """
    if change_class not in _VALID_CHANGE_CLASSES:
        raise ValueError(
            f"invalid change_class {change_class!r}; must be one of {_VALID_CHANGE_CLASSES}"
        )
    now = _now()

    with conn:
        row = conn.execute(
            "SELECT node_type, head_hash, status, vetted FROM nodes WHERE id=?", (node_id,)
        ).fetchone()
        if row is None:
            raise NodeNotFoundError(node_id)
        node_type, head_hash, status, vetted = row

        current_obj = conn.execute(
            "SELECT bytes FROM objects WHERE hash=?", (head_hash,)
        ).fetchone()
        current_content = json.loads(current_obj[0])

        canonical_body = (
            canonicalize_text(new_body) if new_body is not None else current_content["body"]
        )
        new_facets = (
            facets if facets is not None else [Facet(**f) for f in current_content["facets"]]
        )
        task_state_value = (
            current_content.get("task_state")
            if isinstance(task_state, _Unset)
            else task_state
        )

        content = _node_content(canonical_body, new_facets, task_state_value)
        obj_hash = _insert_object(conn, content, now)

        # CRITICAL (task T5.5 companion fix): parent on the commit that
        # produced the CURRENT head, not merely the newest-inserted commit
        # row. Once a conflict-branch commit exists (``record_conflict_branch``,
        # appended to the DAG WITHOUT moving ``head_hash``), the two differ;
        # parenting on the newest row would silently collapse the branch
        # into the mainline on the very next ``commit_node`` call. Bit-identical
        # to the old lookup for every existing caller when no branch commit
        # exists (the newest commit's object_hash always equals head_hash then).
        parents_head = _head_commit_hash(conn, node_id)
        parents = [parents_head] if parents_head is not None else []

        commit_hash = _insert_commit(
            conn,
            node_id,
            parents=parents,
            object_hash_=obj_hash,
            change_class=change_class,
            facets_touched=facets_touched,
            author=author,
            message=message,
            now=now,
        )

        conn.execute(
            "UPDATE nodes SET head_hash=?, updated_at=? WHERE id=?", (obj_hash, now, node_id)
        )
        # Keep nodes_fts in sync with the (possibly new) node body on every
        # commit, not just genesis (spec §4.4).
        conn.execute("UPDATE nodes_fts SET body=? WHERE id=?", (canonical_body, node_id))
        # facets may have changed (facet_count is a maturity input, spec §4.6).
        _recompute_maturity(conn, node_id)

        # SPEC-QUESTION (T7.2): §4.9 triggers invalidation on "any commit with change_class ==
        # 'major'" but does not say who decides ``change_class``. Narrowest reading: it is a plain,
        # already-decided argument, and a literal ``"major"`` runs the invalidation walk with
        # ``facets_touched`` verbatim (a retraction passes every facet id). The import is deferred
        # (``tms.invalidate`` imports this module). The walk runs INSIDE this transaction so its
        # ``facet_break`` reviews are atomic with the commit; that is safe only because it uses
        # ``enqueue_review_within_transaction`` (no nested ``with conn:``).
        if change_class == "major":
            from akasha.tms import invalidate

            invalidate.invalidate(conn, node_id, commit_hash, set(facets_touched))

        # Evaluate the ``all_subtasks_closed`` trigger for the parent supertask(s) after EVERY
        # commit (spec §4.10a; closing a subtask is an ordinary "patch", so this is not gated on
        # ``major``). Only that condition is evaluated here: ``facet_interface_changed`` is the
        # ``invalidate`` call above, ``evidence_retracted`` is the delete path, and
        # ``recheck_after`` has no schedule. ``triggers.evaluate()`` and its action are NOT called:
        # they use the standalone ``enqueue_review``, whose nested ``with conn:`` would commit this
        # still-open transaction. Instead this reuses the pure ``triggers.CONDITIONS`` entry and
        # ``TriggerContext`` and enqueues via ``enqueue_review_within_transaction``, with the same
        # idempotence gate (an open ``subtasks_closed`` review for the node) so re-committing never
        # duplicates. Flagging a human is the only action; ``task_state`` is never written (spec
        # §4.10, §9 story 8). Deferred import: cycle.
        from akasha.tms import triggers

        for parent_edge in find_live_edges(conn, dst=node_id, edge_type="composes"):
            try:
                parent_node = get_node(conn, parent_edge.src)
            except NodeNotFoundError:  # pragma: no cover - defensive, dangling edge
                continue
            if parent_node.task_state is None:
                continue  # not a supertask (composes is also used for non-task hierarchy)
            task_children: list[Node] = []
            for child_edge in find_live_edges(conn, src=parent_node.id, edge_type="composes"):
                try:
                    task_children.append(get_node(conn, child_edge.dst))
                except NodeNotFoundError:  # pragma: no cover - defensive, dangling edge
                    continue
            trigger_ctx = triggers.TriggerContext(
                now=now, commit=commit_hash, children=tuple(task_children)
            )
            if triggers.CONDITIONS["all_subtasks_closed"](parent_node, trigger_ctx):
                if not find_open_reviews(
                    conn, node_id=parent_node.id, cause_kind="subtasks_closed"
                ):
                    enqueue_review_within_transaction(
                        conn, parent_node.id, "subtasks_closed", cause_ref=commit_hash
                    )

    return Node(
        id=node_id,
        node_type=node_type,
        body=canonical_body,
        facets=new_facets,
        task_state=task_state_value,
        vetted=bool(vetted),
        status=status,
    )


def get_node(conn: sqlite3.Connection, node_id: str, as_of: str | None = None) -> Node:
    """Return node_id's content at HEAD, or as it stood at an ISO-8601 instant (spec §4.5).

    Invariant: read-only, never mutates ``objects``/``commits``/``nodes``.
    With ``as_of=None`` returns the current head object. With ``as_of`` set,
    resolves to the object of the most recent commit with ``ts <= as_of``
    (the commit "live" at that instant); raises ``NodeNotFoundError`` if no
    such commit exists (node id unknown, or as_of predates genesis).
    """
    row = conn.execute(
        "SELECT node_type, head_hash, status, vetted FROM nodes WHERE id=?", (node_id,)
    ).fetchone()
    if row is None:
        raise NodeNotFoundError(node_id)
    node_type, head_hash, status, vetted = row

    if as_of is None:
        obj_hash = head_hash
    else:
        commit_row = conn.execute(
            "SELECT object_hash FROM commits WHERE node_id=? AND ts<=? "
            "ORDER BY ts DESC, rowid DESC LIMIT 1",
            (node_id, as_of),
        ).fetchone()
        if commit_row is None:
            raise NodeNotFoundError(f"{node_id} has no commit at or before {as_of!r}")
        obj_hash = commit_row[0]

    obj_row = conn.execute("SELECT bytes FROM objects WHERE hash=?", (obj_hash,)).fetchone()
    content = json.loads(obj_row[0])
    facets = [Facet(**f) for f in content["facets"]]

    return Node(
        id=node_id,
        node_type=node_type,
        body=content["body"],
        facets=facets,
        task_state=content.get("task_state"),
        vetted=bool(vetted),
        status=status,
    )


# SQLite's default compiled-in host-parameter limit (SQLITE_MAX_VARIABLE_NUMBER).
_SQLITE_MAX_VARS = 500


def get_nodes_bulk(conn: sqlite3.Connection, node_ids: Sequence[str]) -> dict[str, Node]:
    """Batch-fetch nodes at HEAD (read-only): ``get_node`` semantics in O(n/500) round trips
    instead of 2n (the N+1 pattern found profiling E20's 5,000-block case). Unknown ids are
    omitted, never raised.
    """
    if not node_ids:
        return {}
    unique_ids = list(dict.fromkeys(node_ids))  # de-dup, preserve first-seen order

    node_rows: dict[
        str, tuple[NodeType, str, Literal["live", "retracted", "tombstone"], int]
    ] = {}
    for i in range(0, len(unique_ids), _SQLITE_MAX_VARS):
        chunk = unique_ids[i : i + _SQLITE_MAX_VARS]
        placeholders = ",".join("?" * len(chunk))
        query = (
            "SELECT id, node_type, head_hash, status, vetted "
            f"FROM nodes WHERE id IN ({placeholders})"
        )
        for row in conn.execute(query, chunk):
            node_rows[row[0]] = (row[1], row[2], row[3], row[4])

    head_hashes = list({v[1] for v in node_rows.values()})
    object_bytes: dict[str, str] = {}
    for i in range(0, len(head_hashes), _SQLITE_MAX_VARS):
        chunk = head_hashes[i : i + _SQLITE_MAX_VARS]
        placeholders = ",".join("?" * len(chunk))
        for obj_hash, obj_bytes in conn.execute(
            f"SELECT hash, bytes FROM objects WHERE hash IN ({placeholders})", chunk
        ):
            object_bytes[obj_hash] = obj_bytes

    result: dict[str, Node] = {}
    for node_id, (node_type, head_hash, status, vetted) in node_rows.items():
        content = json.loads(object_bytes[head_hash])
        facets = [Facet(**f) for f in content["facets"]]
        result[node_id] = Node(
            id=node_id,
            node_type=node_type,
            body=content["body"],
            facets=facets,
            task_state=content.get("task_state"),
            vetted=bool(vetted),
            status=status,
        )
    return result


def get_projection_bulk(
    conn: sqlite3.Connection, node_ids: Sequence[str]
) -> dict[str, tuple[str, str, str | None]]:
    """``{id: (status, body, task_state)}`` at HEAD for the ids that exist (read-only).

    What a file projection needs and nothing more: one JOIN per 500 ids with the body decoded
    inside SQLite, instead of ``get_nodes_bulk``'s two round trips plus a JSON decode and a
    pydantic ``Node`` per id (about 4x faster on a 5,000-block file). Unknown ids are omitted.
    """
    out: dict[str, tuple[str, str, str | None]] = {}
    unique = list(dict.fromkeys(node_ids))
    for i in range(0, len(unique), _SQLITE_MAX_VARS):
        chunk = unique[i : i + _SQLITE_MAX_VARS]
        marks = ",".join("?" * len(chunk))
        rows = conn.execute(
            "SELECT n.id, n.status, json_extract(CAST(o.bytes AS TEXT), '$.body'), "
            "json_extract(CAST(o.bytes AS TEXT), '$.task_state') "
            f"FROM nodes n JOIN objects o ON o.hash = n.head_hash WHERE n.id IN ({marks})",
            chunk,
        )
        for node_id, status, body, task_state in rows:
            out[node_id] = (status, body, task_state)
    return out


def history(conn: sqlite3.Connection, node_id: str) -> list[dict[str, Any]]:
    """Return node_id's commits oldest-first (genesis at index 0) (spec §4.5).

    Invariant: read-only; reflects every ``commits`` row for node_id (the
    full append-only DAG, no rewrite/squash), ordered by insertion order
    (``rowid``), which is monotonic with commit creation since ``commits``
    rows are never updated or deleted.
    """
    rows = conn.execute(
        "SELECT hash, parents, object_hash, change_class, facets_touched, author, message, ts "
        "FROM commits WHERE node_id=? ORDER BY rowid ASC",
        (node_id,),
    ).fetchall()
    return [
        {
            "hash": r[0],
            "parents": json.loads(r[1]),
            "object_hash": r[2],
            "change_class": r[3],
            "facets_touched": json.loads(r[4]),
            "author": r[5],
            "message": r[6],
            "ts": r[7],
        }
        for r in rows
    ]


def get_commit_snapshot(conn: sqlite3.Connection, commit_hash: str) -> dict[str, Any]:
    """Read-only: decode one commit's ``{body, facets, task_state}``.

    Reads a conflict-branch commit's content without treating it as the node's current state (which
    ``get_node`` cannot express). Raises ``NodeNotFoundError`` for an unknown commit hash.
    """
    row = conn.execute("SELECT object_hash FROM commits WHERE hash=?", (commit_hash,)).fetchone()
    if row is None:
        raise NodeNotFoundError(commit_hash)
    obj_row = conn.execute("SELECT bytes FROM objects WHERE hash=?", (row[0],)).fetchone()
    content = json.loads(obj_row[0])
    return {
        "body": content["body"],
        "facets": [Facet(**f) for f in content["facets"]],
        "task_state": content.get("task_state"),
    }


def node_versions(conn: sqlite3.Connection, node_id: str) -> list[dict[str, Any]]:
    """Read-only: every version a node has had, oldest first (M20-D, the mirror join rule).

    One row per commit (conflict-branch commits included -- a text the hub has already seen and
    set aside is not "new"): ``{"hash", "ts", "body", "task_state", "is_head"}``. ``ts`` is the
    commit's ISO 8601 instant, ``is_head`` is true for the commit(s) whose content is the node's
    current head. Raises ``NodeNotFoundError`` for an unknown node.
    """
    head = conn.execute("SELECT head_hash FROM nodes WHERE id=?", (node_id,)).fetchone()
    if head is None:
        raise NodeNotFoundError(node_id)
    rows = conn.execute(
        "SELECT c.hash, c.ts, c.object_hash, o.bytes FROM commits c "
        "JOIN objects o ON o.hash = c.object_hash WHERE c.node_id=? ORDER BY c.rowid ASC",
        (node_id,),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for commit_hash, ts, object_hash_, blob in rows:
        content = json.loads(blob)
        out.append(
            {
                "hash": commit_hash,
                "ts": ts,
                "body": content["body"],
                "task_state": content.get("task_state"),
                "is_head": object_hash_ == head[0],
            }
        )
    return out


def get_maturity(conn: sqlite3.Connection, node_id: str) -> str:
    """Return node_id's persisted maturity stage (read-only, spec §4.6).

    Maturity is derived state on the ``nodes`` row, refreshed by ``_recompute_maturity``; ``Node``
    does not carry it. Raises ``NodeNotFoundError``.
    """
    row = conn.execute("SELECT maturity FROM nodes WHERE id=?", (node_id,)).fetchone()
    if row is None:
        raise NodeNotFoundError(node_id)
    return row[0]


def vet_node(conn: sqlite3.Connection, node_id: str) -> Node:
    """Mark node_id vetted (S4) and recompute its maturity in the same transaction (spec §4.6,
    §4.11).

    Human-only at the API. Idempotent. Raises ``NodeNotFoundError``.
    """
    now = _now()
    with conn:
        row = conn.execute("SELECT 1 FROM nodes WHERE id=?", (node_id,)).fetchone()
        if row is None:
            raise NodeNotFoundError(node_id)
        conn.execute("UPDATE nodes SET vetted=1, updated_at=? WHERE id=?", (now, node_id))
        _recompute_maturity(conn, node_id)
    return get_node(conn, node_id)


def _edge_row_to_model(row: tuple[Any, ...]) -> Edge:
    (
        edge_id,
        src,
        dst,
        edge_type,
        facet_binding,
        provenance,
        mode,
        pinned_commit,
    ) = row
    return Edge(
        id=edge_id,
        src=src,
        dst=dst,
        edge_type=edge_type,
        facet_binding=facet_binding,
        provenance=provenance,
        mode=mode,
        pinned_commit=pinned_commit,
    )


def mint_facet_from_span(
    conn: sqlite3.Connection,
    node_id: str,
    span: str,
    *,
    author: str,
    message: str = "",
) -> Facet:
    """Mint a new facet on node_id from a highlighted span (T7.7, spec §4.2) and return it.

    The facet id is a fresh id8 (facets live only inside the node's object blob, so there is no
    table to check collisions against). It is appended at ``version=1`` and committed via
    ``commit_node`` with ``change_class="minor"`` and ``facets_touched=[new_id]``: a brand-new v1
    facet is neither removed, renamed nor bumped, so ``major`` would spuriously flag every live
    inbound justification edge (§4.9).

    # SPEC-QUESTION (T7.7): ``Facet.name`` must be unique per node but a span supplies no name.
    # Narrowest, collision-free reading: ``name = facet_id``. See docs/spec-questions.md T7.7.

    Not atomic with a following ``create_edge``: if that fails the facet stays as a harmless
    unbound extra (§4.5 has no cross-node atomic primitive). Raises ``NodeNotFoundError``.
    """
    node = get_node(conn, node_id)
    facet_id = ids.mint()
    new_facet = Facet(facet_id=facet_id, name=facet_id, span=span, version=1)
    commit_node(
        conn,
        node_id,
        facets=[*node.facets, new_facet],
        change_class="minor",
        facets_touched=[facet_id],
        author=author,
        message=message,
    )
    return new_facet


def create_edge(
    conn: sqlite3.Connection,
    src: str,
    dst: str,
    edge_type: EdgeType,
    facet_binding: str | None,
    provenance: str,
    mode: str = "track",
    pinned_commit: str | None = None,
) -> Edge:
    """Create a live edge src -> dst (spec §4.5, §4.2) in one transaction.

    The ``Edge`` model validates ``facet_binding`` (justification types need a facet id or ``"*"``;
    ``None`` only for composes/redirects_to): ``pydantic.ValidationError``, nothing written,
    otherwise. Mints an edge id (bound 10 retries, then ``IdMintError``) and recomputes ``dst``'s
    maturity.
    """
    now = _now()
    with conn:
        edge_id = _mint_unique_edge_id(conn)
        # Constructing Edge runs its model_validator, which is the single
        # source of truth for the facet_binding rule (spec §4.2); this
        # raises before any row is written if the rule is violated.
        edge = Edge(
            id=edge_id,
            src=src,
            dst=dst,
            edge_type=edge_type,
            facet_binding=facet_binding,
            provenance=provenance,  # type: ignore[arg-type]  # validated by pydantic below
            mode=mode,  # type: ignore[arg-type]  # validated by pydantic below
            pinned_commit=pinned_commit,
        )
        conn.execute(
            "INSERT INTO edges (id, src, dst, edge_type, facet_binding, provenance, mode, "
            "pinned_commit, created_at, retracted_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
            (
                edge.id,
                edge.src,
                edge.dst,
                edge.edge_type,
                edge.facet_binding,
                edge.provenance,
                edge.mode,
                edge.pinned_commit,
                now,
            ),
        )
        _recompute_maturity(conn, edge.dst)
    return edge


def retract_edge(conn: sqlite3.Connection, edge_id: str) -> None:
    """Retract a live edge by setting ``retracted_at`` (spec §4.5); the row is never deleted
    (§4.4).

    ``neighborhood`` and ``find_live_edges`` skip it. Re-retracting just re-stamps. Recomputes
    ``dst``'s maturity in the same transaction. Raises ``EdgeNotFoundError``.
    """
    now = _now()
    with conn:
        row = conn.execute("SELECT dst FROM edges WHERE id=?", (edge_id,)).fetchone()
        if row is None:
            raise EdgeNotFoundError(edge_id)
        dst = row[0]
        conn.execute("UPDATE edges SET retracted_at=? WHERE id=?", (now, edge_id))
        _recompute_maturity(conn, dst)


def find_live_edges(
    conn: sqlite3.Connection,
    *,
    src: str | None = None,
    dst: str | None = None,
    edge_type: str | None = None,
) -> list[Edge]:
    """Read-only: every live edge (``retracted_at IS NULL``) matching the optional ANDed filters
    (``src``, ``dst``, ``edge_type``); no filter returns all live edges.
    """
    clauses = ["retracted_at IS NULL"]
    params: list[Any] = []
    if src is not None:
        clauses.append("src=?")
        params.append(src)
    if dst is not None:
        clauses.append("dst=?")
        params.append(dst)
    if edge_type is not None:
        clauses.append("edge_type=?")
        params.append(edge_type)
    where = " AND ".join(clauses)
    rows = conn.execute(
        "SELECT id, src, dst, edge_type, facet_binding, provenance, mode, pinned_commit "
        f"FROM edges WHERE {where}",
        params,
    ).fetchall()
    return [_edge_row_to_model(row) for row in rows]


def neighborhood(conn: sqlite3.Connection, node_id: str, hops: int = 1) -> dict[str, Any]:
    """Live subgraph within ``hops`` steps of node_id, both edge directions (spec §4.5).

    Read-only breadth-first expansion over live edges only. Returns ``{"node_ids": [...], "edges":
    [...]}``: node_id itself plus every node reached, and each distinct live ``Edge`` seen.
    """
    visited_nodes: set[str] = {node_id}
    frontier: set[str] = {node_id}
    edges_by_id: dict[str, Edge] = {}

    for _ in range(max(hops, 0)):
        next_frontier: set[str] = set()
        for current in frontier:
            rows = conn.execute(
                "SELECT id, src, dst, edge_type, facet_binding, provenance, mode, "
                "pinned_commit FROM edges WHERE src=? AND retracted_at IS NULL",
                (current,),
            ).fetchall()
            rows += conn.execute(
                "SELECT id, src, dst, edge_type, facet_binding, provenance, mode, "
                "pinned_commit FROM edges WHERE dst=? AND retracted_at IS NULL",
                (current,),
            ).fetchall()
            for row in rows:
                edge = _edge_row_to_model(row)
                edges_by_id[edge.id] = edge
                other = edge.dst if edge.src == current else edge.src
                if other not in visited_nodes:
                    next_frontier.add(other)
        visited_nodes |= next_frontier
        frontier = next_frontier
        if not frontier:
            break

    return {
        "node_ids": sorted(visited_nodes),
        "edges": [edges_by_id[k] for k in sorted(edges_by_id)],
    }


_FTS5_TERM_RE = re.compile(r"[A-Za-z0-9]+")


def _fts5_safe_match_query(q: str) -> str | None:
    """Tokenize ``q`` to alphanumeric terms and rebuild a syntax-safe FTS5 MATCH string.

    A raw query reaches FTS5's expression parser, which 500s on everyday input (a bare ``-``,
    ``"``, ``AND``/``OR``/``NOT``/``NEAR``, ``:``, parentheses; T9.7 reproduced it with
    "already-tracked"). Alphanumeric terms are double-quoted (no escaping needed) and space-joined
    (implicit AND). Returns ``None`` when there are no terms, since an empty MATCH is itself a
    syntax error.
    """
    terms = _FTS5_TERM_RE.findall(q)
    if not terms:
        return None
    return " ".join(f'"{term}"' for term in terms)


def search(conn: sqlite3.Connection, q: str) -> list[Node]:
    """Full-text search over node bodies via ``nodes_fts`` (spec §4.5, §4.4), best match first.

    Read-only; ranked by FTS5 ``rank``; returns each match's head content. ``q`` goes through
    ``_fts5_safe_match_query``; a query with no ASCII alphanumeric terms returns ``[]`` (a known
    limitation shared with ``find_contradiction_candidates``).
    """
    match_query = _fts5_safe_match_query(q)
    if match_query is None:
        return []
    rows = conn.execute(
        "SELECT id FROM nodes_fts WHERE nodes_fts MATCH ? ORDER BY rank", (match_query,)
    ).fetchall()
    return [get_node(conn, row[0]) for row in rows]


def find_contradiction_candidates(
    conn: sqlite3.Connection,
    node_id: str,
    body: str,
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Read-only: exact/near-duplicate LIVE claim candidates for a just-created claim (spec §4.11,
    PRD §8 story 2). A non-LLM FTS5 heuristic over the existing ``nodes_fts`` index: no new table,
    embedding or model (PRD §5).

    ``body`` is canonicalized and tokenized to alphanumeric terms, FTS5-quoted and OR-joined (no
    terms: ``[]`` without a MATCH, which would raise on raw punctuation/operators). Matches are
    live claims except ``node_id`` itself, ranked by bm25 with a byte-equal canonical body always
    first (a spec guarantee bm25 alone does not give), capped at ``limit`` (default 5). Each result
    is ``{node_id, body, created_at, evidence}`` where ``evidence`` lists the ``{node_id, body}``
    of the candidate's live ``cites`` destinations.

    # SPEC-QUESTION: the spec says "Evidence-type dst nodes" without defining that against the
    # NodeType enum. Narrowest reading: ``node_type == "evidence"`` only (not "proof"). See
    # docs/spec-questions.md T10.2b.
    """
    canonical_body = canonicalize_text(body)
    terms = _FTS5_TERM_RE.findall(canonical_body)
    if not terms:
        return []
    match_query = " OR ".join(f'"{term}"' for term in terms)
    rows = conn.execute(
        "SELECT nodes_fts.id, nodes_fts.body, nodes.created_at, bm25(nodes_fts) AS score "
        "FROM nodes_fts JOIN nodes ON nodes.id = nodes_fts.id "
        "WHERE nodes_fts MATCH ? AND nodes.node_type='claim' AND nodes.status='live' "
        "AND nodes_fts.id != ?",
        (match_query, node_id),
    ).fetchall()

    def _sort_key(row: Any) -> tuple[int, float]:
        cand_body, score = row[1], row[3]
        is_exact = 0 if cand_body == canonical_body else 1
        return (is_exact, score)

    ranked = sorted(rows, key=_sort_key)[:limit]

    candidates: list[dict[str, Any]] = []
    for cand_id, cand_body, created_at, _score in ranked:
        evidence: list[dict[str, Any]] = []
        for edge in find_live_edges(conn, src=cand_id, edge_type="cites"):
            dst_node = get_node(conn, edge.dst)
            if dst_node.node_type == "evidence":
                evidence.append({"node_id": dst_node.id, "body": dst_node.body})
        candidates.append(
            {
                "node_id": cand_id,
                "body": cand_body,
                "created_at": created_at,
                "evidence": evidence,
            }
        )
    return candidates


def append_audit(
    conn: sqlite3.Connection,
    token_id: str | None,
    action: str,
    detail: str | None = None,
) -> None:
    """Append one row to ``audit_log`` (spec §4.4, §4.11); nothing else ever updates or deletes it.

    ``ts`` uses ``_now()`` so audit times sort with commit/edge times. ``token_id`` may be NULL.
    The caller owns keeping ``detail`` free of secrets (this layer only ever sees a token id).
    """
    with conn:
        conn.execute(
            "INSERT INTO audit_log (ts, token_id, action, detail) VALUES (?, ?, ?, ?)",
            (_now(), token_id, action, detail),
        )


def _reassign_inbound_edges(conn: sqlite3.Connection, old_dst: str, new_dst: str) -> None:
    """Point every *live* edge targeting ``old_dst`` at ``new_dst`` instead.

    Invariant: only touches edges with ``retracted_at IS NULL`` (a
    retracted edge is dead history, not a dangling reference — it is left
    alone). Does not recompute anyone's maturity; callers do that
    afterward for whichever node(s) gained/lost inbound edges.
    """
    conn.execute("UPDATE edges SET dst=? WHERE dst=? AND retracted_at IS NULL", (new_dst, old_dst))


def delete_node(
    conn: sqlite3.Connection,
    node_id: str,
    redirect_to: list[str] | None = None,
    tombstone: bool = False,
) -> None:
    """Delete node_id: hard-delete if S0, tombstone (+redirect) if S1+ (spec §4.5, §4.6), one
    transaction.

    Maturity is recomputed first. S0: removes the node's commits, node row, every edge touching it
    and its FTS row (objects are left for ``gc_objects``). S1+: needs a non-empty ``redirect_to``
    or ``tombstone=True`` else ``NeedsRedirectError`` (``E_NEEDS_REDIRECT``; nothing is written);
    sets ``status='tombstone'`` and, with ``redirect_to``, inserts a ``redirects`` row and
    reassigns every live inbound edge to the first successor ("zero dangling references"),
    recomputing its maturity. Raises ``NodeNotFoundError``.
    """
    now = _now()
    with conn:
        row = conn.execute("SELECT 1 FROM nodes WHERE id=?", (node_id,)).fetchone()
        if row is None:
            raise NodeNotFoundError(node_id)

        # Spec §4.9: "node retraction is always major touching all facets."
        # Capture every live facet id BEFORE the tombstone UPDATE so the
        # invalidate() walk below can flag bound (and '*'-bound) subscribers.
        # Empty set is fine — only '*'-bound subscribers fire in that case.
        touched_facets = {f.facet_id for f in get_node(conn, node_id).facets}

        _recompute_maturity(conn, node_id)
        current_maturity = conn.execute(
            "SELECT maturity FROM nodes WHERE id=?", (node_id,)
        ).fetchone()[0]

        if current_maturity == "S0":
            conn.execute("DELETE FROM commits WHERE node_id=?", (node_id,))
            conn.execute("DELETE FROM nodes WHERE id=?", (node_id,))
            conn.execute("DELETE FROM edges WHERE src=? OR dst=?", (node_id, node_id))
            conn.execute("DELETE FROM nodes_fts WHERE id=?", (node_id,))
            return

        if not redirect_to and not tombstone:
            raise NeedsRedirectError(node_id)

        conn.execute("UPDATE nodes SET status='tombstone', updated_at=? WHERE id=?", (now, node_id))
        # Must run BEFORE _reassign_inbound_edges (inside redirect_to below):
        # that repoints inbound edges off node_id, which would leave
        # invalidate() with no dst==node_id subscribers to flag.
        head_hash = conn.execute(
            "SELECT head_hash FROM nodes WHERE id=?", (node_id,)
        ).fetchone()[0]
        from akasha.tms import invalidate

        invalidate.invalidate(conn, node_id, head_hash, touched_facets)
        if redirect_to:
            conn.execute(
                "INSERT INTO redirects (old_id, successors, created_at) VALUES (?, ?, ?)",
                (node_id, canonical_json(list(redirect_to)).decode("utf-8"), now),
            )
            successor = redirect_to[0]
            _reassign_inbound_edges(conn, node_id, successor)
            _recompute_maturity(conn, successor)


def split_node(
    conn: sqlite3.Connection, node_id: str, parts: list[dict[str, Any]]
) -> dict[str, list[str]]:
    """Split node_id into one new node per entry of ``parts`` (spec §4.5), one transaction.

    Each part is ``create_node``-style kwargs (``node_type``, ``body``, optional ``facets``,
    ``task_state``, ``author``, ``message``). Inserts one ``redirects`` row, tombstones node_id and
    reassigns its live inbound edges to the first successor, recomputing its maturity. Returns
    ``{node_id: [successor_ids...]}``. Raises ``NodeNotFoundError`` or ``ValueError`` (empty
    ``parts``).

    # SPEC-QUESTION (T1.6): §4.5 lists ``split_node(id, parts)`` without the shape of ``parts``.
    # Narrowest reading: a list of ``create_node``-style kwargs dicts. See docs/spec-questions.md
    # T1.6.
    """
    if not parts:
        raise ValueError("split_node requires a non-empty parts list")
    now = _now()
    with conn:
        row = conn.execute("SELECT 1 FROM nodes WHERE id=?", (node_id,)).fetchone()
        if row is None:
            raise NodeNotFoundError(node_id)

        # Capture live inbound edges BEFORE eager reassignment so we can
        # enqueue one reassignment review per edge (task T7.6). The auto-
        # reassign-to-first-successor below is kept as the safe default —
        # an inbound edge must never be left pointing at a tombstone.
        inbound_before = conn.execute(
            "SELECT id, src FROM edges WHERE dst=? AND retracted_at IS NULL",
            (node_id,),
        ).fetchall()

        successor_ids: list[str] = []
        for part in parts:
            new_node = _create_node_tx(
                conn,
                node_type=part["node_type"],
                body=part["body"],
                facets=part.get("facets"),
                task_state=part.get("task_state"),
                author=part.get("author", "system"),
                message=part.get("message", ""),
            )
            successor_ids.append(new_node.id)

        conn.execute(
            "INSERT INTO redirects (old_id, successors, created_at) VALUES (?, ?, ?)",
            (node_id, canonical_json(successor_ids).decode("utf-8"), now),
        )
        conn.execute("UPDATE nodes SET status='tombstone', updated_at=? WHERE id=?", (now, node_id))
        first_successor = successor_ids[0]
        _reassign_inbound_edges(conn, node_id, first_successor)
        _recompute_maturity(conn, first_successor)

        # Additive reassignment queue on top of the eager default (T7.6).
        for edge_id, edge_src in inbound_before:
            cause_ref = canonical_json(
                {
                    "kind": "reassignment",
                    "edge_id": edge_id,
                    "old_id": node_id,
                    "successors": successor_ids,
                }
            ).decode("utf-8")
            # SPEC-QUESTION (T7.6): the closed ``review_queue.cause_kind`` enum has no member for
            # "an inbound edge needs human reassignment after a split", and every existing member
            # is already an idempotence filter or resolution selector elsewhere. Narrowest reading:
            # a new flagged value ``reassignment``, pending a spec amendment.
            enqueue_review_within_transaction(
                conn, edge_src, "reassignment", cause_ref=cause_ref
            )

    return {node_id: successor_ids}


def gc_objects(conn: sqlite3.Connection) -> list[str]:
    """Delete every ``objects`` row no live reference reaches (spec §4.4, §4.5); return the hashes.

    Invariant: GC never removes a referenced object. Reachable = every ``commits.object_hash`` (S0
    and S1+ history alike) + every ``nodes.head_hash`` + every non-NULL ``sync_files.base_hash``.
    What is left is an orphan of an S0 hard-delete, which leaves its objects for this job. One
    transaction.

    # SPEC-QUESTION (T1.7): §4.5 says "unreachable from any S1+ node or base snapshot", which
    # read literally could collect an object a live S0 node still references and break
    # ``get_node``, contradicting "GC never removes a referenced object". Narrowest reading
    # satisfying both: reachable means referenced by ANY existing commit/head or base snapshot.
    # See docs/spec-questions.md T1.7.
    """
    with conn:
        commit_hashes = {
            r[0] for r in conn.execute("SELECT DISTINCT object_hash FROM commits").fetchall()
        }
        head_hashes = {r[0] for r in conn.execute("SELECT head_hash FROM nodes").fetchall()}
        base_hashes = {
            r[0]
            for r in conn.execute(
                "SELECT base_hash FROM sync_files WHERE base_hash IS NOT NULL"
            ).fetchall()
        }
        reachable = commit_hashes | head_hashes | base_hashes

        all_hashes = {r[0] for r in conn.execute("SELECT hash FROM objects").fetchall()}
        orphaned = sorted(all_hashes - reachable)

        if orphaned:
            placeholders = ",".join("?" for _ in orphaned)
            conn.execute(f"DELETE FROM objects WHERE hash IN ({placeholders})", orphaned)

    return orphaned


def merge_nodes(conn: sqlite3.Connection, ids: list[str]) -> dict[str, list[str]]:
    """Merge nodes into the first id of ``ids`` (spec §4.5), one transaction; return ``{old_id:
    [survivor]}``.

    Each other node gets a ``redirects`` row, is tombstoned, and has its live inbound edges
    reassigned to the survivor; the survivor's maturity is recomputed once. No reassignment review
    is queued (unlike split there is one unambiguous survivor). Raises ``ValueError`` (fewer than 2
    ids) or ``NodeNotFoundError`` (checked before any write).

    # SPEC-QUESTION (T1.6): §4.5 lists ``merge_nodes(ids)`` without survivor selection. Narrowest
    # reading: the first id wins. See docs/spec-questions.md T1.6.
    """
    if len(ids) < 2:
        raise ValueError("merge_nodes requires at least two node ids")
    now = _now()
    with conn:
        for nid in ids:
            row = conn.execute("SELECT 1 FROM nodes WHERE id=?", (nid,)).fetchone()
            if row is None:
                raise NodeNotFoundError(nid)

        survivor = ids[0]
        redirect_map: dict[str, list[str]] = {}
        # Deliberately enqueues no reassignment review -- unlike split, merge
        # has a single unambiguous survivor, so no inbound edge needs human
        # reassignment (narrowest reading of task T7.6).
        for old_id in ids[1:]:
            conn.execute(
                "INSERT INTO redirects (old_id, successors, created_at) VALUES (?, ?, ?)",
                (old_id, canonical_json([survivor]).decode("utf-8"), now),
            )
            conn.execute(
                "UPDATE nodes SET status='tombstone', updated_at=? WHERE id=?", (now, old_id)
            )
            _reassign_inbound_edges(conn, old_id, survivor)
            redirect_map[old_id] = [survivor]

        _recompute_maturity(conn, survivor)

    return redirect_map


def resolve_redirect_chain(conn: sqlite3.Connection, node_id: str) -> str:
    """Follow ``redirects`` to the current live terminal id (spec §4.5, §4.11), read-only.

    Advances to ``successors[0]`` per hop (a successor may itself have been split or merged later)
    and stops on a cycle via a seen set.
    """
    current = node_id
    seen: set[str] = {current}
    while True:
        row = conn.execute(
            "SELECT successors FROM redirects WHERE old_id=?", (current,)
        ).fetchone()
        if row is None:
            return current
        successors = json.loads(row[0])
        nxt = successors[0]
        if nxt in seen:
            return current
        seen.add(nxt)
        current = nxt


def reassign_edge(conn: sqlite3.Connection, edge_id: str, new_dst: str) -> None:
    """Re-point one live edge's ``dst`` to ``new_dst`` (spec §4.5; T7.6): the write path for
    ``tms.review.resolve_reassignment``. Recomputes maturity of both destinations, in its own
    transaction. Raises ``EdgeNotFoundError`` if the edge is missing or retracted.
    """
    with conn:
        row = conn.execute(
            "SELECT dst FROM edges WHERE id=? AND retracted_at IS NULL", (edge_id,)
        ).fetchone()
        if row is None:
            raise EdgeNotFoundError(edge_id)
        old_dst = row[0]
        conn.execute("UPDATE edges SET dst=? WHERE id=?", (new_dst, edge_id))
        _recompute_maturity(conn, old_dst)
        _recompute_maturity(conn, new_dst)


# --- Tokens (spec §4.4 ``tokens`` DDL, §4.11 ``/tokens``) --- ``api/auth.py`` only reads
# ``tokens``; creation and revocation live here (rule 0.4). No function here returns
# ``secret_hash``: the route hashes the secret first, and it is never re-exposed.


def create_token(
    conn: sqlite3.Connection,
    name: str,
    token_class: str,
    secret_hash: str,
    rate_per_min: int | None = None,
) -> dict[str, Any]:
    """Create a ``tokens`` row and return its public fields (spec §4.4, §4.11).

    ``token_class`` must be ``"human"`` or ``"agent"`` (``ValueError`` otherwise, nothing written).
    Mints an id8 (bound 10 retries). Returns ``{id, name, class, rate_per_min, created_at,
    revoked_at}``, deliberately without ``secret_hash``.
    """
    if token_class not in ("human", "agent"):
        raise ValueError(f"invalid token class {token_class!r}; must be 'human' or 'agent'")
    now = _now()
    with conn:
        token_id = _mint_unique_token_id(conn)
        conn.execute(
            "INSERT INTO tokens (id, name, class, secret_hash, rate_per_min, created_at, "
            "revoked_at) VALUES (?, ?, ?, ?, ?, ?, NULL)",
            (token_id, name, token_class, secret_hash, rate_per_min, now),
        )
    return {
        "id": token_id,
        "name": name,
        "class": token_class,
        "rate_per_min": rate_per_min,
        "created_at": now,
        "revoked_at": None,
    }


def revoke_token(conn: sqlite3.Connection, token_id: str) -> None:
    """Set ``revoked_at`` on token_id (spec §4.4, §4.11); the row stays so ``audit_log.token_id``
    still resolves. Re-revoking re-stamps. Raises ``TokenNotFoundError``.
    """
    now = _now()
    with conn:
        row = conn.execute("SELECT 1 FROM tokens WHERE id=?", (token_id,)).fetchone()
        if row is None:
            raise TokenNotFoundError(token_id)
        conn.execute("UPDATE tokens SET revoked_at=? WHERE id=?", (now, token_id))


def list_tokens(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return every token's public fields, oldest first (spec §4.11 ``GET /tokens``).

    Invariant: read-only; never selects or returns ``secret_hash`` (a raw or
    hashed secret must never be re-exposed once minted, per the module
    docstring above and this task's constraint).
    """
    rows = conn.execute(
        "SELECT id, name, class, rate_per_min, created_at, revoked_at "
        "FROM tokens ORDER BY created_at ASC"
    ).fetchall()
    return [
        {
            "id": r[0],
            "name": r[1],
            "class": r[2],
            "rate_per_min": r[3],
            "created_at": r[4],
            "revoked_at": r[5],
        }
        for r in rows
    ]


def _mint_unique_sync_root_id(conn: sqlite3.Connection) -> str:
    """Mint an id not already present in ``sync_roots`` (retry bound 10)."""
    for _ in range(_MINT_RETRY_BOUND):
        candidate = ids.mint()
        row = conn.execute("SELECT 1 FROM sync_roots WHERE id=?", (candidate,)).fetchone()
        if row is None:
            return candidate
    raise IdMintError(f"failed to mint a unique sync-root id after {_MINT_RETRY_BOUND} attempts")


def register_sync_root(
    conn: sqlite3.Connection,
    name: str,
    root_path: str,
) -> dict[str, Any]:
    """Durably register or update one watched filesystem root.

    The registration is operational state required to resume watching after
    daemon restart, even before any ``sync_files`` rows exist. Upsert is by
    human-facing name and preserves both the stable id and ``created_at``.
    """
    if not name.strip():
        raise ValueError("sync-root name must not be empty")
    if not root_path.strip():
        raise ValueError("sync-root path must not be empty")

    with conn:
        existing = conn.execute(
            "SELECT id, created_at FROM sync_roots WHERE name=?", (name,)
        ).fetchone()
        if existing is None:
            sync_root_id = _mint_unique_sync_root_id(conn)
            created_at = _now()
            conn.execute(
                "INSERT INTO sync_roots (id, name, root_path, created_at) VALUES (?, ?, ?, ?)",
                (sync_root_id, name, root_path, created_at),
            )
        else:
            sync_root_id, created_at = existing
            conn.execute(
                "UPDATE sync_roots SET root_path=? WHERE id=?",
                (root_path, sync_root_id),
            )
    return {
        "id": sync_root_id,
        "name": name,
        "root_path": root_path,
        "created_at": created_at,
    }


def list_sync_roots(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return durable sync-root registrations ordered by name."""
    rows = conn.execute(
        "SELECT id, name, root_path, created_at FROM sync_roots ORDER BY name"
    ).fetchall()
    return [
        {"id": row[0], "name": row[1], "root_path": row[2], "created_at": row[3]} for row in rows
    ]


# --- Base store (spec §4.8 ``base_store.get``/``put``, §4.4 ``objects``/``sync_files.base_hash``)
# --- ``sync/base_store.py`` keeps the last-agreed canonical bytes ("B" of the three-way
# reconcile); the raw writes live here (rule 0.4).
#
# SPEC-QUESTION (T5.1): §4.4/§4.8 do not pin a base snapshot's byte layout. Narrowest reading: the
# RAW canonical UTF-8 bytes of the file text (kind ``"base_snapshot"``), content-addressed by their
# hash, not wrapped in canonical JSON like node snapshots. So ``sync_files.base_hash`` points at an
# object whose ``bytes`` are exactly the agreed file text, and ``gc_objects`` needs only the hash.
# See docs/spec-questions.md T5.1.


def _insert_base_snapshot(conn: sqlite3.Connection, canonical_text: str, now: str) -> str:
    """Content-addressed insert of one base snapshot's raw canonical bytes. ``canonical_text`` must
    already be canonical (spec §4.3). ``INSERT OR IGNORE``: re-putting identical text is a no-op.
    """
    data = canonical_text.encode("utf-8")
    obj_hash = object_hash(data)
    conn.execute(
        "INSERT OR IGNORE INTO objects (hash, kind, bytes, created_at) VALUES (?, ?, ?, ?)",
        (obj_hash, "base_snapshot", data, now),
    )
    return obj_hash


def sync_root_exists(conn: sqlite3.Connection, sync_root_id: str) -> bool:
    """Read-only: True iff ``sync_root_id`` has a durable ``sync_roots`` row."""
    row = conn.execute("SELECT 1 FROM sync_roots WHERE id=?", (sync_root_id,)).fetchone()
    return row is not None


def write_base_snapshot(
    conn: sqlite3.Connection,
    sync_root_id: str,
    path: str,
    canonical_text: str,
) -> str:
    """Record ``canonical_text`` as ``path``'s last-agreed base snapshot; return its
    ``objects.hash``.

    Inserts the object and upserts ``sync_files`` (keyed by ``path``) in one transaction. Raises
    ``SyncRootNotFoundError`` for an unregistered root.
    """
    if not sync_root_exists(conn, sync_root_id):
        raise SyncRootNotFoundError(sync_root_id)
    now = _now()
    with conn:
        obj_hash = _insert_base_snapshot(conn, canonical_text, now)
        conn.execute(
            "INSERT INTO sync_files (path, sync_root_id, base_hash, last_synced_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET "
            "sync_root_id=excluded.sync_root_id, base_hash=excluded.base_hash, "
            "last_synced_at=excluded.last_synced_at",
            (path, sync_root_id, obj_hash, now),
        )
    return obj_hash


def list_sync_files(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Read-only: every tracked ``sync_files`` row as ``{path, sync_root_id, base_hash,
    last_synced_at}`` (spec §4.4). Feeds ``ProjectionIndex`` (rebuilt purely from durable state)
    and ``/sync/status``.
    """
    rows = conn.execute(
        "SELECT path, sync_root_id, base_hash, last_synced_at "
        "FROM sync_files ORDER BY path"
    ).fetchall()
    return [
        {
            "path": r[0],
            "sync_root_id": r[1],
            "base_hash": r[2],
            "last_synced_at": r[3],
        }
        for r in rows
    ]


def read_base_snapshot(conn: sqlite3.Connection, sync_root_id: str, path: str) -> str | None:
    """``path``'s last-agreed canonical base text, or ``None`` if unset or recorded under a
    different sync root (the association is per root, so callers never read across roots).
    """
    row = conn.execute(
        "SELECT sync_root_id, base_hash FROM sync_files WHERE path=?", (path,)
    ).fetchone()
    if row is None:
        return None
    row_sync_root_id, base_hash = row
    if row_sync_root_id != sync_root_id or base_hash is None:
        return None
    obj_row = conn.execute("SELECT bytes FROM objects WHERE hash=?", (base_hash,)).fetchone()
    if obj_row is None:
        return None
    data: bytes = obj_row[0]
    return data.decode("utf-8")


# --- Read-only metrics aggregation (spec §7, §4.11 ``GET /metrics``) --- Every §7 counter that
# reads persistent state does so here; none of these opens a write transaction.

# Node types exempt from facet-coverage's denominator: `maturity.py`'s S2
# derivation lets task/entity nodes reach S2+ on inbound-edge count alone
# (no facets required -- see `_S2_FACET_EXEMPT_TYPES`), so an S2+ task/
# entity node structurally never carries a meaningful facet binding.
# Spec §7's "S2+ definitions" (facet_coverage) and vision.md R8 ("facet
# coverage is a gating dogfood metric" tied to relation-from-span capture
# on definition-like nodes) both read as excluding exactly this exempt
# set, not the literal `node_type == "definition"` string.
_FACET_COVERAGE_EXEMPT_TYPES: tuple[str, ...] = ("task", "entity")
_S2_PLUS_MATURITIES: tuple[str, ...] = ("S2", "S3", "S4")


def facet_coverage_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Read-only counts for the §7 ``facet_coverage`` metric: ``{"covered": n, "total": n}``.

    ``total`` is every live node at S2+ except the task/entity types that reach S2+ without needing
    a facet; ``covered`` is the subset with a live inbound justification edge bound to a concrete
    facet id (a ``"*"`` binding is legal but counts against coverage, spec §4.2). ``metrics.py``
    divides.
    """
    maturity_placeholders = ",".join("?" for _ in _S2_PLUS_MATURITIES)
    type_placeholders = ",".join("?" for _ in _FACET_COVERAGE_EXEMPT_TYPES)
    total_row = conn.execute(
        "SELECT COUNT(*) FROM nodes "
        f"WHERE status='live' AND maturity IN ({maturity_placeholders}) "
        f"AND node_type NOT IN ({type_placeholders})",
        (*_S2_PLUS_MATURITIES, *_FACET_COVERAGE_EXEMPT_TYPES),
    ).fetchone()
    justification_placeholders = ",".join("?" for _ in JUSTIFICATION_EDGE_TYPES)
    covered_row = conn.execute(
        "SELECT COUNT(DISTINCT n.id) FROM nodes n "
        "JOIN edges e ON e.dst = n.id AND e.retracted_at IS NULL "
        f"WHERE n.status='live' AND n.maturity IN ({maturity_placeholders}) "
        f"AND n.node_type NOT IN ({type_placeholders}) "
        f"AND e.edge_type IN ({justification_placeholders}) "
        "AND e.facet_binding IS NOT NULL AND e.facet_binding != '*'",
        (
            *_S2_PLUS_MATURITIES,
            *_FACET_COVERAGE_EXEMPT_TYPES,
            *JUSTIFICATION_EDGE_TYPES,
        ),
    ).fetchone()
    return {"covered": int(covered_row[0]), "total": int(total_row[0])}


def count_reviews_created_since(conn: sqlite3.Connection, since_iso: str) -> int:
    """Read-only: count of ``review_queue`` rows (every ``cause_kind``) with ``created_at >=
    since_iso`` (spec §7 ``review_inflow_7d``). Lexical comparison is safe: ``_now()`` timestamps
    are fixed-width UTC.
    """
    row = conn.execute(
        "SELECT COUNT(*) FROM review_queue WHERE created_at >= ?", (since_iso,)
    ).fetchone()
    return int(row[0])


def count_reviews_resolved_since(conn: sqlite3.Connection, since_iso: str) -> int:
    """Read-only: count of ``review_queue`` rows with ``resolved_at >= since_iso``."""
    row = conn.execute(
        "SELECT COUNT(*) FROM review_queue WHERE resolved_at >= ?", (since_iso,)
    ).fetchone()
    return int(row[0])


def list_review_created_at_since(conn: sqlite3.Connection, since_iso: str) -> list[str]:
    """Read-only: every ``review_queue.created_at`` timestamp >= ``since_iso``.

    Raw timestamps (not a count) so ``metrics.py`` can bucket them by
    calendar day for ``inflow_variance_30d`` -- bucketing is metric
    *computation*, not a DB read, so it stays out of this module (rule
    0.4 covers reads/writes, not arithmetic on their results).
    """
    rows = conn.execute(
        "SELECT created_at FROM review_queue WHERE created_at >= ? ORDER BY created_at",
        (since_iso,),
    ).fetchall()
    return [r[0] for r in rows]


def count_violations_total(conn: sqlite3.Connection) -> int:
    """Read-only: total ``review_queue`` rows with ``cause_kind='violation'`` (all-time).

    Numerator for §7's ``violation_rate`` (``violations ÷ sync cycles``).
    Counts every violation ever raised (open or resolved) -- a resolved
    violation still happened during some past sync cycle, so excluding it
    would undercount the rate's numerator relative to its denominator
    (which also accumulates over the daemon's whole run, not a window).
    """
    row = conn.execute(
        "SELECT COUNT(*) FROM review_queue WHERE cause_kind='violation'"
    ).fetchone()
    return int(row[0])


def count_nodes_created_since(conn: sqlite3.Connection, since_iso: str | None = None) -> int:
    """Read-only: count of ``nodes`` rows created (optionally since ``since_iso``).

    Unfiltered by ``status``: a tombstoned/retracted node still happened
    as a creation event (spec §7's ``crossing_rate`` is "nodes created ÷
    day", a minting-activity rate, not a live-node census); an S0
    hard-delete removes the row entirely, so it naturally drops out on
    its own without a status filter.
    """
    if since_iso is None:
        row = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE created_at >= ?", (since_iso,)
        ).fetchone()
    return int(row[0])


def earliest_node_created_at(conn: sqlite3.Connection) -> str | None:
    """Read-only: the earliest ``nodes.created_at`` timestamp, or ``None`` if empty.

    Denominator basis for §7's ``crossing_rate`` -- ``metrics.py`` divides
    the total node count by the number of days elapsed since this instant
    (the daemon's own minting history), not a fixed calendar window.
    """
    # A bare MIN() aggregate always returns exactly one row; its value is
    # NULL (Python None) when the table is empty, never a missing row.
    row = conn.execute("SELECT MIN(created_at) FROM nodes").fetchone()
    return row[0]


# --- ``GET /sync/export`` support (spec §4.11 ``unfiled_node_count``) --- The route diffs the set
# of live node ids against the anchors it parses from every base snapshot (parsing lives outside
# this module); this only exposes the raw read.


def list_live_node_ids(conn: sqlite3.Connection) -> set[str]:
    """Read-only: ids of every live (non-tombstoned) node.

    ``status='live'`` excludes tombstoned (S1+ soft-deleted) nodes, matching
    the §4.11 ``unfiled_node_count`` definition ("live nodes present in no
    managed projection") -- a tombstoned node has nothing left to export
    regardless of whether some stale anchor for it still lingers in a vault
    file.
    """
    rows = conn.execute("SELECT id FROM nodes WHERE status='live'").fetchall()
    return {row[0] for row in rows}


# --- Age-based S0 retention GC (vision.md §14 A7) --- Only the read of which S0 nodes are old
# enough; deletion still goes through ``delete_node``.


def list_expired_s0_node_ids(conn: sqlite3.Connection, older_than_iso: str) -> list[str]:
    """Read-only: ids of live S0 nodes created before ``older_than_iso``, oldest first.

    An S1+ node is never eligible whatever its age ("GC blocked at S1", vision.md §14 A7).
    ``older_than_iso`` must use ``_now()``'s fixed-width format so string comparison is correct.
    """
    rows = conn.execute(
        "SELECT id FROM nodes WHERE maturity='S0' AND status='live' AND created_at < ? "
        "ORDER BY created_at",
        (older_than_iso,),
    ).fetchall()
    return [row[0] for row in rows]
