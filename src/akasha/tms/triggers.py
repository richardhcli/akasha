"""Trigger registry and evaluator (spec §4.10).

A CLOSED registry of four pure conditions ``condition(node, ctx) -> bool`` and ``evaluate``, which
runs them against one node and performs the sole side effect, ``store.enqueue_review``. A fifth
condition or any dynamic registration needs a spec change (§4.10); this is not a script runner
(§8).

* ``all_subtasks_closed``: a ``task`` with at least one live ``composes`` task-child, all ``done``.
  Enqueues one ``subtasks_closed`` review; it NEVER sets the supertask's ``task_state`` (§4.10, §9
  story 8): flagging a human is the only action.
* ``facet_interface_changed``: implemented AS §4.9: it delegates to ``tms.invalidate.invalidate``
  when the event carries a commit with touched facets; the ``facet_break`` reviews land on affected
  subscribers, and the walk owns the non-transitive damper.
* ``evidence_retracted``: a live justification edge into the node was just retracted. The model has
  no "evidence" edge type (``evidence`` is a node type), so the narrowest reading is "any
  justification edge into this node", informational only.
* ``recheck_after``: an ISO date and an opaque period label carried in the context; fires once the
  context's "now" reaches the date.

``evaluate`` (§4.10(a), after every commit touching the node or its children) and
``run_daily_tick`` (§4.10(b)) are standalone; ``store.commit_node`` wires the
``all_subtasks_closed`` and invalidation paths itself.

# SPEC-QUESTION (T7.3): ``recheck_after`` has params "an ISO date, period" but §4.4 has no column
# for a per-node schedule. Narrowest reading: the date and period travel in the caller-supplied
# ``TriggerContext``, not new persisted state; where a daily sweep sources them is left to
# whoever wires ``run_daily_tick`` into the daemon. Non-blocking; docs/spec-questions.md T7.3.
"""

from __future__ import annotations

import dataclasses
import sqlite3
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any

from akasha.kernel import store
from akasha.kernel.model import JUSTIFICATION_EDGE_TYPES, Edge, Node
from akasha.tms import invalidate

__all__ = ["TriggerContext", "CONDITIONS", "evaluate", "run_daily_tick"]


@dataclasses.dataclass(frozen=True)
class TriggerContext:
    """Event context for one ``evaluate`` call (spec §4.10). The caller fills only the fields
    relevant to the event; a condition whose field is absent is ``False``. ``children`` is never
    caller-set: ``evaluate`` recomputes it from the live store so ``all_subtasks_closed`` never
    sees a stale snapshot.
    """

    now: str  # ISO-8601 instant ("today", for recheck_after and the daily tick)
    commit: str | None = None  # commit hash; facet_interface_changed's cause_ref
    touched: frozenset[str] = frozenset()  # facet ids touched by `commit`
    retracted_edge: Edge | None = None  # the edge just retracted, for evidence_retracted
    recheck_date: str | None = None  # ISO date, per-node recheck_after param
    recheck_period: str | None = None  # opaque period label, informational only
    children: tuple[Node, ...] = ()  # live task-children of the node; set by `evaluate`


Condition = Callable[[Node, TriggerContext], bool]
Action = Callable[[sqlite3.Connection, Node, TriggerContext], list[dict[str, Any]]]


def _cond_all_subtasks_closed(node: Node, ctx: TriggerContext) -> bool:
    """True iff ``node`` is a supertask whose every live task-child is done (spec §4.10)."""
    if node.task_state is None:
        return False
    task_children = [c for c in ctx.children if c.task_state is not None]
    return bool(task_children) and all(c.task_state == "done" for c in task_children)


def _cond_facet_interface_changed(node: Node, ctx: TriggerContext) -> bool:
    """True iff this event is a commit that touched at least one facet (spec §4.9/§4.10)."""
    return ctx.commit is not None and bool(ctx.touched)


def _cond_evidence_retracted(node: Node, ctx: TriggerContext) -> bool:
    """True iff a live justification edge into ``node`` was just retracted (spec §4.10)."""
    edge = ctx.retracted_edge
    if edge is None:
        return False
    return edge.dst == node.id and edge.edge_type in JUSTIFICATION_EDGE_TYPES


def _cond_recheck_after(node: Node, ctx: TriggerContext) -> bool:
    """True iff the context's ``now`` has reached or passed ``recheck_date`` (spec §4.10)."""
    if ctx.recheck_date is None:
        return False
    return ctx.now >= ctx.recheck_date


CONDITIONS: Mapping[str, Condition] = MappingProxyType(
    {
        "all_subtasks_closed": _cond_all_subtasks_closed,
        "facet_interface_changed": _cond_facet_interface_changed,
        "evidence_retracted": _cond_evidence_retracted,
        "recheck_after": _cond_recheck_after,
    }
)


def _act_all_subtasks_closed(
    conn: sqlite3.Connection, node: Node, ctx: TriggerContext
) -> list[dict[str, Any]]:
    if store.find_open_reviews(conn, node_id=node.id, cause_kind="subtasks_closed"):
        return []
    return [store.enqueue_review(conn, node.id, "subtasks_closed", cause_ref=ctx.commit)]


def _act_facet_interface_changed(
    conn: sqlite3.Connection, node: Node, ctx: TriggerContext
) -> list[dict[str, Any]]:
    assert ctx.commit is not None  # guaranteed by _cond_facet_interface_changed
    return invalidate.invalidate(conn, node.id, ctx.commit, set(ctx.touched))


def _act_evidence_retracted(
    conn: sqlite3.Connection, node: Node, ctx: TriggerContext
) -> list[dict[str, Any]]:
    if store.find_open_reviews(conn, node_id=node.id, cause_kind="evidence_retracted"):
        return []
    edge = ctx.retracted_edge
    assert edge is not None  # guaranteed by _cond_evidence_retracted
    return [
        store.enqueue_review(
            conn, node.id, "evidence_retracted", cause_ref=edge.id, facet=edge.facet_binding
        )
    ]


def _act_recheck_after(
    conn: sqlite3.Connection, node: Node, ctx: TriggerContext
) -> list[dict[str, Any]]:
    if store.find_open_reviews(conn, node_id=node.id, cause_kind="recheck"):
        return []
    return [store.enqueue_review(conn, node.id, "recheck", cause_ref=ctx.recheck_date)]


_ACTIONS: Mapping[str, Action] = MappingProxyType(
    {
        "all_subtasks_closed": _act_all_subtasks_closed,
        "facet_interface_changed": _act_facet_interface_changed,
        "evidence_retracted": _act_evidence_retracted,
        "recheck_after": _act_recheck_after,
    }
)

assert set(CONDITIONS) == set(_ACTIONS)  # registry closure: names never diverge


def _live_task_children(conn: sqlite3.Connection, node_id: str) -> tuple[Node, ...]:
    """Read-only: every live ``composes`` child of ``node_id`` (spec §4.7 parent->child)."""
    edges = store.find_live_edges(conn, src=node_id, edge_type="composes")
    children: list[Node] = []
    for e in edges:
        try:
            children.append(store.get_node(conn, e.dst))
        except store.NodeNotFoundError:  # pragma: no cover - defensive, dangling edge
            continue
    return tuple(children)


def evaluate(
    conn: sqlite3.Connection, node_id: str, ctx: TriggerContext
) -> list[dict[str, Any]]:
    """Run every condition against ``node_id`` for one event (spec §4.10(a)); return the newly
    enqueued review rows (``[]`` if unaffected).

    The caller builds a ``TriggerContext`` (a commit and its touched facets, a retracted edge, or a
    recheck schedule). No condition mutates node, edge or task state; the only side effect is
    ``store.enqueue_review``.
    """
    node = store.get_node(conn, node_id)
    ctx = dataclasses.replace(ctx, children=_live_task_children(conn, node_id))

    enqueued: list[dict[str, Any]] = []
    for name, condition in CONDITIONS.items():
        if condition(node, ctx):
            enqueued.extend(_ACTIONS[name](conn, node, ctx))
    return enqueued


def run_daily_tick(
    conn: sqlite3.Connection, contexts: Mapping[str, TriggerContext]
) -> list[dict[str, Any]]:
    """Run ``evaluate`` for every ``node_id -> TriggerContext`` in ``contexts`` (spec §4.10(b), the
    daily tick). Choosing which nodes to sweep and their recheck schedules is the caller's job (see
    the module SPEC-QUESTION); this only iterates.
    """
    enqueued: list[dict[str, Any]] = []
    for node_id, ctx in contexts.items():
        enqueued.extend(evaluate(conn, node_id, ctx))
    return enqueued
