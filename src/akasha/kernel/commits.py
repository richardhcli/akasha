"""Commit-facing pure helpers: ``facets_touched``, the change-class heuristic, and the
conflict-branch message and ``cause_ref`` builders. No database access and no import from
``store.py``.

``default_change_class`` is the full §4.9 heuristic default: ``"major"`` iff a facet was removed or
renamed, or a touched facet's ``version`` was bumped; otherwise ``"patch"``. It never returns
``"minor"`` (that class exists only for an explicit UI/CLI choice, e.g. minting a facet, and always
overrides this default). "Node retraction is always major touching all facets" needs no code here:
``invalidate()`` already flags every bound subscriber when handed a node's full facet set, and
``store.commit_node`` runs it for any commit whose actual ``change_class`` is ``"major"``.

# SPEC-QUESTION (T1.6, superseded by T7.2): the boundary between this heuristic and the full
# commit wiring was stated only as "major iff a facet removed/renamed or a version bumped, else
# patch"; T7.2 confirmed that is the whole §4.9 default clause. See docs/spec-questions.md T1.6.
"""

from __future__ import annotations

from typing import Any, Iterable

from akasha.kernel.model import ChangeClass, Facet


def facets_touched(old_facets: list[Facet], new_facets: list[Facet]) -> list[str]:
    """Sorted facet ids that changed between two facet lists, compared by ``facet_id``: added,
    removed, renamed (different ``name``), or version-bumped (strictly greater). A ``span``-only
    change is NOT a touch (a source-location detail; narrowest reading of the four categories).
    Pure.
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
    """Default change class (spec §4.9's heuristic clause, T7.2): ``"major"`` iff a facet of
    ``old_facets`` was removed or renamed (checked over ALL old facets, since §4.9 states that
    unconditionally) or a facet in ``facets_touched_ids`` had its version strictly bumped; else
    ``"patch"`` (additions, unchanged facets, or a bump outside the touched set).

    ``facets_touched_ids`` defaults to :func:`facets_touched` of the two lists, which reproduces
    the original two-argument behaviour. Never returns ``"minor"``; retraction needs no special
    case (see the module docstring). Pure.
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
    """The fixed ``commits.message`` of a conflict-branch commit (T5.5): a branch is always
    identifiable by the ``"conflict-branch:"`` prefix. Pure formatting.
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
    """The (unserialized) dict for a conflict review's ``cause_ref`` (T5.5). Pure.

    The caller ``canonical_json``-encodes it: the bytes must be deterministic so
    ``find_open_reviews``'s exact-match gate can dedup a replayed conflict (T5.6) without a second
    review or branch. It extends T5.4's shape (``path``, ``vault_text``, ``vault_task_state``,
    ``base_text``, kept verbatim) with ``branch_commit``, which is ``None`` for a deleted-op
    conflict (no vault version left to branch; see ``reconcile.conflict_branch_handler``).
    """
    return {
        "path": path,
        "vault_text": vault_text,
        "vault_task_state": vault_task_state,
        "base_text": base_text,
        "branch_commit": branch_commit,
    }
