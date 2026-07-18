"""SQLite access layer. The only module that writes SQLite (build-plan rule 0.4).

Carries the migration runner (task T0.3), the node/commit-DAG store API
(task T1.3, spec §4.5), the edge create/retract + neighborhood/search
surface (task T1.4, spec §4.5, §4.2, §4.4), and deletion/tombstone/
redirects/split/merge plus maturity-recompute wiring (task T1.6, spec
§4.5, §4.6, §4.4 redirects table). Review queue + GC land in subsequent
M1 tasks.

Content-addressing scheme used throughout (not spelled out verbatim in the
DDL, spec §4.4, beyond "hash TEXT PRIMARY KEY" columns; narrowest reading
chosen here, see individual docstrings): both ``objects.hash`` and
``commits.hash`` are the sha256 hex digest (``canonical.object_hash``) of
the canonical-JSON (``canonical.canonical_json``) encoding of their own
content, mirroring how ``objects.hash`` is already used as a content
address for object bytes. A node's versioned content (body, facets,
task_state) is stored as one canonical-JSON blob per ``objects`` row
(kind ``"node_snapshot"``); ``node_type``/``maturity``/``status``/``vetted``
live only on the ``nodes`` row (not versioned by commits).

# SPEC-QUESTION (T1.3): §4.4/§4.5 name ``objects.bytes``/``commits.hash`` but
# never pin down (a) the exact byte layout of a node's versioned content
# blob, (b) whether ``commits.hash`` is content-addressed (git-style) or a
# minted id8 like other ids, or (c) which order ``history(id)`` returns
# commits in. Narrowest reading implemented above; each is independently
# testable/replaceable without an on-disk-format break to other modules
# (facets are not represented anywhere else in the DDL, so they must live
# in the object blob). See docs/spec-questions.md entry for T1.3.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, get_args

from akasha.kernel import ids, maturity
from akasha.kernel.canonical import canonical_json, canonicalize_text, object_hash
from akasha.kernel.model import ChangeClass, Edge, EdgeType, Facet, Node, NodeType

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"

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

    ``check_same_thread`` defaults to ``True`` (stdlib default, preserving
    every existing caller). The API daemon opens one connection PER REQUEST
    (see ``api/deps.py::get_conn``): WAL permits concurrent readers plus a
    single writer, so the daemon's real concurrency (the Web UI fires several
    ``fetch()``es in parallel) is served safely — a single ``sqlite3.Connection``
    shared across the ASGI threadpool is NOT safe under concurrent access and
    corrupts reads (see SPEC-QUESTION T8.5b amending spec §3). ``busy_timeout``
    makes a connection wait for a held write lock instead of raising
    ``SQLITE_BUSY`` when two requests write near-simultaneously.
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
    """Append exactly one ``review_queue`` row (spec §4.4, §4.6, §4.11).

    Persistent write, so per rule 0.4 this lives in ``kernel/store.py`` (no
    other module INSERTs into ``review_queue`` directly) -- same precedent
    as ``append_audit``/``vet_node``/``create_token``. ``cause_kind`` is a
    closed enum per the §4.4 DDL comment
    (``facet_break|subtasks_closed|evidence_retracted|recheck|conflict|
    violation|proposal``); this function does not validate membership
    itself (callers are the trusted in-repo call sites: T4.6's agent-proposal
    gate passes ``cause_kind="proposal"`` verbatim, future M7 triggers pass
    their own values) -- never invents a new value. ``resolved_at``/
    ``resolution`` start NULL (open item). ``node_id`` is NULL only when a
    create-node proposal has no node yet; the review id correlates that
    proposal and the real node id is minted on human approval. Returns the
    inserted row as a plain dict (mirrors ``create_token``'s "return what
    was persisted" shape) so callers can render it directly in an API
    response.
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
    """Body of ``enqueue_review``, without opening its own transaction (task T7.2).

    Invariant: identical to ``enqueue_review`` (see its docstring) but
    assumes the caller already holds an open ``with conn:`` block --
    mirrors ``_create_node_tx``'s/``_insert_commit``'s rationale exactly:
    sqlite3's ``with conn:`` commits on every block exit, not just the
    outermost one, so a function that may run INSIDE another mutation's
    transaction (``tms/invalidate.py``'s ``invalidate()``, called from
    ``commit_node`` on a major commit) must never open a second, nested
    ``with conn:`` of its own -- doing so would silently commit the
    caller's still-pending writes early, breaking the "atomic with the
    commit" invariant spec §4.9 requires for invalidation. Used by both
    ``enqueue_review`` itself (the standalone, transactional entry point)
    and ``tms.invalidate.invalidate`` (transaction-less, composed inside
    a caller's own transaction).
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
    """Read-only: every OPEN (``resolved_at IS NULL``) ``review_queue`` row (task T5.5).

    All three filters are optional and ANDed together (mirrors
    ``find_live_edges``'s filter style, T5.4); omitting all of them returns
    every open review item. Added outside this task's own Files list (rule
    0.4 recurring precedent, same as T4.2/T4.4/T4.5/T4.6/T5.1/T5.4's
    store.py touches) because ``sync/reconcile.py``'s conflict handler needs
    an idempotence gate ("is there already an open conflict review for this
    exact node+cause_ref?") before enqueueing a duplicate on crash-replay
    (T5.6). T5.7 ("Sync API routes") reuses this verbatim for
    ``/sync/status``'s open-review reporting -- designed as a general
    read-only query, not a conflict-only helper.
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
    """Mark a review_queue row resolved (spec §4.5, §4.9; task T7.5).

    Sets ``resolved_at=now()`` and ``resolution`` inside a single
    transaction. Raises ``ReviewNotFoundError`` if missing,
    ``ReviewAlreadyResolvedError`` if already resolved, ``ValueError`` if
    ``resolution`` is not one of ``still_holds|revised|retracted|dismissed``.
    Policy (e.g. ``dismissed`` only for ``violation``) lives in
    ``tms.review.resolve_review``, not here.
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
    """Recompute and persist node_id's maturity stage in place (spec §4.6).

    Invariant: reads node_id's ``node_type``/``vetted`` plus its current
    head object's facet count, and every *live* inbound edge
    (``dst=node_id AND retracted_at IS NULL``) resolved to
    ``(edge_type, source node_type)`` via a join against ``nodes`` on
    ``e.src = n.id``; feeds these into ``maturity.derive`` (spec §4.6,
    pure function, no DB access of its own) and ``UPDATE``s
    ``nodes.maturity`` to the result. Read-then-write only — never touches
    ``objects``/``commits``/``edges``. The caller is responsible for
    invoking this inside the same transaction as the mutation that changed
    an input (spec §4.6: "recomputed inside the same transaction as any
    mutation that can change the inputs"); this function does not open its
    own ``with conn:`` block. No-op if node_id no longer exists (e.g.
    called for a node that was hard-deleted earlier in the same
    transaction).
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
) -> Node:
    """Body of ``create_node``, without opening its own transaction.

    Invariant: identical to ``create_node`` (see its docstring) but assumes
    the caller already holds an open ``with conn:`` block — used both by
    ``create_node`` itself and by ``split_node`` (which mints several new
    nodes inside one outer transaction and must not nest ``with conn:``
    blocks, since sqlite3's context manager commits on every block exit,
    not just the outermost one).
    """
    if node_type not in _VALID_NODE_TYPES:
        raise ValueError(f"invalid node_type {node_type!r}; must be one of {_VALID_NODE_TYPES}")
    facets = facets or []
    canonical_body = canonicalize_text(body)
    content = _node_content(canonical_body, facets, task_state)
    now = _now()

    node_id = _mint_unique_id(conn)
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
) -> Node:
    """Create a brand-new node with a genesis commit (spec §4.5, §4.1).

    Invariant: mints a fresh id (retrying on ``nodes.id`` collision, bound
    10 attempts, then raising ``IdMintError`` — spec §4.1), inserts exactly
    one new ``objects`` row (canonical body+facets+task_state) and exactly
    one genesis ``commits`` row (empty ``parents``, ``change_class="major"``
    since a fresh node touches every facet it starts with), sets
    ``nodes.head_hash`` to that object, and inserts a matching row into
    ``nodes_fts`` (id, canonical body) so the node is immediately
    searchable — all inside a single transaction. Returns the resulting
    Node with its canonical body. A freshly-created node has no possible
    live inbound edges yet, so it is always ``S0`` at creation; no maturity
    recompute is needed here (unlike ``create_edge``/``retract_edge``/
    ``commit_node``, spec §4.6).
    """
    with conn:
        return _create_node_tx(conn, node_type, body, facets, task_state, author, message)


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
    """Return the commit hash that produced ``nodes.head_hash``, or ``None`` if unknown.

    Invariant: the newest (``rowid DESC``) commit whose ``object_hash``
    equals the node's CURRENT ``head_hash`` -- i.e. the commit the hub
    actually considers "current", not merely the most-recently-inserted
    commit row for this node. These differ once a conflict-branch commit
    (task T5.5, ``record_conflict_branch``) exists: a branch commit is
    appended to the DAG WITHOUT moving ``head_hash``, so the newest-inserted
    commit and the head commit are no longer the same row. Falls back to
    the newest commit by ``rowid`` if no commit matches ``head_hash``
    exactly (defensive; should not happen in practice since every write
    path that changes ``head_hash`` also inserts the commit that produced
    it in the same transaction). Returns ``None`` if node_id has no commits
    at all (or does not exist) -- callers treat that as "genesis, no
    parent", same as every existing parent-lookup call site.
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
    """Append the vault's conflicting version as a BRANCH commit (task T5.5, spec §4.8).

    On a both-sides-edit conflict, the reconcile pipeline keeps BOTH
    versions on the node's commit DAG rather than discarding either one
    (spec §4.8: "hub keeps both versions as branches on the node's commit
    DAG"). This function records the VAULT side as a new commit whose
    ``parents`` is the node's CURRENT head commit (``_head_commit_hash`` --
    the deterministic fork-anchor; the true last-synced commit the base
    snapshot reflects is not recoverable without a new schema addition, see
    the logged SPEC-QUESTION) WITHOUT moving ``nodes.head_hash`` -- the hub
    head stays whatever the file-side winner is (the mainline commit the
    caller already applied via ``commit_node``, or the pre-existing head if
    nothing was applied). Content: canonicalized ``branch_body`` (or the
    current head's body if ``branch_body`` is ``None`` -- e.g. a
    task-state-only conflict), the CURRENT head object's facets (a conflict
    branch never carries a facet edit of its own), and ``task_state``
    resolved via the same ``_UNSET_TASK_STATE`` sentinel ``commit_node``
    uses (omit to preserve the head's task_state, pass a value to set it).

    Idempotence (required for T5.6 crash-replay): the new object is
    content-addressed (``_insert_object``'s ``INSERT OR IGNORE`` -- a repeat
    call with identical content reuses the same ``objects`` row), and this
    function additionally gates the COMMIT insert itself: if a commit for
    ``node_id`` with this exact ``object_hash`` already exists, its hash is
    returned and NOTHING is inserted (no duplicate branch, no duplicate
    parent-chain fork). Never touches ``nodes.head_hash``/``updated_at``/
    ``nodes_fts`` and never calls ``_recompute_maturity`` -- no maturity
    input (facet count, vetted, live inbound edges) changed by recording an
    alternate, non-head version. Raises ``NodeNotFoundError`` if node_id
    does not exist. All inside a single transaction. Returns the (new or
    pre-existing) branch commit's hash.
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
    """Append a new commit to node_id's DAG and move its head (spec §4.5).

    Invariant: inserts exactly one new ``objects`` row (canonicalized
    ``new_body`` if given, else the current body; ``facets`` if given, else
    the current facets — "new_body|facets", spec §4.5) and exactly one new
    ``commits`` row parented on the node's current head commit (the most
    recently inserted commit for this node_id), then moves
    ``nodes.head_hash``/``updated_at`` to the new object and updates the
    matching ``nodes_fts`` row to the (possibly new) canonical body — all
    inside a single transaction. Never mutates an existing ``objects`` or
    ``commits`` row; the previous head remains reachable via ``history``.
    Also recomputes and persists node_id's maturity in the same
    transaction (spec §4.6), since a commit can change its facet count.

    # design note (T5.4, fable-reviewed, human-decided 2026-07-12, rule 0.4):
    # ``task_state`` is a new sentinel-guarded optional keyword, added here
    # (outside T5.4's own Files list, same recurring precedent as T4.2/T4.4/
    # T4.5/T4.6/T5.1's store.py touches) because the sync reconcile pipeline
    # needs to commit a checkbox toggle (spec §4.8 ``checkbox_toggled``) and
    # a same-commit body+state flip (``modified``) without any other caller
    # being able to accidentally clobber ``task_state`` on an ordinary body
    # edit. Omitting the argument (the default) preserves today's behavior
    # exactly (silently keeps the current ``task_state``); passing ``None``
    # or a literal value explicitly sets it. 100% backward compatible: no
    # existing call site passes this argument.
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

        # SPEC-QUESTION (T7.2): spec §4.9 says invalidation triggers on "any
        # commit with change_class == 'major'" but does not say which module
        # decides the effective change_class (heuristic default vs. an
        # explicit UI/CLI override) before it reaches this function.
        # Narrowest reading adopted here: ``commit_node`` never recomputes or
        # second-guesses ``change_class`` -- it is a plain, already-decided
        # argument (an explicit override IS respected simply because nothing
        # here overrides it back), and this function's ONLY job per T7.2's
        # Steps (3) is to trigger the already-implemented (T7.1) invalidation
        # walk whenever that argument is literally ``"major"``, using
        # ``facets_touched`` verbatim as the touched set (a caller modeling a
        # node retraction -- "always major touching all facets", §4.9 -- gets
        # this by passing every one of the node's facet ids in
        # ``facets_touched`` alongside ``change_class="major"``; see
        # ``kernel/commits.py``'s module docstring). Deferred import to avoid
        # a circular import (``tms.invalidate`` imports this module); this is
        # the T7.2-sanctioned store.py touch outside this task's own Files
        # list (rule 0.4 recurring precedent -- see docs/spec-questions.md
        # entry for T7.2). Called INSIDE this transaction, not after it, so
        # the enqueued facet_break reviews are atomic with the commit itself
        # (crash between the two would otherwise leave a major commit
        # persisted with no corresponding stale-subscriber flag); this is
        # safe only because ``invalidate()`` (via ``store.enqueue_review_within_transaction``)
        # never opens its own nested ``with conn:`` -- see
        # ``enqueue_review_within_transaction``'s docstring for why a nested transaction would
        # silently commit early instead.
        if change_class == "major":
            from akasha.tms import invalidate

            invalidate.invalidate(conn, node_id, commit_hash, set(facets_touched))

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
    """Read-only: decode one commit's ``{body, facets, task_state}`` content (task T5.5).

    Added outside this task's own Files list (rule 0.4 recurring precedent,
    same as the other store.py touches above) -- ``sync/reconcile.py``'s
    conflict-branch handler (and its tests) need to read back the body a
    branch commit carries WITHOUT moving ``nodes.head_hash`` or otherwise
    treating the branch as the node's current state, which ``get_node``
    cannot express (it only ever resolves to the current head or an
    as-of-time commit reachable from it). Raises ``NodeNotFoundError`` if
    ``commit_hash`` has no matching ``commits`` row (reusing the existing
    "unknown id" exception rather than inventing a new one for this
    read-only lookup).
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


def get_maturity(conn: sqlite3.Connection, node_id: str) -> str:
    """Return node_id's current persisted maturity stage (read-only, spec §4.6).

    Maturity is derived state kept on the ``nodes`` row (not versioned by
    commits, so the ``Node`` model does not carry it), refreshed by
    ``_recompute_maturity`` on every mutation that can change its inputs.
    The API's ``GET /nodes/{id}`` reports "node + maturity" (spec §4.11), so
    it needs this alongside the ``Node`` body. Raises ``NodeNotFoundError``
    if node_id is unknown.
    """
    row = conn.execute("SELECT maturity FROM nodes WHERE id=?", (node_id,)).fetchone()
    if row is None:
        raise NodeNotFoundError(node_id)
    return row[0]


def vet_node(conn: sqlite3.Connection, node_id: str) -> Node:
    """Mark node_id vetted (S4) and recompute its maturity (spec §4.6, §4.11 ``/vet``).

    The ``POST /nodes/{id}/vet`` endpoint (T4.4) is human-only (∅). Sets
    ``nodes.vetted=1`` and recomputes maturity in the SAME transaction (M1
    close follow-up: "vet endpoint must recompute maturity in-txn"), so the
    returned node reflects the new stage (S4 per ``maturity.derive``'s
    ``vetted`` rule). Idempotent: vetting an already-vetted node is a no-op
    re-write. Raises ``NodeNotFoundError`` if node_id is unknown. All writes
    route through this store function (rule 0.4).
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
    """Mint a brand-new facet on node_id from a highlighted span (task T7.7, spec §4.2).

    Invariant: reads node_id's current head content (raises
    ``NodeNotFoundError`` if unknown — same exception ``commit_node``
    raises, propagated from it below), mints a fresh ``facet_id`` via the
    same id8 scheme every other id in the system uses (``ids.mint()``,
    spec §4.1) -- reusing the CLI's ``_parse_facets`` precedent
    (``cli/main.py``): a facet_id is minted client/server-side with no DB
    collision check, since (unlike ``nodes``/``edges``/``tokens``/
    ``review_queue``) there is no standalone facets table to check
    uniqueness against -- facets live only inside a node's versioned
    object blob (module docstring above). Appends one new ``Facet`` (with
    ``version=1``, a brand-new facet has never had an interface break) to
    node_id's current facet list and commits it via ``commit_node`` with
    ``change_class="minor"`` and ``facets_touched=[new_facet_id]``.

    ``change_class="minor"`` (never ``"major"``) is not a judgment call:
    spec §4.9's invalidation-trigger heuristic fires on ``major`` iff a
    facet was "removed/renamed" or an existing facet's ``version`` was
    "bumped" -- a brand-new v1 facet is neither, and a ``major`` commit
    here would spuriously flag every other live inbound justification
    edge on node_id (a ``'*'``-bound one, or a ``composes`` edge) even
    though nothing they depend on changed (spec §4.9 ``invalidate``).

    # SPEC-QUESTION (T7.7): spec §4.2's ``Facet.name`` is a "short label,
    # unique per node", but facets-from-spans capture (§T7.7 step 1) only
    # supplies ``facet_span`` (the highlighted text) -- no name field.
    # Narrowest, collision-free reading used here: ``name = facet_id``
    # (the id8 is unique by construction, unlike the span text, which may
    # repeat). See docs/spec-questions.md entry for T7.7.

    Not atomic with a subsequent ``create_edge`` call (the caller's own
    transaction; this function opens and closes its own via
    ``commit_node``): if the caller mints a facet here and then
    ``create_edge`` fails (e.g. an edge-model validation error unrelated
    to the facet_binding itself), the minted facet is NOT rolled back --
    it remains a harmless, unbound extra facet on node_id, never a
    dangling reference (spec §4.5 has no cross-node atomic-commit
    primitive to compose the two writes into a single transaction).

    Returns the newly-minted ``Facet``. Raises ``NodeNotFoundError`` if
    node_id does not exist.
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
    """Create a new live edge from src to dst (spec §4.5, §4.2).

    Invariant: validates the ``facet_binding`` rule by constructing the
    ``Edge`` pydantic model (spec §4.2's ``_check_facet_binding`` validator
    — reused here verbatim, not reimplemented): justification edge types
    ({supports, contradicts, depends_on, derived_from, cites}) require
    ``facet_binding`` to be a facet_id or ``"*"``; ``None`` is only legal
    for composes/redirects_to. Raises ``pydantic.ValidationError`` (a
    ``ValueError`` subclass) and writes nothing if the rule is violated.
    On success: mints a fresh edge id (retrying on ``edges.id`` collision,
    bound 10 attempts, then raising ``IdMintError`` — spec §4.1) and
    inserts exactly one new ``edges`` row with ``created_at`` set and
    ``retracted_at`` NULL (live) — all inside a single transaction. Also
    recomputes and persists ``dst``'s maturity in the same transaction
    (spec §4.6), since a new live inbound edge is a maturity input.
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
    """Retract a live edge by setting ``retracted_at`` (spec §4.5).

    Invariant: never ``DELETE``s the ``edges`` row (append-only discipline,
    spec §4.4) — sets ``retracted_at`` to now instead, which excludes the
    edge from ``neighborhood`` (whose queries filter on
    ``retracted_at IS NULL``) while leaving it visible to any caller that
    reads the raw table for history. Raises ``EdgeNotFoundError`` if
    edge_id does not exist. A second retraction on an already-retracted
    edge just re-sets ``retracted_at`` to a later timestamp (not rejected
    as a no-op). Also recomputes and persists the retracted edge's
    ``dst``'s maturity in the same transaction (spec §4.6), since losing a
    live inbound edge is a maturity input change. All inside a single
    transaction.
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
    """Read-only: every live edge (``retracted_at IS NULL``) matching the given filters.

    # design note (T5.4, fable-reviewed, human-decided 2026-07-12, rule 0.4):
    # added outside T5.4's own Files list, same recurring precedent as the
    # store.py touches in T4.2/T4.4/T4.5/T4.6/T5.1 — the reconcile pipeline's
    # ``reparented`` op needs to locate the specific live ``composes`` edge
    # from a task's OLD parent before retracting it and creating the new
    # one, and no existing store function exposes a filtered edge lookup.
    #
    # All filters are optional and ANDed together; omitting all three
    # returns every live edge (rarely useful, but not rejected — the
    # caller's problem). Uses the same ``retracted_at IS NULL`` semantics
    # and row shape as ``neighborhood``/``_edge_row_to_model`` (no new
    # query pattern invented).
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
    """Return the live subgraph reachable from node_id within ``hops`` steps (spec §4.5).

    Invariant: read-only; only ever considers live edges
    (``retracted_at IS NULL``), queried via the partial indexes
    ``ix_edges_src``/``ix_edges_dst`` (spec §4.4) by selecting on ``src=?``
    and ``dst=?`` respectively (both directions, since a node's
    neighborhood includes edges pointing either into or out of it).
    Performs a breadth-first expansion for ``hops`` rounds starting from
    ``node_id``; a retracted edge is never traversed and never appears in
    the result. Returns ``{"node_ids": [...], "edges": [...]}`` where
    ``node_ids`` includes ``node_id`` itself and every node reached within
    ``hops`` steps, and ``edges`` is every distinct live edge seen along
    the way (as ``Edge`` models), deduplicated by id.
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


def search(conn: sqlite3.Connection, q: str) -> list[Node]:
    """Full-text search over node bodies via ``nodes_fts`` (spec §4.5, §4.4).

    Invariant: read-only; issues one FTS ``MATCH`` query against the
    ``nodes_fts`` virtual table (columns ``id UNINDEXED, body``, kept in
    sync with every node's current head body by ``create_node``/
    ``commit_node``), ranked by FTS5's built-in relevance (``rank``).
    Returns each matching node's current head content (via ``get_node``),
    best match first. A node whose current body no longer matches ``q``
    (because it was edited after the FTS row was last synced) is never
    returned, since ``nodes_fts`` is always kept current.
    """
    rows = conn.execute(
        "SELECT id FROM nodes_fts WHERE nodes_fts MATCH ? ORDER BY rank", (q,)
    ).fetchall()
    return [get_node(conn, row[0]) for row in rows]


def append_audit(
    conn: sqlite3.Connection,
    token_id: str | None,
    action: str,
    detail: str | None = None,
) -> None:
    """Append exactly one append-only row to ``audit_log`` (spec §4.4, §4.11).

    ``audit_log`` is persistent SQLite state, so per build-plan rule 0.4
    ("every mutation of persistent state goes through ``kernel/store.py``;
    no other module writes SQLite directly") the raw INSERT lives here —
    the API-layer audit middleware/decorator (T4.2, ``api/auth.py``) calls
    this rather than touching SQLite itself. ``ts`` is stamped here with
    the same ``_now()`` used by every other store write, keeping audit
    timestamps lexically comparable with commit/edge timestamps.

    Append-only: this issues a single ``INSERT`` and nothing else ever
    ``UPDATE``s or ``DELETE``s ``audit_log``. ``token_id`` is nullable
    (the DDL allows NULL for an unauthenticated action). The caller must
    never pass a raw secret in ``detail`` — this layer only ever sees a
    ``token_id`` (never the bearer secret), so no secret is available to
    leak into the log, but ``detail`` is caller-controlled free text and
    the caller owns keeping it secret-free (spec §4.11 step 2).
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
    """Delete node_id: hard-delete if S0, tombstone (+redirect) if S1+ (spec §4.5, §4.6).

    Invariant: recomputes node_id's maturity first (in the same
    transaction, so the decision uses fresh inputs). If the (possibly
    just-recomputed) maturity is ``S0``: hard-deletes — removes node_id's
    ``commits`` rows, then its ``nodes`` row, then every ``edges`` row with
    ``src=node_id OR dst=node_id`` (an S0 node has no live inbound edge by
    definition, per spec §4.6's S1 rule, but may still have outbound or
    retracted edges — those are removed too so nothing dangles), then its
    ``nodes_fts`` row. Does NOT touch ``objects`` rows (append-only except
    S0 GC, spec §4.4 — orphaned objects are reclaimed by the T1.7 GC job,
    not here).

    If maturity is S1+: requires either a non-empty ``redirect_to`` (list
    of successor node ids) or ``tombstone=True``; if neither is given,
    raises ``NeedsRedirectError`` (``.code == "E_NEEDS_REDIRECT"``) and
    writes/deletes nothing (the exception propagates out of the ``with
    conn:`` block, which rolls back the maturity-recompute write too).
    Otherwise sets ``nodes.status='tombstone'``; when ``redirect_to`` is
    given, additionally inserts one ``redirects`` row (``old_id=node_id``,
    JSON-encoded ``successors=redirect_to``, ``created_at``) and reassigns
    every live inbound edge of node_id to the FIRST entry of
    ``redirect_to`` (spec: "leave zero dangling references"), then
    recomputes that successor's maturity (its inbound-edge set changed).
    Raises ``NodeNotFoundError`` if node_id does not exist. All inside a
    single transaction.
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
    """Split node_id into one new node per entry of ``parts`` (spec §4.5).

    Invariant: ``parts`` is a non-empty list of ``create_node``-style
    kwargs dicts (``node_type``, ``body``, optional ``facets``,
    ``task_state``, ``author``, ``message``) — see SPEC-QUESTION below for
    why this shape was chosen. Mints one brand-new node per part (via
    ``_create_node_tx``, inside this function's own transaction rather
    than nesting another ``with conn:``); inserts exactly one
    ``redirects`` row (``old_id=node_id``, JSON-encoded
    ``successors=[new node ids in part order]``, ``created_at``); sets
    node_id's ``nodes.status='tombstone'``; reassigns every live inbound
    edge that pointed at node_id to the FIRST successor so nothing dangles
    (spec: "leave zero dangling references"); recomputes that successor's
    maturity. Returns ``{node_id: [successor_ids...]}``. Raises
    ``NodeNotFoundError`` if node_id does not exist, ``ValueError`` if
    ``parts`` is empty. All inside a single transaction.

    # SPEC-QUESTION (T1.6): spec §4.5 lists ``split_node(id, parts) ->
    # redirect`` but never specifies the shape of ``parts``. Narrowest
    # reading used here: a list of ``create_node``-style kwargs dicts, one
    # brand-new node minted per entry. See docs/spec-questions.md entry
    # for T1.6.
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
            # SPEC-QUESTION (T7.6): the review_queue.cause_kind closed enum
            # (facet_break|subtasks_closed|evidence_retracted|recheck|conflict|
            # violation|proposal, spec mvp-spec.md sec 4.4) has no member for
            # 'an inbound edge needs human reassignment after a split'; every
            # existing member is unsuitable because each is already used as an
            # idempotence-gate filter or resolution-path selector elsewhere in
            # the codebase that this task must not touch; narrowest reading
            # that does not silently overload a loaded existing member is to
            # introduce a new, clearly-flagged value 'reassignment' pending a
            # spec amendment.
            enqueue_review_within_transaction(
                conn, edge_src, "reassignment", cause_ref=cause_ref
            )

    return {node_id: successor_ids}


def gc_objects(conn: sqlite3.Connection) -> list[str]:
    """Delete every ``objects`` row unreachable from any live reference (spec §4.4, §4.5).

    Invariant (binding, spec §4.5's property suite / T1.8): **GC never
    removes a referenced object.** The REACHABLE set is computed as the
    UNION of (a) every ``commits.object_hash`` for every row currently in
    ``commits`` (i.e. every object still reachable via any node's DAG
    history, S0 or S1+ alike), (b) every ``nodes.head_hash`` (a live
    node's current head, redundant with (a) in normal operation but kept
    as an explicit belt-and-suspenders read), and (c) every non-NULL
    ``sync_files.base_hash`` (base snapshots). Anything in ``objects`` NOT
    in that union is, by construction, an orphan: an object whose owning
    node+commits were already hard-deleted by ``delete_node`` (spec §4.5:
    S0 hard-delete removes ``commits``/``nodes`` rows but intentionally
    leaves the ``objects`` row behind for this GC job to reclaim later).
    Computes the reachable set and issues one ``DELETE FROM objects WHERE
    hash NOT IN (...)`` inside a single transaction, then returns the
    sorted list of hashes actually deleted.

    # SPEC-QUESTION (T1.7): spec §4.5 phrases reachability as "objects
    # unreachable from any S1+ node or base snapshot", which read literally
    # would permit collecting an object still referenced by a live S0
    # node's commits/head — but that would break ``get_node``/``history``
    # for that node, directly contradicting the stronger, restated
    # invariant "GC never removes a referenced object" (spec §4.5's
    # property-suite line, and this task's own Goal/DoD wording). Narrowest
    # reading that satisfies BOTH sentences: widen reachability to "every
    # object referenced by any still-existing ``commits``/``nodes.head_hash``
    # row (S0 or S1+) or base snapshot" rather than "S1+ heads/history"
    # only. See docs/spec-questions.md entry for T1.7.
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
    """Merge multiple existing nodes into one surviving node (spec §4.5).

    Invariant: ``ids`` must have length >= 2. The FIRST entry of ``ids``
    is kept as the survivor — narrowest reading, since spec §4.5 says only
    "choose/keep a survivor" without specifying a selection algorithm (see
    SPEC-QUESTION below); no new node is created (unlike ``split_node``).
    For every OTHER entry (the retired nodes): inserts one ``redirects``
    row (``old_id=that id``, JSON-encoded ``successors=[survivor]``,
    ``created_at``); sets its ``nodes.status='tombstone'``; reassigns
    every live inbound edge that pointed at it to the survivor (spec:
    "leave zero dangling references"). Recomputes the survivor's maturity
    once at the end (its inbound-edge set changed, possibly repeatedly).
    Returns ``{old_id: [survivor] for each retired id}``. Raises
    ``ValueError`` if ``len(ids) < 2``, ``NodeNotFoundError`` if any id
    does not exist (checked before any write). All inside a single
    transaction.

    Deliberately enqueues no reassignment review — unlike split, merge has
    a single unambiguous survivor, so no inbound edge needs human
    reassignment (narrowest reading of task T7.6).

    # SPEC-QUESTION (T1.6): spec §4.5 lists ``merge_nodes(ids) ->
    # redirect`` but never specifies survivor-selection. Narrowest
    # reading used here: the first id in the list wins. See
    # docs/spec-questions.md entry for T1.6.
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
    """Follow ``redirects`` transitively to the current live terminal (spec §4.5, §4.11).

    Invariant: starting from ``node_id``, repeatedly look up
    ``redirects.successors`` for the current id; when a row exists, advance
    to ``successors[0]`` (mirrors the eager-reassignment convention of
    always following the FIRST successor by default). When no row exists,
    the current id is the terminal/live id — return it. Multi-hop matters
    because a node picked as a successor at one split/merge may itself be
    split/merged again later, so a single-hop redirect lookup would resolve
    to an already-tombstoned id. Terminates even on a pathological cycle:
    a ``seen`` set of visited ids stops the walk (return current) rather
    than looping forever. Read-only; never opens ``with conn:``.
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
    """Re-point one live edge's ``dst`` to ``new_dst`` (spec §4.5; task T7.6).

    Invariant: the sole write path used by ``tms.review.resolve_reassignment``
    to apply a human-chosen successor after a split (rule 0.4: only
    ``store.py`` issues the raw ``UPDATE``). Raises ``EdgeNotFoundError`` if
    ``edge_id`` is missing or already retracted. After the update, recomputes
    maturity for both the old and new destinations (both nodes' inbound-edge
    sets just changed). Own top-level transaction.
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


# --- Tokens (task T4.5, spec §4.4 ``tokens`` DDL / §4.11 ``/tokens``) ------
#
# ``api/auth.py`` (T4.1) deliberately only *reads* ``tokens`` (see its module
# docstring: "token issuance/revocation is a separate, API-layer concern that
# belongs to T4.5's /tokens route"). Per build-plan rule 0.4 ("every mutation
# of persistent state goes through kernel/store.py"), the actual INSERT/
# UPDATE for token create/revoke lives here, not in ``api/routes/tokens.py``.
# These functions never see or return a raw secret — the caller (T4.5's
# route) hashes the secret via ``api.auth.hash_secret`` first and passes only
# ``secret_hash`` in; ``list_tokens``/``create_token`` never select or return
# ``secret_hash`` back out, so a raw or hashed secret can never leak through
# this surface (spec §4.11 / build-plan T4.5 constraint: "never store or log
# a raw secret" — the hash itself is already stored by the time it reaches
# here, and is never re-exposed).


def create_token(
    conn: sqlite3.Connection,
    name: str,
    token_class: str,
    secret_hash: str,
    rate_per_min: int | None = None,
) -> dict[str, Any]:
    """Create a new ``tokens`` row and return its public fields (spec §4.4, §4.11).

    Invariant: ``token_class`` must be ``"human"`` or ``"agent"`` (the DDL's
    documented enum, spec §4.4 comment); raises ``ValueError`` and writes
    nothing otherwise. Mints a fresh id8 (retrying on ``tokens.id``
    collision, bound 10 attempts, then ``IdMintError`` — same scheme as
    nodes/edges, spec §4.1) and inserts exactly one row with
    ``created_at`` set and ``revoked_at`` NULL, inside a single transaction.
    Returns ``{id, name, class, rate_per_min, created_at, revoked_at}`` —
    deliberately omits ``secret_hash`` (never re-exposed once stored).
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
    """Set ``revoked_at`` on token_id (spec §4.4, §4.11 ``DELETE /tokens/{id}``).

    Invariant: never ``DELETE``s the ``tokens`` row (append-only discipline,
    mirroring ``retract_edge``'s soft-retract pattern) — a revoked token's
    audit history (``audit_log.token_id``) must remain resolvable. Raises
    ``TokenNotFoundError`` if token_id does not exist. Re-revoking an
    already-revoked token just re-sets ``revoked_at`` to a later timestamp
    (not rejected as a no-op), same as ``retract_edge``.
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


# ---------------------------------------------------------------------------
# Base store support (task T5.1, spec §4.8 ``base_store.get``/``.put``,
# §4.4 ``objects``/``sync_files.base_hash``).
#
# ``sync/base_store.py`` is the last-agreed-canonical-bytes snapshot used as
# the "B" input of the §4.8 three-way reconcile. Per build-plan rule 0.4
# ("every mutation of persistent state goes through kernel/store.py") the
# raw SQLite writes (an ``objects`` insert + a ``sync_files`` upsert) live
# here, not in ``base_store.py``.
#
# SPEC-QUESTION (T5.1): build-plan T5.1's ``Files`` list only names
# ``src/akasha/sync/base_store.py`` and its test file, omitting
# ``kernel/store.py`` — but rule 0.4 forces the raw ``objects``/
# ``sync_files`` writes here, same precedent as T4.2 ``append_audit``,
# T4.4 ``vet_node``, T4.5 token helpers, T4.6 ``enqueue_review`` (all
# resolved "rule 0.4 controls" in docs/archived-questions.md's M4 batch).
# See docs/spec-questions.md entry for T5.1.
#
# SPEC-QUESTION (T5.1): spec §4.4/§4.8 don't pin down a base-snapshot
# ``objects`` row's byte layout. Build-plan T5.1's Steps line says
# ``put(...)`` "stores canonical bytes as an object" — narrowest literal
# reading taken: a base snapshot is stored as the RAW canonical UTF-8
# bytes of the file text (kind ``"base_snapshot"``), content-addressed by
# ``object_hash`` of those raw bytes directly — NOT wrapped in a
# canonical-JSON dict the way node snapshots are (``_insert_object``/
# ``"node_snapshot"``). This keeps ``sync_files.base_hash`` pointing at an
# object whose ``bytes`` column *is* exactly the last-agreed canonical
# file text, so a future diff/patch tool can read it back without any
# JSON unwrapping step, and it composes cleanly with ``gc_objects``
# (T1.7), which already treats every non-NULL ``sync_files.base_hash`` as
# a reachability root purely by hash lookup, independent of the
# referenced object's ``kind``/content shape. See docs/spec-questions.md
# entry for T5.1.


def _insert_base_snapshot(conn: sqlite3.Connection, canonical_text: str, now: str) -> str:
    """Content-addressed insert of one base snapshot's raw canonical bytes.

    ``canonical_text`` must already be canonicalized (spec §4.3) by the
    caller — this function does not canonicalize. Hash is
    ``object_hash`` of the UTF-8 encoding of ``canonical_text`` directly
    (not a canonical-JSON-wrapped dict; see the content-shape note above).
    ``INSERT OR IGNORE`` mirrors ``_insert_object``'s idempotent-reinsert
    behavior: identical canonical text always hashes identically, so a
    repeat ``put`` of the same content is a safe no-op, never a mutation of
    an existing row.
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
    contract_version: int | None = None,
) -> str:
    """Durably record ``canonical_text`` as ``path``'s new last-agreed base snapshot.

    Raises ``SyncRootNotFoundError`` if ``sync_root_id`` is not a durably
    registered sync root (spec §4.10/T4.10 registry) — the base store must
    never silently associate a snapshot with an unknown root. Inserts the
    content-addressed ``objects`` row (see ``_insert_base_snapshot``) and
    upserts ``sync_files`` keyed by ``path`` (its primary key, spec §4.4)
    so ``base_hash`` and ``sync_root_id`` both move together, inside one
    transaction. Returns the new base snapshot's ``objects.hash``.

    # design note (T5.4, fable-reviewed, human-decided 2026-07-12): closes
    # T5.1's logged SPEC-QUESTION on ``sync_files.contract_version``. The
    # new optional ``contract_version`` keyword lets a caller that actually
    # parsed the file's front matter (T5.4's reconcile pipeline) pass the
    # real value through explicitly; omitting it (the default, ``None``)
    # preserves T5.1's exact original behavior (preserve the existing row's
    # value across a re-``put``, else default to the literal ``1``) — fully
    # backward compatible, ``sync/base_store.py::put`` (T5.1) is unchanged.
    """
    if not sync_root_exists(conn, sync_root_id):
        raise SyncRootNotFoundError(sync_root_id)
    now = _now()
    with conn:
        obj_hash = _insert_base_snapshot(conn, canonical_text, now)
        existing = conn.execute(
            "SELECT contract_version FROM sync_files WHERE path=?", (path,)
        ).fetchone()
        if contract_version is not None:
            resolved_contract_version = contract_version
        elif existing is not None:
            resolved_contract_version = existing[0]
        else:
            resolved_contract_version = 1
        conn.execute(
            "INSERT INTO sync_files "
            "(path, sync_root_id, base_hash, contract_version, last_synced_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET "
            "sync_root_id=excluded.sync_root_id, base_hash=excluded.base_hash, "
            "contract_version=excluded.contract_version, "
            "last_synced_at=excluded.last_synced_at",
            (path, sync_root_id, obj_hash, resolved_contract_version, now),
        )
    return obj_hash


def list_sync_files(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Read-only enumerator of every tracked ``sync_files`` row (spec §4.4).

    # design note (T5.4, fable-reviewed, human-decided 2026-07-12, rule 0.4):
    # added outside T5.4's own Files list, same recurring precedent as the
    # other store.py touches listed above — ``sync/reconcile.py``'s
    # ``ProjectionIndex`` (cross-file ``E_DUP_ID``/move detection, spec
    # §4.7/§3.5's M5 follow-up) needs to enumerate every synced file's
    # ``(path, sync_root_id, base_hash, contract_version)`` to rebuild its
    # id -> path ownership map purely from durable state (crash-safe,
    # rebuildable). Read-only; never used to author truth, only to look up
    # which base snapshot to re-parse per path.
    #
    # design note (T5.7, rule 0.4): ``last_synced_at`` added to the
    # projected columns (the column already exists in the ``sync_files``
    # DDL, migration 002 — this is a read-only SELECT-list widening, not a
    # schema change) so ``GET /sync/status`` can report each file's
    # last-synced timestamp without a second query. Purely additive;
    # T5.4's existing callers that only read ``path``/``sync_root_id``/
    # ``base_hash``/``contract_version`` are unaffected.
    """
    rows = conn.execute(
        "SELECT path, sync_root_id, base_hash, contract_version, last_synced_at "
        "FROM sync_files ORDER BY path"
    ).fetchall()
    return [
        {
            "path": r[0],
            "sync_root_id": r[1],
            "base_hash": r[2],
            "contract_version": r[3],
            "last_synced_at": r[4],
        }
        for r in rows
    ]


def read_base_snapshot(conn: sqlite3.Connection, sync_root_id: str, path: str) -> str | None:
    """Return ``path``'s last-agreed canonical base text, or ``None`` if unset.

    Scoped to ``sync_root_id``: a ``sync_files`` row that exists but is
    associated with a *different* sync root (or has no ``base_hash`` yet)
    reads as ``None``, same as a wholly fresh path — the base-store
    association is per-root, not just per-path (spec §4.4: ``path`` is
    globally unique as the table's primary key, but callers must still be
    scoped to their own root to avoid cross-root leakage).
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
