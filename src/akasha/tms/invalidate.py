"""Interface-break invalidation walk (spec §4.9).

Trigger: any commit with ``change_class == "major"``. This module only
implements the walk itself -- classifying a commit as major (or minor) is
the caller's job (T7.2); ``invalidate`` just honors whatever ``touched``
set of facet ids it is handed. Node retraction ("major touching all
facets") is likewise a caller concern: the caller passes ``touched`` equal
to the full set of the retracted node's facet ids.

Implements exactly the §4.9 pseudocode::

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

    # SPEC-QUESTION: §4.9's pseudocode calls `composes_touched_facet(edge,
    # touched)` but never defines it anywhere in the spec. The predicate's
    # first two clauses (`facet_binding in touched` / `facet_binding ==
    # '*'`) already cover every `composes` edge with a specific or
    # wildcard facet binding, so this third clause can only add coverage
    # for a `composes` edge with `facet_binding IS NULL` (a whole-node
    # composition with no facet binding at all). Narrowest reading
    # adopted here: such a whole-node `composes` edge is considered
    # touched by ANY non-empty `touched` set (i.e. any interface break on
    # the target, not tied to a specific facet, is relevant to a plain
    # "this node is part of that node" subscription). Logged in
    # docs/spec-questions.md under task T7.1.
    """
    return edge.facet_binding is None and len(touched) > 0


def _already_unresolved_stale(conn: sqlite3.Connection, src: str) -> bool:
    """Non-transitive damper: True iff ``src`` already has an open ``facet_break`` review."""
    return bool(store.find_open_reviews(conn, node_id=src, cause_kind="facet_break"))


def invalidate(
    conn: sqlite3.Connection, node_id: str, commit: str, touched: set[str]
) -> list[dict[str, Any]]:
    """Walk live subscriber edges into ``node_id`` and flag stale subscribers (spec §4.9).

    Selects every live (``retracted_at IS NULL``), ``mode == 'track'`` edge
    whose ``dst`` is ``node_id`` and whose ``edge_type`` is a justification
    type or ``composes``, and whose ``facet_binding`` is either one of the
    ``touched`` facet ids, the wildcard ``'*'``, or (composes edges only)
    satisfies ``_composes_touched_facet``. For each matching edge's ``src``,
    enqueues a ``facet_break`` review unless ``src`` already has an open
    ``facet_break`` review (the non-transitive damper -- a node already
    flagged stale is not re-flagged by a further downstream break).

    Returns the list of newly-enqueued review rows (as returned by
    ``store.enqueue_review``); an unaffected call returns ``[]``.
    """
    live_edges = store.find_live_edges(conn, dst=node_id)
    subs = [
        e
        for e in live_edges
        if e.mode == "track"
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
            review = store.enqueue_review(
                conn, e.src, "facet_break", cause_ref=commit, facet=e.facet_binding
            )
            enqueued.append(review)
    return enqueued
