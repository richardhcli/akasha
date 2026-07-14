"""Commit-facing helpers: facets_touched + change-class heuristic (tasks T1.6, T7.2).

This module contains small, pure, standalone helpers over ``Facet``
lists plus a handful of unrelated pure formatting helpers used by the
sync conflict path (added by T5.5, see below):

- ``facets_touched(old_facets, new_facets)`` — which facet_ids changed.
- ``default_change_class(old_facets, new_facets, facets_touched=None)``
  — the full §4.9 heuristic default: ``"major"`` iff a facet was
  removed/renamed, or a facet in the (optionally caller-supplied)
  ``facets_touched`` set had its ``version`` bumped; otherwise
  ``"patch"``. This function never returns ``"minor"`` -- spec §4.9 only
  names ``major`` (the interface-break trigger) and the residual
  "least-invasive class the commit warrants" for everything else, which
  this module (and the build plan's Goal/DoD for both T1.6 and T7.2)
  reads as ``"patch"``; nothing in §4.9's default-heuristic clause ever
  selects ``"minor"`` -- that class exists only for an explicit UI/CLI
  choice (e.g. a brand-new facet addition, spec §7.7's mint-facet flow),
  which always OVERRIDES this default rather than going through it.

"Node retraction is always major touching all facets" (spec §4.9) is
NOT reimplemented here as a separate code path: T7.1's ``invalidate()``
already flags every bound subscriber (including wildcard-bound) when
handed ``touched`` equal to a node's full facet-id set (see
``tests/unit/tms/test_invalidate.py::test_all_facets_touched_flags_every_bound_subscriber``),
and T7.2's wiring (below / ``store.commit_node``) triggers that walk for
ANY commit whose *actual* ``change_class`` is ``"major"`` regardless of
whether that value came from this heuristic or an explicit override --
so a caller representing a node retraction as a major commit that
touches every one of the node's facets gets the exact §4.9 behavior for
free, with no bespoke "retraction" function needed.

This module performs no database access and imports nothing from
``store.py`` (mirrors ``maturity.py``'s pure-function discipline).

# SPEC-QUESTION (T1.6, superseded by T7.2): the task boundary between
# this module's heuristic and T7.2's full commit wiring was stated in
# the build plan only as "narrow heuristic (major iff a facet
# removed/renamed or a facet version bumped, else patch)". T7.2 confirms
# that reading is already the FULL §4.9 default-heuristic clause (the
# spec's only other classes -- "minor" and the retraction rule -- are
# not reached via this heuristic at all, per the module docstring
# above), so no further narrowing was needed here. See
# docs/spec-questions.md entry for T1.6.
"""

from __future__ import annotations

from typing import Any, Iterable

from akasha.kernel.model import ChangeClass, Facet


def facets_touched(old_facets: list[Facet], new_facets: list[Facet]) -> list[str]:
    """Return the sorted list of facet_ids that changed between two facet lists.

    Invariant: compares by ``facet_id``. A facet_id is "touched" iff it is
    (a) present in ``new_facets`` but not ``old_facets`` (added), (b)
    present in ``old_facets`` but not ``new_facets`` (removed), (c)
    present in both but with a different ``name`` (renamed), or (d)
    present in both but with a strictly greater ``version`` in
    ``new_facets`` (version-bumped). A facet whose ONLY change is its
    ``span`` is NOT touched — narrowest reading, since span is a
    source-location detail, not one of the four categories the build-plan
    Goal names ("added/removed/renamed/version-bumped"); see the
    module-level SPEC-QUESTION. Pure function: does not mutate either
    argument.
    """
    old_by_id = {f.facet_id: f for f in old_facets}
    new_by_id = {f.facet_id: f for f in new_facets}
    touched: set[str] = set()
    for facet_id in old_by_id.keys() | new_by_id.keys():
        old_f = old_by_id.get(facet_id)
        new_f = new_by_id.get(facet_id)
        if old_f is None or new_f is None:
            touched.add(facet_id)
        elif old_f.name != new_f.name or new_f.version > old_f.version:
            touched.add(facet_id)
    return sorted(touched)


def default_change_class(
    old_facets: list[Facet],
    new_facets: list[Facet],
    facets_touched_ids: Iterable[str] | None = None,
) -> ChangeClass:
    """Default change-class heuristic (spec §4.9's "heuristic default" clause, task T7.2).

    Invariant: returns ``"major"`` iff EITHER (a) at least one facet
    present in ``old_facets`` was removed (absent from ``new_facets``) or
    renamed (same ``facet_id``, different ``name`` — checked over every
    old facet, not merely the ``facets_touched_ids`` set, since spec
    §4.9 states this clause unconditionally: "a facet was
    removed/renamed"), OR (b) a facet whose id is in
    ``facets_touched_ids`` had its ``version`` strictly bumped between
    ``old_facets`` and ``new_facets`` (spec §4.9: "a *touched* facet's
    version was bumped" — this clause alone is scoped to the caller's
    touched set). Returns ``"patch"`` otherwise (including when facets
    are only added, or unchanged, or a version bump happened on a facet
    NOT listed in ``facets_touched_ids``).

    ``facets_touched_ids`` defaults to ``None``, in which case it is
    computed as ``facets_touched(old_facets, new_facets)`` (this
    module's own diff) — this is 100% backward compatible with T1.6's
    original 2-positional-argument call sites (every facet that could
    possibly have a version bump is, by ``facets_touched``'s own
    definition, already included in that computed set, so passing no
    explicit ``facets_touched_ids`` reproduces T1.6's original
    all-old-facets version-bump check exactly).

    This function never returns ``"minor"`` and never implements the
    separate "node retraction is always major" rule — see the module
    docstring for why neither is needed here. Pure function: does not
    mutate any argument.
    """
    old_by_id = {f.facet_id: f for f in old_facets}
    new_by_id = {f.facet_id: f for f in new_facets}
    for facet_id, old_f in old_by_id.items():
        new_f = new_by_id.get(facet_id)
        if new_f is None:
            return "major"  # removed
        if new_f.name != old_f.name:
            return "major"  # renamed

    touched_ids = (
        set(facets_touched_ids)
        if facets_touched_ids is not None
        else set(facets_touched(old_facets, new_facets))
    )
    for facet_id in touched_ids:
        old_f = old_by_id.get(facet_id)
        new_f = new_by_id.get(facet_id)
        if old_f is not None and new_f is not None and new_f.version > old_f.version:
            return "major"  # touched facet's version was bumped

    return "patch"


def conflict_branch_message(path: str) -> str:
    """The fixed ``commits.message`` a conflict-branch commit carries (task T5.5).

    Pure string formatting only -- no DB access. ``sync/reconcile.py``'s
    conflict handler passes the result straight to
    ``kernel/store.py::record_conflict_branch``'s ``message`` kwarg; a
    branch commit is always identifiable by this exact prefix
    (``message.startswith("conflict-branch:")``), which
    ``tests/integration/test_conflict.py`` asserts on directly.
    """
    return f"conflict-branch: {path}"


def conflict_cause_ref(
    *,
    path: str,
    vault_text: str | None,
    vault_task_state: str | None,
    base_text: str | None,
    branch_commit: str | None,
) -> dict[str, Any]:
    """Build the (not-yet-serialized) dict for a conflict review's ``cause_ref`` (task T5.5).

    Pure function -- the caller (``sync/reconcile.py``'s conflict handler)
    is responsible for ``kernel.canonical.canonical_json()``-encoding the
    returned dict into deterministic bytes; this module never touches JSON
    encoding or the DB itself (mirrors ``facets_touched``/
    ``default_change_class``'s pure-function discipline above).
    Deterministic bytes are required so
    ``kernel/store.py::find_open_reviews``'s exact-``cause_ref``-match gate
    can dedup a replayed conflict (task T5.6 crash-replay) without a
    second review item or a second branch. Extends T5.4's
    ``_default_conflict_handler`` cause_ref shape (``path``, ``vault_text``,
    ``vault_task_state``, ``base_text``) with the new ``branch_commit`` key
    -- the existing three keys are preserved verbatim so
    ``tests/unit/sync/test_reconcile.py``'s pre-existing conflict test stays
    green. ``branch_commit`` is ``None`` for a deleted-op conflict (the
    vault removed the anchor; there is no vault version left to branch --
    see ``sync/reconcile.py::conflict_branch_handler``'s docstring and the
    logged SPEC-QUESTION).
    """
    return {
        "path": path,
        "vault_text": vault_text,
        "vault_task_state": vault_task_state,
        "base_text": base_text,
        "branch_commit": branch_commit,
    }
