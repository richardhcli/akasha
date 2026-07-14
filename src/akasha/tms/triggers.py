"""Trigger registry + evaluator (spec §4.10).

A **closed** registry of four named, pure-function conditions
``condition(node, ctx) -> bool``, plus a standalone evaluator (``evaluate``)
that runs every condition against one node and, for each condition that
fires, performs the **sole** side effect: ``store.enqueue_review``. Adding a
fifth condition (or any dynamic/ad-hoc registration path) requires a spec
change (schema-freeze discipline, §4.10) -- this module deliberately offers
none, and is *not* a script runner (§8).

Conditions:

* ``all_subtasks_closed`` -- true when a supertask (a ``task`` node) has at
  least one live ``composes`` child that is itself a task, and every such
  task-child is ``task_state == "done"``. Its action enqueues one
  ``subtasks_closed`` review on the supertask; it **never** sets the
  supertask's own ``task_state`` (spec §4.10, §9 story 8) -- flagging for
  human review is the only allowed action.
* ``facet_interface_changed`` -- built-in, implemented *as* §4.9: delegates
  entirely to ``tms.invalidate.invalidate`` (no reimplementation of the
  subscriber walk). Fires whenever the event carries a commit with a
  non-empty touched-facet set; the action *is* the ``invalidate`` walk,
  which enqueues ``facet_break`` reviews on affected subscribers (not
  necessarily the node passed to ``evaluate`` itself) and owns its own
  non-transitive damper.
* ``evidence_retracted`` -- fires when a live justification edge
  (``supports|contradicts|depends_on|derived_from|cites``, spec §4.2) whose
  ``dst`` is the node was just retracted. There is no distinct "evidence"
  edge_type in the model (``evidence`` is a node_type, spec §4.2); the
  narrowest reading is "any justification edge into this node was
  retracted", logged as informational only (not blocking).
* ``recheck_after`` -- params: an ISO date + an opaque period label,
  carried in the event context. Fires once the context's "now" instant has
  reached or passed the recheck date.

Both ``evaluate`` (per-commit entry point, per §4.10(a): "after every commit
touching the node or its children") and ``run_daily_tick`` (§4.10(b)) are
standalone public functions. Wiring either into the commit path
(``kernel/commits.py``, ``kernel/store.py::commit_node``) or a daemon tick
loop is deferred to a later, separate task -- exactly as T7.1 left
``invalidate``'s commit-path wiring to T7.2; this task's Files list is
``tms/triggers.py`` + its unit test only.

# SPEC-QUESTION: §4.10 gives `recheck_after` "params: an ISO date, period"
# but the schema (§4.4 DDL) has no column/table for a per-node recheck
# schedule, and this task's Files list is `tms/triggers.py` only (no
# migration). Narrowest reading adopted here: the ISO date/period are
# carried transiently in the caller-supplied `TriggerContext` for a given
# `evaluate`/`run_daily_tick` call rather than invented as new persisted
# state; sourcing per-node recheck schedules for a real daily-tick sweep
# (where do the date/period live between ticks?) is left to whichever
# later task wires `run_daily_tick` into the daemon. Non-blocking; logged
# under task T7.3 in docs/spec-questions.md.
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
    """Event context handed to every condition for one ``evaluate`` call (spec §4.10).

    Only the fields relevant to the event that triggered evaluation need be
    populated by the caller; a condition whose relevant field is absent
    simply evaluates to ``False`` (nothing in this event concerns it).
    ``children`` is never set by the caller -- ``evaluate`` (re)computes it
    from the live store on every call so ``all_subtasks_closed`` always
    sees the current subtask set, not a caller-supplied snapshot that could
    go stale.
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
    """Run every registered condition against ``node_id`` for one event (spec §4.10(a)).

    Intended entry point for "after every commit touching the node or its
    children" -- the caller (a later task's commit-path wiring) constructs a
    ``TriggerContext`` describing the event (commit hash + touched facets,
    a just-retracted edge, or a recheck schedule) and calls this function.
    Standalone: not wired into ``commit_node``/the daemon tick loop by this
    task. Returns the concatenation of every newly-enqueued review row
    (``store.enqueue_review``'s return shape); an unaffected call returns
    ``[]``. The sole side effect across every condition's action is
    ``store.enqueue_review`` -- no condition ever mutates node/edge/task
    state.
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
    """Run ``evaluate`` for every node in ``contexts`` (spec §4.10(b): daily tick).

    ``contexts`` maps ``node_id -> TriggerContext`` (e.g. a recheck-due node
    with ``recheck_date``/``recheck_period`` populated). Sourcing which
    nodes need a daily sweep and their recheck schedules is the caller's
    job (the future daemon daily-tick driver, deferred per this task's
    scope -- see the module-level SPEC-QUESTION); this function only
    iterates and evaluates, mirroring how ``evaluate`` is the standalone
    per-commit entry point later wiring will call.
    """
    enqueued: list[dict[str, Any]] = []
    for node_id, ctx in contexts.items():
        enqueued.extend(evaluate(conn, node_id, ctx))
    return enqueued
