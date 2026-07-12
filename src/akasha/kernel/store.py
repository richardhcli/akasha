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
from typing import Any, get_args

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


class IdMintError(Exception):
    """Raised when minting a unique node id fails after the retry bound (spec §4.1)."""


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


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
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


def commit_node(
    conn: sqlite3.Connection,
    node_id: str,
    new_body: str | None = None,
    facets: list[Facet] | None = None,
    *,
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
        task_state = current_content.get("task_state")

        content = _node_content(canonical_body, new_facets, task_state)
        obj_hash = _insert_object(conn, content, now)

        parent_row = conn.execute(
            "SELECT hash FROM commits WHERE node_id=? ORDER BY rowid DESC LIMIT 1", (node_id,)
        ).fetchone()
        parents = [parent_row[0]] if parent_row is not None else []

        _insert_commit(
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

    return Node(
        id=node_id,
        node_type=node_type,
        body=canonical_body,
        facets=new_facets,
        task_state=task_state,
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


def _reassign_inbound_edges(conn: sqlite3.Connection, old_dst: str, new_dst: str) -> None:
    """Point every *live* edge targeting ``old_dst`` at ``new_dst`` instead.

    Invariant: only touches edges with ``retracted_at IS NULL`` (a
    retracted edge is dead history, not a dangling reference — it is left
    alone). Does not recompute anyone's maturity; callers do that
    afterward for whichever node(s) gained/lost inbound edges.
    """
    conn.execute(
        "UPDATE edges SET dst=? WHERE dst=? AND retracted_at IS NULL", (new_dst, old_dst)
    )


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

        conn.execute(
            "UPDATE nodes SET status='tombstone', updated_at=? WHERE id=?", (now, node_id)
        )
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
        conn.execute(
            "UPDATE nodes SET status='tombstone', updated_at=? WHERE id=?", (now, node_id)
        )
        first_successor = successor_ids[0]
        _reassign_inbound_edges(conn, node_id, first_successor)
        _recompute_maturity(conn, first_successor)

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
