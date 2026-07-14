"""Trigger registry + evaluator tests (task T7.3, spec §4.10).

Holistic coverage: each of the four registered conditions fires under its
positive scenario and does not fire under a negative one;
``all_subtasks_closed`` enqueues exactly one review, never auto-closes the
supertask, and is idempotent on re-evaluation; the sole side effect of a
firing condition is ``store.enqueue_review`` (verified by snapshotting
every other table before/after); and the registry is closed to ad-hoc
additions.
"""

from types import MappingProxyType

import pytest

from akasha.kernel import store
from akasha.kernel.model import Facet
from akasha.tms import triggers


def _fresh_conn(tmp_path):
    conn = store.connect(tmp_path / "triggers.db")
    store.run_migrations(conn)
    return conn


def _facet(facet_id: str, name: str, version: int = 1) -> Facet:
    return Facet(facet_id=facet_id, name=name, span=name, version=version)


_OTHER_TABLES = ("nodes", "edges", "commits", "objects")


def _snapshot(conn):
    return {t: conn.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall() for t in _OTHER_TABLES}


# --------------------------------------------------------------------------
# all_subtasks_closed
# --------------------------------------------------------------------------


def test_all_subtasks_closed_fires_when_every_child_done(tmp_path):
    conn = _fresh_conn(tmp_path)
    supertask = store.create_node(conn, "task", "supertask", task_state="open")
    child1 = store.create_node(conn, "task", "child-1", task_state="done")
    child2 = store.create_node(conn, "task", "child-2", task_state="done")
    for child in (child1, child2):
        store.create_edge(
            conn,
            src=supertask.id,
            dst=child.id,
            edge_type="composes",
            facet_binding=None,
            provenance="human",
        )

    before = _snapshot(conn)
    result = triggers.evaluate(conn, supertask.id, triggers.TriggerContext(now="2026-07-14"))
    after = _snapshot(conn)

    assert len(result) == 1
    assert result[0]["cause_kind"] == "subtasks_closed"
    assert result[0]["node_id"] == supertask.id

    # sole side effect: no node/edge/commit/object row changed.
    assert before == after

    # never auto-closed.
    reloaded = store.get_node(conn, supertask.id)
    assert reloaded.task_state == "open"

    open_reviews = store.find_open_reviews(conn, node_id=supertask.id, cause_kind="subtasks_closed")
    assert len(open_reviews) == 1

    # idempotent: re-evaluating enqueues no duplicate.
    second = triggers.evaluate(conn, supertask.id, triggers.TriggerContext(now="2026-07-14"))
    assert second == []
    open_reviews_again = store.find_open_reviews(
        conn, node_id=supertask.id, cause_kind="subtasks_closed"
    )
    assert len(open_reviews_again) == 1

    reloaded_again = store.get_node(conn, supertask.id)
    assert reloaded_again.task_state == "open"


def test_all_subtasks_closed_does_not_fire_when_a_child_is_open(tmp_path):
    conn = _fresh_conn(tmp_path)
    supertask = store.create_node(conn, "task", "supertask", task_state="open")
    child1 = store.create_node(conn, "task", "child-1", task_state="done")
    child2 = store.create_node(conn, "task", "child-2", task_state="open")
    for child in (child1, child2):
        store.create_edge(
            conn,
            src=supertask.id,
            dst=child.id,
            edge_type="composes",
            facet_binding=None,
            provenance="human",
        )

    result = triggers.evaluate(conn, supertask.id, triggers.TriggerContext(now="2026-07-14"))

    assert result == []
    assert store.find_open_reviews(conn, node_id=supertask.id, cause_kind="subtasks_closed") == []


def test_all_subtasks_closed_does_not_fire_for_non_task_node(tmp_path):
    conn = _fresh_conn(tmp_path)
    parent = store.create_node(conn, "entity", "parent")  # not a task (task_state is None)
    child = store.create_node(conn, "task", "child", task_state="done")
    store.create_edge(
        conn,
        src=parent.id,
        dst=child.id,
        edge_type="composes",
        facet_binding=None,
        provenance="human",
    )

    result = triggers.evaluate(conn, parent.id, triggers.TriggerContext(now="2026-07-14"))

    assert result == []


# --------------------------------------------------------------------------
# facet_interface_changed (delegates to tms.invalidate, spec §4.9)
# --------------------------------------------------------------------------


def test_facet_interface_changed_fires_and_flags_subscriber(tmp_path):
    conn = _fresh_conn(tmp_path)
    target = store.create_node(
        conn, "definition", "target", facets=[_facet("f1", "one"), _facet("f2", "two")]
    )
    sub = store.create_node(conn, "claim", "subscriber")
    store.create_edge(
        conn,
        src=sub.id,
        dst=target.id,
        edge_type="supports",
        facet_binding="f1",
        provenance="human",
    )

    before = _snapshot(conn)
    ctx = triggers.TriggerContext(now="2026-07-14", commit="commit-1", touched=frozenset({"f1"}))
    result = triggers.evaluate(conn, target.id, ctx)
    after = _snapshot(conn)

    assert len(result) == 1
    assert result[0]["node_id"] == sub.id
    assert result[0]["cause_kind"] == "facet_break"
    assert before == after

    open_reviews = store.find_open_reviews(conn, node_id=sub.id, cause_kind="facet_break")
    assert len(open_reviews) == 1


def test_facet_interface_changed_does_not_fire_when_no_facets_touched(tmp_path):
    conn = _fresh_conn(tmp_path)
    target = store.create_node(conn, "definition", "target", facets=[_facet("f1", "one")])
    sub = store.create_node(conn, "claim", "subscriber")
    store.create_edge(
        conn,
        src=sub.id,
        dst=target.id,
        edge_type="supports",
        facet_binding="f1",
        provenance="human",
    )

    ctx = triggers.TriggerContext(now="2026-07-14", commit="commit-1", touched=frozenset())
    result = triggers.evaluate(conn, target.id, ctx)

    assert result == []
    assert store.find_open_reviews(conn, node_id=sub.id, cause_kind="facet_break") == []


# --------------------------------------------------------------------------
# evidence_retracted
# --------------------------------------------------------------------------


def test_evidence_retracted_fires_when_justification_edge_retracted(tmp_path):
    conn = _fresh_conn(tmp_path)
    claim = store.create_node(conn, "claim", "claim body")
    evidence = store.create_node(conn, "evidence", "evidence body")
    edge = store.create_edge(
        conn,
        src=evidence.id,
        dst=claim.id,
        edge_type="supports",
        facet_binding="*",
        provenance="human",
    )
    store.retract_edge(conn, edge.id)

    before = _snapshot(conn)
    result = triggers.evaluate(
        conn, claim.id, triggers.TriggerContext(now="2026-07-14", retracted_edge=edge)
    )
    after = _snapshot(conn)

    assert len(result) == 1
    assert result[0]["cause_kind"] == "evidence_retracted"
    assert result[0]["node_id"] == claim.id
    assert result[0]["cause_ref"] == edge.id
    assert before == after

    open_reviews = store.find_open_reviews(conn, node_id=claim.id, cause_kind="evidence_retracted")
    assert len(open_reviews) == 1

    # idempotent: a second evaluation with the same retracted edge enqueues no duplicate.
    second = triggers.evaluate(
        conn, claim.id, triggers.TriggerContext(now="2026-07-14", retracted_edge=edge)
    )
    assert second == []


def test_evidence_retracted_does_not_fire_for_unrelated_node(tmp_path):
    conn = _fresh_conn(tmp_path)
    claim = store.create_node(conn, "claim", "claim body")
    other = store.create_node(conn, "claim", "other claim")
    evidence = store.create_node(conn, "evidence", "evidence body")
    edge = store.create_edge(
        conn,
        src=evidence.id,
        dst=claim.id,
        edge_type="supports",
        facet_binding="*",
        provenance="human",
    )
    store.retract_edge(conn, edge.id)

    # evaluating a node the retracted edge doesn't point at: no fire.
    result = triggers.evaluate(
        conn, other.id, triggers.TriggerContext(now="2026-07-14", retracted_edge=edge)
    )

    assert result == []


def test_evidence_retracted_does_not_fire_without_a_retracted_edge_in_context(tmp_path):
    conn = _fresh_conn(tmp_path)
    claim = store.create_node(conn, "claim", "claim body")

    result = triggers.evaluate(conn, claim.id, triggers.TriggerContext(now="2026-07-14"))

    assert result == []


# --------------------------------------------------------------------------
# recheck_after
# --------------------------------------------------------------------------


def test_recheck_after_fires_once_recheck_date_has_passed(tmp_path):
    conn = _fresh_conn(tmp_path)
    node = store.create_node(conn, "claim", "some claim")

    before = _snapshot(conn)
    result = triggers.evaluate(
        conn,
        node.id,
        triggers.TriggerContext(
            now="2026-07-14", recheck_date="2026-07-01", recheck_period="P90D"
        ),
    )
    after = _snapshot(conn)

    assert len(result) == 1
    assert result[0]["cause_kind"] == "recheck"
    assert result[0]["node_id"] == node.id
    assert result[0]["cause_ref"] == "2026-07-01"
    assert before == after

    open_reviews = store.find_open_reviews(conn, node_id=node.id, cause_kind="recheck")
    assert len(open_reviews) == 1


def test_recheck_after_does_not_fire_before_recheck_date(tmp_path):
    conn = _fresh_conn(tmp_path)
    node = store.create_node(conn, "claim", "some claim")

    result = triggers.evaluate(
        conn,
        node.id,
        triggers.TriggerContext(
            now="2026-07-14", recheck_date="2026-12-01", recheck_period="P90D"
        ),
    )

    assert result == []
    assert store.find_open_reviews(conn, node_id=node.id, cause_kind="recheck") == []


def test_recheck_after_does_not_fire_without_a_recheck_date(tmp_path):
    conn = _fresh_conn(tmp_path)
    node = store.create_node(conn, "claim", "some claim")

    result = triggers.evaluate(conn, node.id, triggers.TriggerContext(now="2026-07-14"))

    assert result == []


# --------------------------------------------------------------------------
# registry closure
# --------------------------------------------------------------------------


def test_registry_exposes_exactly_the_four_named_conditions():
    assert set(triggers.CONDITIONS) == {
        "all_subtasks_closed",
        "facet_interface_changed",
        "evidence_retracted",
        "recheck_after",
    }


def test_registry_is_immutable_mapping_with_no_dynamic_registration_path():
    assert isinstance(triggers.CONDITIONS, MappingProxyType)
    with pytest.raises(TypeError):
        triggers.CONDITIONS["a_fifth_condition"] = lambda node, ctx: True  # type: ignore[index]

    # no ad-hoc registration API exposed by the module.
    assert not hasattr(triggers, "register")
    assert not hasattr(triggers, "register_condition")
    assert not hasattr(triggers, "add_condition")


def test_run_daily_tick_evaluates_every_supplied_node(tmp_path):
    conn = _fresh_conn(tmp_path)
    due = store.create_node(conn, "claim", "due claim")
    not_due = store.create_node(conn, "claim", "not due claim")

    contexts = {
        due.id: triggers.TriggerContext(now="2026-07-14", recheck_date="2026-07-01"),
        not_due.id: triggers.TriggerContext(now="2026-07-14", recheck_date="2026-12-01"),
    }

    result = triggers.run_daily_tick(conn, contexts)

    assert len(result) == 1
    assert result[0]["node_id"] == due.id
    assert result[0]["cause_kind"] == "recheck"
