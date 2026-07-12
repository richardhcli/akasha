"""Commit-facing helpers: facets_touched + change-class heuristic (task T1.6).

This module intentionally contains ONLY two small, pure, standalone
helpers over ``Facet`` lists:

- ``facets_touched(old_facets, new_facets)`` — which facet_ids changed.
- ``default_change_class(old_facets, new_facets)`` — a NARROW heuristic
  slice of spec §4.9's change-class rule (major-iff-removed/renamed/
  version-bumped, else patch).

The FULL §4.9 heuristic (which also covers "minor" classification and
"node retraction is always major touching all facets") plus wiring this
into ``store.commit_node``'s default ``change_class``/``facets_touched``
arguments is T7.2's job — deliberately NOT built here (build-plan T1.6
Steps: "do NOT build that here, just the standalone helpers"). Callers
that want the full commit-wiring behavior should wait for T7.2; callers
here get exactly the two pure functions.

This module performs no database access and imports nothing from
``store.py`` (mirrors ``maturity.py``'s pure-function discipline).

# SPEC-QUESTION (T1.6): the task boundary between this module's narrow
# helpers and T7.2's full commit wiring is stated in the build plan only
# as "narrow heuristic (major iff a facet removed/renamed or a facet
# version bumped, else patch)" — implemented exactly that literally below.
# "minor" classification and the retraction-is-always-major rule (spec
# §4.9) are left entirely to T7.2. See docs/spec-questions.md entry for
# T1.6.
"""

from __future__ import annotations

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


def default_change_class(old_facets: list[Facet], new_facets: list[Facet]) -> ChangeClass:
    """Narrow default change-class heuristic (spec §4.9's "heuristic default" clause).

    Invariant: returns ``"major"`` iff at least one facet present in
    ``old_facets`` was removed (absent from ``new_facets``), renamed
    (same ``facet_id``, different ``name``), or had its ``version``
    strictly bumped in ``new_facets``; returns ``"patch"`` otherwise
    (including when facets are only added, or unchanged). This function
    never returns ``"minor"`` — that classification, and the separate
    "node retraction is always major" rule, belong to spec §4.9's full
    heuristic, which is T7.2's job (see module docstring). Pure function:
    does not mutate either argument.
    """
    old_by_id = {f.facet_id: f for f in old_facets}
    new_by_id = {f.facet_id: f for f in new_facets}
    for facet_id, old_f in old_by_id.items():
        new_f = new_by_id.get(facet_id)
        if new_f is None:
            return "major"  # removed
        if new_f.name != old_f.name:
            return "major"  # renamed
        if new_f.version > old_f.version:
            return "major"  # version-bumped
    return "patch"
