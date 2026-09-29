"""Interface-break invalidation walk (spec §4.9).

Trigger: any commit with ``change_class == "major"``. This module is only the walk: classifying a
commit (``kernel/commits.py::default_change_class``) and wiring (``store.commit_node``) are the
caller's, and ``invalidate`` honors whatever ``touched`` facet-id set it is handed. Node retraction
("major touching all facets") is likewise the caller passing the node's full facet set.

Transaction discipline: ``invalidate()`` opens no ``with conn:`` and enqueues through
``store.enqueue_review_within_transaction``, not the self-committing ``enqueue_review``.
``commit_node`` calls it INSIDE its own transaction so the ``facet_break`` reviews are atomic with
the commit, and sqlite3's ``with conn:`` commits on every block exit, so a nested transactional
call would commit the caller's pending writes early. A standalone caller (a unit test) wraps it in
its own ``with conn:`` if the reviews must be durable.

Exactly the §4.9 pseudocode::

    def invalidate(node_id, commit, touched: set[facet_id]):
        subs = edges where dst == node_id and retracted_at is null and mode == 'track'
               and edge_type in JUSTIFICATION | {'composes'}
               and (facet_binding in touched or facet_binding == '*'
                    or (edge_type == 'composes' and composes_touched_facet(edge, touched)))
        for e in subs:
            if not already_unresolved_stale(e.src):        # non-transitive damper
                enqueue_review(e.src, cause='facet_break', cause_ref=commit, facet=e.facet_binding)
"""

from __future__ import annotations

import sqlite3
from typing import Any

from akasha.kernel import store
from akasha.kernel.model import JUSTIFICATION_EDGE_TYPES, Edge

_SUBSCRIBER_EDGE_TYPES = JUSTIFICATION_EDGE_TYPES | {"composes"}


def _composes_touched_facet(edge: Edge, touched: set[str]) -> bool:
    """Whole-node ``composes`` subscription predicate.

    # SPEC-QUESTION (T7.1): §4.9 calls ``composes_touched_facet(edge, touched)`` but never
    # defines it. The first two clauses (a bound facet in ``touched``, or ``'*'``) already cover
    # every ``composes`` edge with a facet binding, so this only adds ``composes`` edges with
    # ``facet_binding IS NULL``. Narrowest reading: such a whole-node edge is touched by ANY
    # non-empty ``touched`` set (any interface break on the target matters to "this node is part
    # of that node"). See docs/spec-questions.md T7.1.
    """
    return edge.facet_binding is None and len(touched) > 0


def _already_unresolved_stale(conn: sqlite3.Connection, src: str) -> bool:
    """Non-transitive damper: True iff ``src`` already has an open ``facet_break`` review."""
    return bool(store.find_open_reviews(conn, node_id=src, cause_kind="facet_break"))


def invalidate(
    conn: sqlite3.Connection,
    node_id: str,
    commit: str,
    touched: set[str],
    exclude_srcs: set[str] | frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Walk live subscriber edges into ``node_id`` and flag stale subscribers (spec §4.9).

    Selects live (``retracted_at IS NULL``), ``mode == 'track'`` edges into ``node_id`` of a
    justification type or ``composes`` whose ``facet_binding`` is a ``touched`` id, ``'*'``, or
    (composes only) satisfies ``_composes_touched_facet``. For each ``src`` it enqueues a
    ``facet_break`` review unless ``src`` already has an open one (the non-transitive damper).
    Returns the new review rows (``[]`` if unaffected). Opens no transaction; see the module
    docstring. ``exclude_srcs`` (T22.5) skips subscribers that are not dependents, e.g. the
    superseding node in ``store.supersede_node``.
    """
    live_edges = store.find_live_edges(conn, dst=node_id)
    subs = [
        e
        for e in live_edges
        if e.mode == "track"
        and e.src not in exclude_srcs
        and e.edge_type in _SUBSCRIBER_EDGE_TYPES
        and (
            (e.facet_binding in touched)
            or e.facet_binding == "*"
            or (e.edge_type == "composes" and _composes_touched_facet(e, touched))
        )
    ]

    enqueued: list[dict[str, Any]] = []
    for e in subs:
        if not _already_unresolved_stale(conn, e.src):
            review = store.enqueue_review_within_transaction(
                conn, e.src, "facet_break", cause_ref=commit, facet=e.facet_binding
            )
            enqueued.append(review)
    return enqueued
