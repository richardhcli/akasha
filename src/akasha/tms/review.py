"""Review-queue resolutions + daily active-queue view (spec §4.9; task T7.5).

Orchestration only: every persistent write goes through ``kernel/store.py``
(rule 0.4). This module never executes raw SQL.

Resolutions (spec §4.9): ``still_holds``, ``revised`` (new commit via
``store.commit_node``, itself classified and may cascade), ``retracted``,
``dismissed`` (violations only). Daily active-queue cap of 10 is a
READ-SIDE view (``active_queue``); ``store.enqueue_review`` remains
unbounded — a write-side cap would silently drop review items (zero-
silent-guesses invariant).
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from akasha.kernel import store
from akasha.kernel.model import Facet

# Narrowest reading for create-node proposal approval: the resolution enum
# has no ``approved`` member (spec §4.4: still_holds|revised|retracted|dismissed).
# # SPEC-QUESTION (T7.5): proposal approval has no dedicated resolution value;
# # using ``still_holds`` as "accepted as proposed without revision".
_PROPOSAL_APPROVAL_RESOLUTION = "still_holds"

_ACTIVE_QUEUE_CAP = 10


class DismissalNotAllowedError(Exception):
    """Raised when ``dismissed`` is requested for a non-violation review."""

    def __init__(self, review_id: str, cause_kind: str) -> None:
        self.review_id = review_id
        self.cause_kind = cause_kind
        super().__init__(
            f"resolution 'dismissed' is only allowed for cause_kind='violation'; "
            f"review {review_id!r} has cause_kind={cause_kind!r}"
        )


class ProposalApprovalError(Exception):
    """Raised when ``approve_proposal`` is called on a non-proposal review."""

    def __init__(self, review_id: str, cause_kind: str) -> None:
        self.review_id = review_id
        self.cause_kind = cause_kind
        super().__init__(
            f"approve_proposal requires cause_kind='proposal'; "
            f"review {review_id!r} has cause_kind={cause_kind!r}"
        )


def _require_open(row: dict[str, Any]) -> None:
    if row["resolved_at"] is not None:
        raise store.ReviewAlreadyResolvedError(row["id"])


def active_queue(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return at most 10 OPEN review_queue rows in daily-queue order (spec §4.9).

    Ordering key: ``(staleness_age ASC, inbound_edge_count DESC, user_flag)``.
    ``staleness_age`` is the row's ``created_at`` ISO-8601 string (older first).
    ``inbound_edge_count`` is the number of LIVE inbound edges on the review's
    node (0 when ``node_id`` is NULL — never call ``find_live_edges(dst=None)``,
    which would match every live edge). Read-side cap only: the full open set
    remains available via ``store.find_open_reviews``.
    """
    open_rows = store.find_open_reviews(conn)

    def sort_key(row: dict[str, Any]) -> tuple[str, int]:
        node_id = row["node_id"]
        # CRITICAL: omit dst only when filtering is intended to be absent;
        # dst=None means "every live edge", not "edges with NULL dst".
        inbound = (
            0 if node_id is None else len(store.find_live_edges(conn, dst=node_id))
        )
        # # SPEC-QUESTION (T7.5): ordering mentions a user-flag tiebreaker, but
        # # review_queue DDL has no user_flag (or priority) column — treat as
        # # an absent/constant tiebreaker (nothing to read).
        return (row["created_at"], -inbound)

    ordered = sorted(open_rows, key=sort_key)
    return ordered[:_ACTIVE_QUEUE_CAP]


def resolve_review(
    conn: sqlite3.Connection,
    review_id: str,
    resolution: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Apply one of the four §4.9 resolutions to ``review_id``.

    ``still_holds`` / ``retracted`` / ``dismissed``: mark resolved via
    ``store.resolve_review`` (no new commit). ``dismissed`` is allowed only
    when ``cause_kind == 'violation'``.

    ``revised``: call public ``store.commit_node`` as one complete transaction
    (with the caller's new_body/facets/change_class/facets_touched/author/
    message kwargs), THEN ``store.resolve_review`` as a second, separate
    top-level transaction. Never wrap ``commit_node`` in another ``with
    conn:`` — it already opens its own, and nesting would commit early.
    """
    row = store.get_review(conn, review_id)
    _require_open(row)

    if resolution == "dismissed":
        if row["cause_kind"] != "violation":
            raise DismissalNotAllowedError(review_id, row["cause_kind"])
        return store.resolve_review(conn, review_id, "dismissed")

    if resolution in ("still_holds", "retracted"):
        return store.resolve_review(conn, review_id, resolution)

    if resolution == "revised":
        node_id = row["node_id"]
        if node_id is None:
            raise ValueError(
                f"resolution 'revised' requires a non-NULL node_id; "
                f"review {review_id!r} has node_id=NULL"
            )
        # Transaction 1: commit_node (owns its own with conn:; may cascade
        # facet_break reviews via invalidate inside that same txn).
        commit_kwargs: dict[str, Any] = {
            "new_body": kwargs.get("new_body"),
            "facets": kwargs.get("facets"),
            "change_class": kwargs["change_class"],
            "facets_touched": kwargs["facets_touched"],
            "author": kwargs["author"],
            "message": kwargs.get("message", ""),
        }
        if "task_state" in kwargs:
            commit_kwargs["task_state"] = kwargs["task_state"]
        store.commit_node(conn, node_id, **commit_kwargs)
        # Transaction 2: mark this review resolved (separate top-level txn).
        return store.resolve_review(conn, review_id, "revised")

    raise ValueError(
        f"invalid resolution {resolution!r}; must be one of "
        f"still_holds|revised|retracted|dismissed"
    )


def approve_proposal(conn: sqlite3.Connection, review_id: str) -> str:
    """Approve a create-node proposal: mint exactly once, then resolve.

    Looks up the review (must exist, ``cause_kind=='proposal'``, still open —
    a second call raises ``ReviewAlreadyResolvedError`` and mints nothing),
    parses ``cause_ref`` as JSON ``{method,path,body}``, calls
    ``store.create_node`` once, then records the minted id and resolves the
    review via ``store.finalize_proposal_approval``. Returns the new node_id.
    """
    row = store.get_review(conn, review_id)
    _require_open(row)
    if row["cause_kind"] != "proposal":
        raise ProposalApprovalError(review_id, row["cause_kind"])

    if not row["cause_ref"]:
        raise ValueError(f"proposal review {review_id!r} has empty cause_ref")
    envelope = json.loads(row["cause_ref"])
    body = envelope["body"]

    facets_raw = body.get("facets")
    facets: list[Facet] | None
    if facets_raw is None:
        facets = None
    else:
        facets = [f if isinstance(f, Facet) else Facet(**f) for f in facets_raw]

    # Transaction 1: mint (create_node owns its own with conn:).
    node = store.create_node(
        conn,
        node_type=body["node_type"],
        body=body["body"],
        facets=facets,
        task_state=body.get("task_state"),
        author="human",
        message=body.get("message", ""),
    )
    # # SPEC-QUESTION (T7.5): resolution enum has no 'approved' member
    # # (still_holds|revised|retracted|dismissed). Narrowest reading:
    # # record proposal approval as 'still_holds' ("accepted as proposed").
    # Transaction 2: attach node_id + resolve (own with conn:; never nests
    # around create_node).
    store.finalize_proposal_approval(
        conn, review_id, node.id, _PROPOSAL_APPROVAL_RESOLUTION
    )
    return node.id
