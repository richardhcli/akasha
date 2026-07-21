"""Reconcile pipeline: the §4.8 per-file three-way merge (build-plan task T5.4).

This is the algorithmic core of the sync engine — the ``on_change(path)``
pipeline that reconciles a managed vault file's current text (``V``) against
the last-agreed base (``B``) and the hub's current projection (``H``),
applying certain-repairs, computing ops keyed by anchor id, resolving
per-node conflicts, and writing back a canonical, converged file.

Design provenance
------------------
An architecture review (the ``fable`` model) resolved every ambiguity this
task's own spec section (§4.8, plus §4.3/§4.5/§4.6/§4.7) left open; those
resolutions were HUMAN-DECIDED on 2026-07-12 (aligned with fable) and are
implemented here as-is, marked inline with
``# design note (T5.4, fable-reviewed, human-decided 2026-07-12): ...``
rather than as open ``SPEC-QUESTION`` markers — they are not up for
re-litigation.

Module layout (mirrors the fable implementation order)
--------------------------------------------------------
1. ``Op`` / ``DiffOutcome`` / ``ReconcileReviewItem`` — pure pydantic result
   shapes.
2. ``apply_repairs`` / ``diff_blocks`` / ``_compute_ops`` — the pure,
   zero-I/O layer (no DB, no filesystem): given already-parsed
   :class:`~akasha.contract.parser.BlockSet` values and a pure
   maturity/projection lookup, compute the ops table. This is the bulk of
   the unit-test surface.
3. ``ProjectionIndex`` — an in-memory, rebuildable id -> owning-path map
   used for cross-file ``E_DUP_ID``/move-detection (the M3 T3.5/T3.6
   follow-up logged against M5 in ``docs/agents/task-status.md``).
4. ``hub_state_for`` / ``hub_changed_since`` / ``kernel_apply`` — the
   store-facing (I/O) primitives. Every write goes through
   ``kernel/store.py`` (rule 0.4); this module never touches SQLite
   directly.
5. ``Reconciler`` — the wiring class: sync-root resolution, the full
   ``on_change`` pipeline in spec §4.8's pseudocode order, conflict
   persistence (a swappable seam for T5.5), pause&diff persistence, and
   canonical write-back with echo recording.

Note on wiring: this task does not wire ``Reconciler`` into the live
``Watcher``/daemon — that lands with T5.6. ``Reconciler.on_change`` has the
exact ``Callable[[str], None]`` shape ``sync.watcher.Watcher``'s
``on_cycle`` parameter expects (see that module's docstring), so T5.6 only
needs to construct a ``Reconciler`` and pass its bound ``on_change`` method.
"""

from __future__ import annotations

import bisect
import logging
import os
import secrets
import sqlite3
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from pydantic import BaseModel

from akasha import metrics
from akasha.contract import grammar, linter
from akasha.contract.linter import LintResult, MaturityLookup, Repair
from akasha.contract.parser import Block, BlockSet, NewRequest, parse
from akasha.contract.render import render
from akasha.kernel import commits, store
from akasha.kernel.canonical import canonical_json, canonicalize_text, object_hash
from akasha.kernel.ids import contract_anchor
from akasha.kernel.model import Maturity
from akasha.sync import base_store
from akasha.sync.watcher import detect_cloud_path, retry_with_backoff

if TYPE_CHECKING:
    from collections.abc import Callable

    from akasha.sync.origin import OriginTracker

logger = logging.getLogger("akasha")

# design note (T5.4, fable-reviewed, human-decided 2026-07-12) -- DECIDED
# gap #1: the node_type minted for a `^tm-new` paragraph (non-task) block.
# spec §4.2's NodeType has no generic "note"/"paragraph" member; "claim" is
# the closest existing type for free-standing managed prose and is used as
# a fixed module constant (a swappable seam, not a guess re-derived per
# call site).
PARAGRAPH_NODE_TYPE: Literal["claim"] = "claim"

# design note (T5.4, fable-reviewed, human-decided 2026-07-12) -- DECIDED
# gap #2: the change_class used for every sync-authored commit_node call.
# "patch" is the least-invalidating class (spec §4.9's invalidation walk
# only triggers on "major"), appropriate for a vault edit that is not yet
# heuristically classified. This constant is a classifier SEAM: M7/T7.2
# ("change-class heuristic + wiring into commit") replaces it with a real
# heuristic call; nothing else in this module should be changed to adopt
# that later.
SYNC_CHANGE_CLASS: Literal["patch"] = "patch"

# The reserved author literal for every sync-originated store write (spec
# §4.8: "kernel.apply(op) # via store API, origin='sync'"; mirrors the
# existing author="system" literal already used by create_node's default).
SYNC_AUTHOR = "sync"


# --- pure result models -------------------------------------------------------


class Op(BaseModel):
    """One reconcile-pipeline operation, keyed by anchor id (spec §4.8).

    ``kind`` is one of the six spec §4.8 op kinds:
    modified | created | deleted | moved | checkbox_toggled | reparented.

    ``vault_block``/``base_block`` are the parsed :class:`Block` on each
    side (``None`` where not applicable — e.g. ``base_block`` is always
    ``None`` for a ``created`` op, ``vault_block`` is always ``None`` for a
    ``deleted`` op). ``new_request`` is set only for a ``created`` op
    sourced from a literal ``^tm-new`` marker (as opposed to a cross-file
    adopted anchor, spec §7's E04, which sets ``node_id`` instead).

    ``parent_id`` is a deliberate, minimal extension beyond the four core
    fields fable's design lists verbatim
    (``Op(kind, node_id, vault_block, base_block, new_request)``): spec
    point 4 requires a freshly-minted `^tm-new` task to wire a
    ``composes(parent->child)`` edge "if a parent task at its depth"
    exists, but nothing else in the pipeline carries that parent id
    forward from ops-computation time (where the full document order is
    available) to apply time (``kernel_apply``, which only sees one ``Op``
    at a time). ``Op`` is this module's own in-memory pipeline type (not a
    persisted schema), so this is a documented, justified addition, not an
    invented schema/endpoint/grammar element.
    """

    kind: Literal["modified", "created", "deleted", "moved", "checkbox_toggled", "reparented"]
    node_id: str | None
    vault_block: Block | None = None
    base_block: Block | None = None
    new_request: NewRequest | None = None
    parent_id: str | None = None


class ReconcileReviewItem(BaseModel):
    """A reconcile-level review annotation not expressible via ``linter.ViolationCode``.

    ``linter.py`` (not in this task's Files list) freezes ``ViolationCode``
    to the five §4.7 codes; cross-file classification (spec §7 -- the M3
    T3.5/T3.6 follow-up logged against M5) needs two findings that fall
    outside that closed set:

    - an EOL anchor whose id is syntactically valid (checksum passes) but
      corresponds to no node the kernel has ever heard of ("unknown
      anchor" -- distinct from ``E_ID_CHECKSUM``, which is a checksum
      *failure*), and
    - a cross-file ``E_DUP_ID`` (the SAME anchor id live in two different
      managed files at once -- distinct from single-file ``E_DUP_ID``,
      which ``linter.py`` already detects).

    ``code`` is a free-form string (not the frozen ``ViolationCode``
    Literal) since these are reconcile-level findings, persisted via
    ``store.enqueue_review``'s free-text ``cause_ref`` JSON, never
    round-tripped back through ``linter.LintResult``.
    """

    id: str | None
    code: str
    message: str
    line_nos: list[int] = []


class DiffOutcome(BaseModel):
    """Result of :func:`diff_blocks`: the pure ops table plus lint findings."""

    ops: list[Op]
    lint: LintResult
    repaired_text: str
    extra_review_items: list[ReconcileReviewItem] = []


# --- apply_repairs (zero-I/O) --------------------------------------------------


def apply_repairs(text: str, repairs: list[Repair]) -> str:
    """Apply every certain-repair (spec §4.7) to ``text``, returning the result.

    Pure string transform: splits ``text`` on ``"\\n"`` (matching
    ``contract.parser``'s line-number convention -- ``Repair.line_no`` is
    1-indexed into the full file including front matter), and for each
    repair whose recorded ``before`` still matches the line at
    ``line_no - 1`` verbatim, replaces it with ``after``. A repair whose
    ``before`` no longer matches (e.g. two repairs computed against the
    same stale snapshot happen to target overlapping content) is skipped
    rather than guessed -- this should not happen in practice since
    ``linter.lint`` computes every repair against the SAME vault text, but
    defends against ever corrupting a line the repair wasn't actually
    computed for.
    """
    if not repairs:
        return text
    lines = text.split("\n")
    for repair in repairs:
        idx = repair.line_no - 1
        if 0 <= idx < len(lines) and lines[idx] == repair.before:
            lines[idx] = repair.after
    return "\n".join(lines)


# --- stable-order helper (zero-I/O) ---------------------------------------------


def _stable_order_ids(b_order: list[str], v_order: list[str]) -> set[str]:
    """Return the STABLE (non-moved) id set -- an O(n log n) replacement for the old O(n*m) LCS DP.

    Fixes the E20 5,000-block perf bug (build-plan T5.8-1, fable-reviewed,
    human-decided 2026-07-12): the previous ``_lcs_ids`` helper (an O(n*m)
    longest-common-subsequence DP, now removed -- its exact behavior is
    preserved as the ``_lcs_oracle`` reference function in
    ``tests/unit/sync/test_reconcile.py``, Hypothesis-verified against this
    function) ran over the ~5,000 *stable* common ids EVERY reconcile
    cycle, even when nothing moved (~15s wall time vs spec §6.2's <2s
    budget). ``b_order``/``v_order`` are both filtered to the SAME id set
    (``stable_ids ⊆ common``, see ``_compute_ops``), i.e. permutations of
    one another -- so their longest-common-subsequence is exactly their
    longest INCREASING subsequence (LIS) once ``v_order`` is remapped
    through each id's position in ``b_order``. This function is REQUIRED
    to return the exact same set the old LCS DP would (verified by the
    Hypothesis oracle test against random permutations) since the
    moved-op set it drives is pinned byte-identical by golden fixtures
    (E03/E17).

    Fast path: if the two orders are already identical (the common case --
    nothing moved), every id is trivially stable, O(n) with zero DP/LIS
    work at all.

    General case: O(n log n) patience-sort LIS over
    ``seq = [pos_in_b[x] for x in v_order]``, reconstructed GREEDILY FROM
    THE LEFT to match ``_lcs_ids``'s own left-to-right tie-break (which
    advances both cursors, i.e. matches each id at its EARLIEST possible
    position in ``v_order``): compute ``s_len[i]`` = length of the longest
    strictly-increasing subsequence of ``seq`` STARTING at index ``i``
    (via one reverse patience pass over ``seq`` reversed, tracking pile
    tops), then greedily take ``v_order[i]`` left-to-right whenever
    ``s_len[i] == need`` (the longest remaining suffix length still
    available) AND ``seq[i] > last`` (strictly after the previously taken
    element), decrementing ``need`` and updating ``last`` each time taken.
    """
    if b_order == v_order:
        return set(b_order)

    pos_in_b = {x: i for i, x in enumerate(b_order)}
    seq = [pos_in_b[x] for x in v_order]
    n = len(seq)
    if n == 0:
        return set()

    # s_len[i] = length of the longest strictly-increasing subsequence of
    # ``seq`` STARTING at index i. Computed with one right-to-left patience
    # pass over the NEGATED values: scanning i from n-1 down to 0 while
    # patience-sorting -seq[i] is exactly the standard "LIS ending here"
    # algorithm run on the reversed, negated sequence, which is equivalent
    # (by the reversal+negation duality) to "LIS starting here" on the
    # original sequence read forward. ``tails`` is kept sorted ascending
    # (the standard patience-sort invariant); it is never read back for its
    # own values, only its length/insertion-position.
    s_len = [0] * n
    tails: list[int] = []
    for i in range(n - 1, -1, -1):
        idx = bisect.bisect_left(tails, -seq[i])
        s_len[i] = idx + 1
        if idx == len(tails):
            tails.append(-seq[i])
        else:
            tails[idx] = -seq[i]

    # Greedy left-to-right reconstruction matching ``_lcs_ids``'s own
    # left-to-right tie-break (advances both cursors on a match, i.e. picks
    # the EARLIEST v-position for each stable id): ``need`` starts at the
    # overall LIS length and only decreases when an element is actually
    # taken, so the first index reaching each remaining required length
    # (with a strictly-increasing value relative to the last taken one) is
    # always chosen.
    need = max(s_len)
    last = -1
    result: set[str] = set()
    for i in range(n):
        if s_len[i] == need and seq[i] > last:
            result.add(v_order[i])
            last = seq[i]
            need -= 1
    return result


# --- ProjectionIndex ------------------------------------------------------------


class ProjectionIndex:
    """In-memory, rebuildable ``node_id -> owning path`` map (spec §7 / M5 follow-up).

    Built purely from durable state (``store.list_sync_files`` + each
    file's base snapshot) -- never from a live vault read -- so it is
    crash-safe and rebuildable at any time via :meth:`build`. Updated
    incrementally by the ``Reconciler`` after every successful reconcile
    cycle (:meth:`update`, called with the freshly-written ``H2``'s block
    ids), so it always reflects each file's state AS OF ITS OWN last
    reconcile -- not necessarily its live-on-disk bytes at this exact
    instant if that file hasn't been reconciled yet this "wave". Multi-cycle
    race hardening (two files whose independent watcher events race each
    other) is explicitly T5.8's battery, not this task's.
    """

    def __init__(self) -> None:
        self._owner: dict[str, str] = {}
        self._by_path: dict[str, set[str]] = {}

    @classmethod
    def build(cls, conn: sqlite3.Connection) -> ProjectionIndex:
        """Rebuild the index from every tracked sync file's current base snapshot."""
        index = cls()
        for row in store.list_sync_files(conn):
            path = row["path"]
            sync_root_id = row["sync_root_id"]
            base_text = store.read_base_snapshot(conn, sync_root_id, path)
            if base_text is None:
                continue
            block_set = parse(base_text)
            index.update(path, set(block_set.blocks.keys()))
        return index

    def owner(self, node_id: str) -> str | None:
        """Return the path currently believed to own ``node_id``, or ``None``."""
        return self._owner.get(node_id)

    def update(self, path: str, block_ids: set[str]) -> None:
        """Record that ``path``'s base snapshot now contains exactly ``block_ids``.

        Any id ``path`` previously owned but no longer contains is dropped
        (unless another path has since claimed it, in which case it is
        already gone from ``_owner`` under this key). Every id in
        ``block_ids`` is (re)claimed by ``path`` -- last writer wins, which
        is the correct "most recently reconciled" semantics for
        single-cycle-at-a-time processing.
        """
        previous = self._by_path.get(path, set())
        for stale_id in previous - block_ids:
            if self._owner.get(stale_id) == path:
                del self._owner[stale_id]
        self._by_path[path] = set(block_ids)
        for node_id in block_ids:
            self._owner[node_id] = path


# --- diff_blocks / _compute_ops (zero-I/O) --------------------------------------


def _new_request_parent(blocks_v: BlockSet, nr: NewRequest) -> str | None:
    """Nearest shallower REAL (already-anchored) task before ``nr`` in doc order.

    Mirrors ``contract.parser``'s own ``_parent_for_depth`` stack algorithm,
    applied only over already-anchored task blocks (never another
    still-unminted ``^tm-new`` sibling in the same cycle -- see the
    ``Op.parent_id`` docstring for why that narrower case is left
    unresolved here, a documented limitation).
    """
    if nr.shape != "task" or nr.depth == 0:
        return None
    candidates = sorted(
        (b for b in blocks_v.blocks.values() if b.kind == "task" and b.line_no < nr.line_no),
        key=lambda b: b.line_no,
    )
    stack: list[tuple[int, str]] = []
    for block in candidates:
        while stack and stack[-1][0] >= block.depth:
            stack.pop()
        stack.append((block.depth, block.id))
    while stack and stack[-1][0] >= nr.depth:
        stack.pop()
    return stack[-1][1] if stack else None


def _compute_ops(
    blocks_b: BlockSet,
    blocks_v: BlockSet,
    *,
    maturity: MaturityLookup,
    projection: ProjectionIndex,
    current_path: str,
    lint_result: LintResult,
    anchor_elsewhere: Callable[[str], str | None] | None = None,
) -> tuple[list[Op], list[ReconcileReviewItem]]:
    """Compute the ops table for ``blocks_v`` against ``blocks_b`` (spec §4.8/§7).

    ``blocks_v`` is the (already parsed) V' the ops are computed against --
    the caller decides whether that's post-repair or raw, keeping this
    function itself repair-agnostic. See module docstring detection rules
    (verbatim from the fable design) for the full per-kind semantics.

    ``anchor_elsewhere`` (build-plan T5.8-3, fable-reviewed, human-decided
    2026-07-13) is an OPTIONAL zero-I/O-from-this-function's-perspective
    callable: given a base-only id about to be hard-deleted, it returns the
    path of another currently-tracked ``*.md`` file (within the same sync
    root) whose LIVE on-disk bytes right now contain a managed block with
    that exact anchor id, or ``None`` if no such file exists. This is PROOF
    (not a guess) of a move-in-flight for an S0 node -- the ``ProjectionIndex``
    ``owner`` check just above only catches a move that the OTHER file has
    already reconciled at least once; ``anchor_elsewhere`` additionally
    catches the "natural causality" ordering where the source file's cycle
    runs BEFORE the destination file has ever been reconciled (so
    ``projection.owner`` is still ``None`` or stale). Defaulting to ``None``
    preserves this function's exact prior behavior (every existing pure
    unit test and golden fixture, including E06's single-file delete-s0
    case, passes unchanged since a one-file sync root has no "elsewhere" to
    find anyway). See ``Reconciler``'s real implementation for the on-disk
    scan this callable wraps.
    """
    ops: list[Op] = []
    extra_review: list[ReconcileReviewItem] = []

    withheld_lost_anchor = {
        item.id for item in lint_result.review_items if item.code == "E_LOST_ANCHOR" and item.id
    } | {r.id for r in lint_result.repairs if r.code == "E_LOST_ANCHOR"}
    withheld_deleted_s1 = {
        item.id for item in lint_result.review_items if item.code == "E_DELETED_S1" and item.id
    }
    withheld_checksum = {
        item.id for item in lint_result.review_items if item.code == "E_ID_CHECKSUM" and item.id
    }

    b_ids = set(blocks_b.blocks)
    v_ids = set(blocks_v.blocks)
    common = b_ids & v_ids
    changed_ids: set[str] = set()

    # --- modified / checkbox_toggled / reparented (V' doc order) -----------
    for node_id, vault_block in blocks_v.blocks.items():
        if node_id not in common:
            continue
        base_block = blocks_b.blocks[node_id]

        text_changed = base_block.text != vault_block.text
        state_changed = (
            vault_block.kind == "task" and base_block.task_state != vault_block.task_state
        )
        parent_changed = (
            vault_block.kind == "task" and base_block.parent_id != vault_block.parent_id
        )

        emitted = False
        if text_changed:
            ops.append(
                Op(kind="modified", node_id=node_id, vault_block=vault_block, base_block=base_block)
            )
            emitted = True
        elif state_changed:
            ops.append(
                Op(
                    kind="checkbox_toggled",
                    node_id=node_id,
                    vault_block=vault_block,
                    base_block=base_block,
                )
            )
            emitted = True
        if parent_changed:
            ops.append(
                Op(
                    kind="reparented",
                    node_id=node_id,
                    vault_block=vault_block,
                    base_block=base_block,
                )
            )
            emitted = True
        if emitted:
            changed_ids.add(node_id)

    # --- moved: LCS over the stable (unchanged) ids in each doc order ------
    stable_ids = common - changed_ids
    b_order = [i for i in blocks_b.blocks if i in stable_ids]
    v_order = [i for i in blocks_v.blocks if i in stable_ids]
    stable = _stable_order_ids(b_order, v_order)
    for node_id in v_order:
        if node_id not in stable:
            ops.append(
                Op(
                    kind="moved",
                    node_id=node_id,
                    vault_block=blocks_v.blocks[node_id],
                    base_block=blocks_b.blocks[node_id],
                )
            )

    # --- created: every ^tm-new request -------------------------------------
    for nr in blocks_v.new_requests:
        ops.append(
            Op(
                kind="created",
                node_id=None,
                new_request=nr,
                parent_id=_new_request_parent(blocks_v, nr),
            )
        )

    # --- created: anchors new to this file (adopt vs cross-file dup) -------
    new_anchor_ids = v_ids - b_ids
    for node_id in blocks_v.blocks:
        if node_id not in new_anchor_ids:
            continue
        if node_id in withheld_checksum:
            # Already surfaced as E_ID_CHECKSUM by linter.lint(); no
            # duplicate reconcile-level finding.
            continue
        vault_block = blocks_v.blocks[node_id]
        stage = _maturity_of(maturity, node_id)
        if stage is None:
            extra_review.append(
                ReconcileReviewItem(
                    id=node_id,
                    code="E_UNKNOWN_ANCHOR",
                    message=(
                        f"anchor ^tm-{node_id} does not correspond to any known node"
                    ),
                    line_nos=[vault_block.line_no],
                )
            )
            continue
        owner = projection.owner(node_id)
        if owner is None or owner == current_path:
            ops.append(Op(kind="created", node_id=node_id, vault_block=vault_block))
        else:
            extra_review.append(
                ReconcileReviewItem(
                    id=node_id,
                    code="E_DUP_ID",
                    message=(
                        f"anchor ^tm-{node_id} is live in both {current_path!r} and "
                        f"{owner!r} (cross-file duplicate, copy without cut)"
                    ),
                    line_nos=[vault_block.line_no],
                )
            )

    # --- deleted: base-only ids, excluding withheld/cross-file-move-out ----
    withheld_delete = withheld_lost_anchor | withheld_deleted_s1
    b_only_ids = b_ids - v_ids
    for node_id, base_block in blocks_b.blocks.items():
        if node_id not in b_only_ids:
            continue
        if node_id in withheld_delete:
            continue
        owner = projection.owner(node_id)
        if owner is not None and owner != current_path:
            # Cross-file move-out: some other file has already (as of its
            # own last reconcile) adopted this id. Silent -- no data loss,
            # the other file's cycle already committed/will commit the
            # membership transfer.
            continue
        # design note (T5.8-3, fable-reviewed, human-decided 2026-07-13):
        # withhold the hard-delete (instead of the ``owner`` check above,
        # which only catches a move the OTHER file has already reconciled)
        # iff a live managed block with this exact anchor id can be PROVEN
        # to exist RIGHT NOW in another *.md file under the same sync root
        # -- concrete evidence of a move-in-flight, never a guess. Silence
        # here mirrors the cross-file move-out branch above: the
        # destination's own upcoming cycle adopts the id via the existing
        # created/adopt machinery, and this cycle's base_store.put/
        # projection.update below vacate this file's ownership so that
        # adopt lands on an unowned id.
        if anchor_elsewhere is not None:
            loc = anchor_elsewhere(node_id)
            if loc is not None and loc != current_path:
                continue
        ops.append(Op(kind="deleted", node_id=node_id, base_block=base_block))

    return ops, extra_review


def _maturity_of(lookup: MaturityLookup, node_id: str) -> str | None:
    if callable(lookup):
        return lookup(node_id)
    return lookup.get(node_id)


def diff_blocks(
    blocks_b: BlockSet,
    blocks_v: BlockSet,
    *,
    base_text: str,
    vault_text: str,
    maturity: MaturityLookup,
    projection: ProjectionIndex,
    current_path: str,
    anchor_elsewhere: Callable[[str], str | None] | None = None,
) -> DiffOutcome:
    """Lint, certain-repair, and diff one file's parsed blocks (spec §4.8).

    Zero-I/O: ``maturity`` and ``projection`` are pure lookups (a callable/
    mapping and an already-built :class:`ProjectionIndex`, respectively) --
    this function itself never touches the DB or filesystem. Calls
    ``linter.lint`` internally (never re-derives violations), applies every
    certain-repair to a working copy of ``vault_text`` (``apply_repairs``),
    re-parses the repaired text as V', and computes ops against V'
    (``_compute_ops``). ``anchor_elsewhere`` is forwarded verbatim to
    ``_compute_ops`` -- see that function's docstring; defaulting to
    ``None`` preserves this function's exact prior behavior too.

    Partition invariant: an anchor id appears in EITHER ``ops`` OR
    ``lint.review_items``/``extra_review_items``, never both -- ids
    withheld by an open/unrepaired violation (``E_DELETED_S1``, unknown
    anchor, cross-file ``E_DUP_ID``, ...) never reach ``ops``.
    """
    lint_result = linter.lint(blocks_b, blocks_v, vault_text, maturity)
    repaired_text = apply_repairs(vault_text, lint_result.repairs)
    blocks_v_prime = parse(repaired_text)
    ops, extra_review = _compute_ops(
        blocks_b,
        blocks_v_prime,
        maturity=maturity,
        projection=projection,
        current_path=current_path,
        lint_result=lint_result,
        anchor_elsewhere=anchor_elsewhere,
    )
    return DiffOutcome(
        ops=ops, lint=lint_result, repaired_text=repaired_text, extra_review_items=extra_review
    )


# --- hub-facing (I/O) primitives ------------------------------------------------


def _body_line(body: str) -> str:
    """The single-line content a canonical node ``body`` contributes to the grammar.

    ``kernel.canonical.canonicalize_text`` (spec §4.3) guarantees EXACTLY
    one trailing newline on every canonical body, including a genuinely
    single-line paragraph/task body (e.g. ``"Original text"`` is stored as
    ``"Original text\\n"``). The line-oriented contract grammar (§4.7) never
    includes that mandatory trailing newline in a ``Block.text`` capture, so
    every hub<->vault body comparison/substitution in this module strips it
    first via this helper -- without it, EVERY single-line body would
    spuriously look "different"/"unprojectable" (a trailing ``"\\n"`` is
    technically ``"a newline in the body"``) even when nothing changed.
    A body with genuine embedded newlines (multi-paragraph) still has an
    internal ``"\\n"`` after stripping the trailing one, and is correctly
    flagged unprojectable by :func:`hub_state_for`.
    """
    return body.rstrip("\n")


def hub_state_for(
    conn: sqlite3.Connection,
    structure: BlockSet,
    *,
    path: str | None = None,
    read_only: bool = False,
) -> BlockSet:
    """Project the hub's CURRENT state onto ``structure``'s skeleton (spec §4.8).

    Copies ``structure`` (an already-parsed :class:`BlockSet` -- the
    "skeleton": which anchors exist, in what order, at what depth/parent),
    substituting each block's ``text``/``task_state`` for its node's
    CURRENT hub head (``store.get_node(id).body``/``.task_state``). A
    tombstoned node's block is dropped entirely (the hub no longer has
    anything to project for that anchor -- an S0+ hard/soft delete
    propagates). A block whose node id the kernel has never heard of at all
    (``store.NodeNotFoundError``) is KEPT with its skeleton text unchanged
    (task T5.8-2, human-decided 2026-07-13, fable-designed: the
    lossless-container invariant requires
    ``render(hub_state_for(parse(B))) == B`` to be a fixed point for a
    quiet cycle, which is only possible if a not-yet-known/unresolved
    anchor id's line survives; that id was already reviewed as
    ``E_ID_CHECKSUM``/``E_UNKNOWN_ANCHOR`` when it first appeared vault-side
    -- ``_compute_ops`` never re-reviews a common id, so no duplicate
    review is enqueued here). ``structure``'s ``raw_lines``/``front_matter``
    ride through ``model_copy`` untouched -- only ``blocks`` is ever
    substituted. No new node->path table is introduced -- membership
    derives entirely from ``structure``, which the caller builds by parsing
    either the base or the final vault text (see ``Reconciler.on_change``).

    # design note (T5.4, fable-reviewed, human-decided 2026-07-12) --
    # DECIDED gap #3: a hub body with an INTERNAL newline (after stripping
    # the one mandatory canonical trailing newline, see ``_body_line``) is
    # unprojectable by the line-oriented contract grammar (§4.7 -- every
    # block is exactly one line). Rather than corrupt the file with an
    # embedded newline, such a block is left with its ORIGINAL (skeleton)
    # text and one violation review item is enqueued so a human can
    # resolve the mismatch (e.g. by splitting the node or editing it back
    # to a single line).

    ``read_only`` (task T10.2, ``GET /sync/export``): when ``True``,
    suppresses the ``store.enqueue_review`` call above -- a read-only HTTP
    GET must mutate nothing, not even a review-queue insert. The block's
    original skeleton text is still kept for that entry either way (the
    render output is byte-identical regardless of ``read_only``; only the
    DB write is skipped). Defaults to ``False``, preserving every existing
    caller's exact prior behavior (``Reconciler.on_change``'s two call
    sites, which must keep enqueuing the review during a real reconcile).
    """
    new_blocks: dict[str, Block] = {}
    for node_id, block in structure.blocks.items():
        try:
            node = store.get_node(conn, node_id)
        except store.NodeNotFoundError:
            new_blocks[node_id] = block
            continue
        if node.status == "tombstone":
            continue
        line = _body_line(node.body)
        if "\n" in line:
            if not read_only:
                store.enqueue_review(
                    conn,
                    node_id,
                    "violation",
                    cause_ref=canonical_json(
                        {
                            "code": "E_UNPROJECTABLE_BODY",
                            "path": path,
                            "id": node_id,
                            "message": (
                                "hub body contains a newline; the line-oriented contract "
                                "grammar cannot project it -- base text kept for this block"
                            ),
                        }
                    ).decode(),
                )
            new_blocks[node_id] = block
            continue
        update: dict[str, Any] = {"text": line}
        if block.kind == "task":
            update["task_state"] = node.task_state
        new_blocks[node_id] = block.model_copy(update=update)
    return structure.model_copy(update={"blocks": new_blocks})


def hub_changed_since(conn: sqlite3.Connection, base_block: Block, node_id: str) -> bool:
    """True iff the hub's current head diverges from ``base_block`` (spec §4.8).

    Content-based, per fable's design: compares CURRENT
    ``store.get_node(node_id)`` body/task_state against ``base_block``'s
    recorded text/state (the vault-parsed snapshot as of the last agreed
    base). A hub edit-then-revert within the same cycle therefore reads as
    "unchanged" -- accepted and documented, not a bug.
    """
    node = store.get_node(conn, node_id)
    if _body_line(node.body) != base_block.text:
        return True
    return bool(base_block.kind == "task" and node.task_state != base_block.task_state)


def _vault_matches_hub(conn: sqlite3.Connection, node_id: str, vault_block: Block) -> bool:
    """True iff the vault's proposed content already matches the CURRENT hub head."""
    node = store.get_node(conn, node_id)
    if _body_line(node.body) != vault_block.text:
        return False
    return not (vault_block.kind == "task" and node.task_state != vault_block.task_state)


def _render_new_line(nr: NewRequest, node_id: str) -> str:
    """Rewrite a ``^tm-new`` line into its real contract-anchored form (spec §4.7)."""
    anchor = contract_anchor(node_id)
    if nr.shape == "task":
        indent = grammar.INDENT_UNIT * nr.depth
        mark = "x" if nr.task_state == "done" else " "
        return f"{indent}- [{mark}] {nr.text} {anchor}"
    return f"{nr.text} {anchor}"


def kernel_apply(conn: sqlite3.Connection, op: Op, *, author: str = SYNC_AUTHOR) -> str | None:
    """Apply one :class:`Op` via the store API only (spec §4.8: "origin='sync'").

    Returns the freshly-minted node id for a ``^tm-new`` ``created`` op (so
    the caller can rewrite that line in the vault text), else ``None``.
    Never writes SQLite directly (rule 0.4) -- every branch below calls a
    ``kernel/store.py`` function.
    """
    if op.kind == "created":
        if op.new_request is not None:
            nr = op.new_request
            node_type = "task" if nr.shape == "task" else PARAGRAPH_NODE_TYPE
            node = store.create_node(
                conn,
                node_type=node_type,
                body=nr.text,
                task_state=nr.task_state if nr.shape == "task" else None,
                author=author,
            )
            if nr.shape == "task" and op.parent_id is not None:
                store.create_edge(
                    conn,
                    src=op.parent_id,
                    dst=node.id,
                    edge_type="composes",
                    facet_binding=None,
                    provenance="human",
                )
            return node.id
        # Cross-file adopt (spec §7 E04): the node already exists; this
        # file simply didn't own the anchor before. Commit only if the
        # vault's body/state actually differs from the current hub head.
        assert op.node_id is not None
        assert op.vault_block is not None
        node = store.get_node(conn, op.node_id)
        vb = op.vault_block
        body_differs = _body_line(node.body) != vb.text
        state_differs = vb.kind == "task" and node.task_state != vb.task_state
        if body_differs or state_differs:
            kwargs: dict[str, Any] = {}
            if state_differs:
                kwargs["task_state"] = vb.task_state
            store.commit_node(
                conn,
                op.node_id,
                new_body=vb.text if body_differs else None,
                change_class=SYNC_CHANGE_CLASS,
                facets_touched=[],
                author=author,
                **kwargs,
            )
        return None

    if op.kind == "modified":
        assert op.node_id is not None and op.vault_block is not None
        vb = op.vault_block
        kwargs = {}
        state_changed = (
            vb.kind == "task"
            and op.base_block is not None
            and vb.task_state != op.base_block.task_state
        )
        if state_changed:
            kwargs["task_state"] = vb.task_state
        store.commit_node(
            conn,
            op.node_id,
            new_body=vb.text,
            change_class=SYNC_CHANGE_CLASS,
            facets_touched=[],
            author=author,
            **kwargs,
        )
        return None

    if op.kind == "checkbox_toggled":
        assert op.node_id is not None and op.vault_block is not None
        store.commit_node(
            conn,
            op.node_id,
            task_state=op.vault_block.task_state,
            change_class=SYNC_CHANGE_CLASS,
            facets_touched=[],
            author=author,
        )
        return None

    if op.kind == "reparented":
        assert op.node_id is not None and op.base_block is not None and op.vault_block is not None
        old_parent = op.base_block.parent_id
        if old_parent is not None:
            for edge in store.find_live_edges(
                conn, src=old_parent, dst=op.node_id, edge_type="composes"
            ):
                store.retract_edge(conn, edge.id)
        new_parent = op.vault_block.parent_id
        if new_parent is not None:
            store.create_edge(
                conn,
                src=new_parent,
                dst=op.node_id,
                edge_type="composes",
                facet_binding=None,
                provenance="human",
            )
        return None

    if op.kind == "deleted":
        assert op.node_id is not None
        store.delete_node(conn, op.node_id)
        return None

    # "moved": hub no-op (spec §4.8 point 4) -- sibling order is file-side
    # only, persisted purely via base_store.put(H2); still emitted as an Op
    # so callers/golden fixtures can see it happened.
    return None


# --- conflict seam ---------------------------------------------------------------


def conflict_branch_handler(conn: sqlite3.Connection, op: Op, path: str) -> None:
    """Real T5.5 conflict handling: branch the vault version + enqueue one review.

    A both-sides-edit conflict loses nothing on either side: the hub head
    already keeps whatever won the file this cycle (the mainline
    ``commit_node``/no-op that ran earlier this same ``on_change`` pass,
    before this handler is ever invoked -- ``head_hash`` is NOT touched
    here), and the vault's divergent version is ADDITIONALLY recorded as a
    non-head branch commit on the SAME node's DAG
    (``store.record_conflict_branch``), parented on the node's current head
    commit (the deterministic fork anchor -- see that function's docstring
    and the logged SPEC-QUESTION on fork-point provenance). Exactly one
    ``cause_kind="conflict"`` review is enqueued per distinct conflict,
    deduplicated via ``store.find_open_reviews`` against the deterministic
    ``cause_ref`` bytes (replay-safe for T5.6's crash recovery -- a
    completed cycle re-run hits both the ``record_conflict_branch`` and the
    enqueue dedup gate and performs zero additional writes).

    A ``deleted``-op conflict (``op.vault_block is None`` -- the vault
    removed the anchor while the hub concurrently edited it) has nothing to
    branch: the hub head is already the sole remaining body for that node,
    so this enqueues a review WITHOUT recording a branch commit (documented
    SPEC-QUESTION: a vault-delete + hub-edit conflict gets a review but no
    branch commit -- there is no second version to preserve).
    """
    branch_commit: str | None = None
    if op.node_id is not None and op.vault_block is not None:
        vb = op.vault_block
        kwargs: dict[str, Any] = {}
        if vb.kind == "task":
            kwargs["task_state"] = vb.task_state
        branch_commit = store.record_conflict_branch(
            conn,
            op.node_id,
            vb.text,
            author=SYNC_AUTHOR,
            message=commits.conflict_branch_message(path),
            **kwargs,
        )

    cause_ref = canonical_json(
        commits.conflict_cause_ref(
            path=path,
            vault_text=op.vault_block.text if op.vault_block else None,
            vault_task_state=op.vault_block.task_state if op.vault_block else None,
            base_text=op.base_block.text if op.base_block else None,
            branch_commit=branch_commit,
        )
    ).decode()

    if not store.find_open_reviews(
        conn, node_id=op.node_id, cause_kind="conflict", cause_ref=cause_ref
    ):
        store.enqueue_review(conn, op.node_id, "conflict", cause_ref=cause_ref)


# --- Reconciler --------------------------------------------------------------


@dataclass
class Reconciler:
    """The full §4.8 ``on_change(path)`` pipeline, wired to a live store + origin.

    Public API (T5.5/T5.6/T5.7 wiring contract)
    ---------------------------------------------
    - ``Reconciler(conn, origin, *, conflict_handler=conflict_branch_handler, projection=None)``
    - ``on_change(path: str) -> None`` -- the exact ``Callable[[str], None]``
      shape ``sync.watcher.Watcher``'s ``on_cycle`` parameter expects
      (T5.6 wires ``watcher = Watcher(conn, reconciler.on_change, ...)``).
    - ``conflict_handler`` is a swappable seam
      (``Callable[[sqlite3.Connection, Op, str], None]``): defaults to
      ``conflict_branch_handler`` (task T5.5 -- branches the vault version
      onto the node's commit DAG + enqueues one ``cause_kind="conflict"``
      review), reused verbatim without touching this pipeline at all.
    - ``projection`` defaults to a freshly ``ProjectionIndex.build(conn)``
      if omitted; callers that want to share one long-lived index across
      many ``Reconciler`` instances (unusual) may pass their own.
    """

    conn: sqlite3.Connection
    origin: OriginTracker
    conflict_handler: Callable[[sqlite3.Connection, Op, str], None] = conflict_branch_handler
    projection: ProjectionIndex | None = None

    def __post_init__(self) -> None:
        if self.projection is None:
            self.projection = ProjectionIndex.build(self.conn)
        self._roots_cache: list[dict[str, Any]] | None = None

    # -- sync-root resolution ---------------------------------------------

    def _load_roots(self, *, force: bool = False) -> list[dict[str, Any]]:
        if force or self._roots_cache is None:
            self._roots_cache = store.list_sync_roots(self.conn)
        return self._roots_cache

    @staticmethod
    def _normalize(path: str) -> str:
        return unicodedata.normalize("NFC", os.path.abspath(path))

    def _match_root(
        self, normalized_path: str, roots: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        best: dict[str, Any] | None = None
        best_len = -1
        for root in roots:
            root_path = self._normalize(root["root_path"])
            if normalized_path == root_path or normalized_path.startswith(root_path + os.sep):
                if len(root_path) > best_len:
                    best = root
                    best_len = len(root_path)
        return best

    def resolve_sync_root(self, path: str) -> dict[str, Any] | None:
        """Longest-prefix match ``path`` against every durable sync root (spec §4.4/§4.8).

        NFC-normalizes and absolutizes both sides before comparing. Roots
        are cached; a miss triggers exactly one forced refresh (a sync
        root registered after this ``Reconciler`` was constructed/last
        cached is picked up on the very next unmatched path) before giving
        up.
        """
        normalized = self._normalize(path)
        match = self._match_root(normalized, self._load_roots())
        if match is None:
            match = self._match_root(normalized, self._load_roots(force=True))
        return match

    # -- write-back ----------------------------------------------------------

    def write_if_diff(self, path: str, text: str) -> bool:
        """Write ``text`` (already canonical) to ``path`` iff it differs; record the write.

        Returns ``True`` iff a write happened. Atomic (temp file +
        ``os.replace``). Records ``(path, object_hash(text))`` via
        ``self.origin`` for EVERY write this method performs, including a
        ``^tm-new`` rewrite -- "origin-tagged, not an echo" (spec §4.7).

        build-plan T9.1: both the pre-write existence read and the
        ``os.replace`` rename are wrapped in :func:`retry_with_backoff` --
        this is the spec's own named write-back primitive (§4.8's
        ``write_if_diff(path, H)``/``write_if_diff(path, H2)``), i.e. the
        actual OS-level file write this task's "locking retry" targets. A
        transient Windows sharing-violation/lock-violation/AV-held-handle
        error on either call is retried with backoff before giving up.
        """
        target = Path(path)
        current: str | None = None
        if target.exists():
            current = canonicalize_text(
                retry_with_backoff(lambda: target.read_text(encoding="utf-8"))
            )
        if current == text:
            return False
        tmp_path = target.with_name(f".{target.name}.tmp-{secrets.token_hex(8)}")
        tmp_path.write_text(text, encoding="utf-8")
        retry_with_backoff(lambda: os.replace(tmp_path, target))
        self.origin.record_write(path, object_hash(text.encode("utf-8")))
        return True

    # -- cross-file anchor scan (T5.8-3) ---------------------------------------

    @staticmethod
    def _make_anchor_elsewhere(root_path: str, current_path: str) -> Callable[[str], str | None]:
        """Build the real ``anchor_elsewhere`` callable for one ``on_change`` cycle.

        Fresh per call (files can change between cycles -- never cached
        across cycles). Scoped to ``root_path`` ONLY (ids are unique per
        sync root, spec §4.7 -- scanning across roots would be an identity
        guess). The directory walk + each candidate file's bytes are read
        AT MOST ONCE per cycle no matter how many disappearing ids are
        queried (the ``cache`` dict below is populated lazily on the first
        call and reused for every subsequent id this same cycle); each
        candidate is only ``parse``d (the more expensive confirm step) once
        it is actually string-prefiltered as a live candidate for SOME
        queried id, and that parse is itself cached per file too.

        For each id: substring-prefilter (``contract_anchor(node_id) in
        text``) every OTHER ``*.md`` file's raw bytes under ``root_path``,
        then confirm via ``parse(canonicalize_text(text))`` that the id is a
        genuine EOL managed anchor (not fenced/mid-line/unmanaged) before
        trusting it. Returns the first matching file's path, else ``None``.
        """
        cache: dict[str, tuple[str, BlockSet | None]] = {}
        walked = False

        def scan(node_id: str) -> str | None:
            nonlocal walked
            if not walked:
                for candidate in Path(root_path).rglob("*.md"):
                    candidate_str = str(candidate)
                    if candidate_str == current_path:
                        continue
                    try:
                        text = candidate.read_bytes().decode("utf-8")
                    except OSError:
                        continue
                    cache[candidate_str] = (text, None)
                walked = True

            anchor = contract_anchor(node_id)
            for candidate_str, (text, parsed) in list(cache.items()):
                if anchor not in text:
                    continue
                if parsed is None:
                    parsed = parse(canonicalize_text(text))
                    cache[candidate_str] = (text, parsed)
                if node_id in parsed.blocks:
                    return candidate_str
            return None

        return scan

    # -- main pipeline ---------------------------------------------------------

    def on_change(self, path: str) -> None:
        """The §4.8 ``on_change(path)`` pipeline, run synchronously for ``path``.

        Follows the spec pseudocode verbatim, in order: quiet shortcut,
        hub-only shortcut, parse+lint+repair+diff, pause&diff (zero
        writes), conservative-profile repair routing, per-op conflict
        resolution, canonical write-back + base_store.put + projection
        update.
        """
        root = self.resolve_sync_root(path)
        if root is None:
            logger.warning("on_change: %r matches no registered sync root; ignoring", path)
            return
        sync_root_id = root["id"]
        conservative = detect_cloud_path(root["root_path"]) is not None
        assert self.projection is not None

        # design note (T9.2c): timing starts here, after the unregistered
        # sync-root guard above -- an event for a path with no registered
        # sync root does zero reconciliation and is not a "sync cycle" for
        # §7's violation_rate denominator (both this task's build-plan Steps
        # and its spec-questions.md registration enumerate exactly the four
        # exit paths covered by the try/finally below -- quiet, hub-only,
        # pause&diff, and normal completion -- and omit the guard above).
        # ``finally`` also covers an exception mid-cycle (e.g. a genuinely
        # registered file vanishing under ``retry_with_backoff``): that is
        # still a real attempted cycle and must count.
        cycle_start = time.monotonic()
        try:
            # build-plan T9.1: the vault file may be transiently locked by
            # another process (AV scanner, editor autosave) right as its
            # watcher event fires -- retry with backoff rather than
            # surfacing a raw OSError for what is, on Windows, a routine
            # sharing violation. See ``sync.watcher``'s module docstring
            # ("Windows locking-retry / AV-noise tolerance") for the
            # reconcile.py/watcher.py split.
            raw = retry_with_backoff(lambda: Path(path).read_text(encoding="utf-8"))
            vault_text = canonicalize_text(raw)
            base_text = base_store.get(self.conn, sync_root_id, path)

            blocks_b_skeleton = parse(base_text or "")
            hub_blockset = hub_state_for(self.conn, blocks_b_skeleton, path=path)
            hub_text = render(hub_blockset)

            if vault_text == base_text and hub_text == base_text:
                return  # quiet

            if vault_text == base_text:
                # hub-only change: project the hub onto the base skeleton.
                self.write_if_diff(path, hub_text)
                base_store.put(self.conn, sync_root_id, path, hub_text)
                self.projection.update(path, set(hub_blockset.blocks.keys()))
                return

            blocks_b = parse(base_text or "")
            blocks_v = parse(vault_text)

            def maturity_lookup(node_id: str) -> Maturity | None:
                try:
                    return cast("Maturity", store.get_maturity(self.conn, node_id))
                except store.NodeNotFoundError:
                    return None

            anchor_elsewhere = self._make_anchor_elsewhere(root["root_path"], path)

            outcome = diff_blocks(
                blocks_b,
                blocks_v,
                base_text=base_text or "",
                vault_text=vault_text,
                maturity=maturity_lookup,
                projection=self.projection,
                current_path=path,
                anchor_elsewhere=anchor_elsewhere,
            )

            decision = linter.pause_and_diff(outcome.lint, blocks_b, base_text or "", vault_text)
            if decision is not None:
                store.enqueue_review(
                    self.conn,
                    None,
                    "violation",
                    cause_ref=canonical_json(
                        {
                            "path": path,
                            "pause": True,
                            "diff": decision.review_item.message,
                            "snapshot": decision.snapshot,
                        }
                    ).decode(),
                )
                return  # zero writes, zero base_store.put

            if conservative and outcome.lint.repairs:
                # design note (T5.4, fable-reviewed, human-decided 2026-07-12):
                # a conservative sync root (cloud-synced path, T5.3) never
                # applies certain-repairs silently -- route them to review
                # instead, one documented boolean branch. Ops are recomputed
                # against the RAW (unrepaired) vault blocks.
                #
                # design note (T9.2c): these repairs were routed to review,
                # not applied -- metrics.record_auto_repair must NOT fire
                # here, only in the else branch below where a repair was
                # actually, silently applied.
                for repair in outcome.lint.repairs:
                    store.enqueue_review(
                        self.conn,
                        repair.id,
                        "violation",
                        cause_ref=canonical_json(
                            {
                                "path": path,
                                "code": repair.code,
                                "action": repair.action,
                                "line_no": repair.line_no,
                                "before": repair.before,
                                "after": repair.after,
                            }
                        ).decode(),
                    )
                ops, extra_review = _compute_ops(
                    blocks_b,
                    blocks_v,
                    maturity=maturity_lookup,
                    projection=self.projection,
                    current_path=path,
                    lint_result=outcome.lint,
                    anchor_elsewhere=anchor_elsewhere,
                )
                repaired_text = vault_text
            else:
                ops = outcome.ops
                extra_review = outcome.extra_review_items
                repaired_text = outcome.repaired_text
                # design note (T9.2c): ``repaired_text`` above is
                # ``outcome.repaired_text`` == ``apply_repairs(vault_text,
                # outcome.lint.repairs)`` (see ``diff_blocks``) -- every
                # item in ``outcome.lint.repairs`` was just silently
                # applied to the vault text that will be written back this
                # cycle, so each one is exactly one real §4.7 certain-repair
                # application. Empty when there is nothing to repair (or
                # under a conservative root, since that case takes the
                # ``if`` branch above instead) -- never double-counted.
                for repair in outcome.lint.repairs:
                    metrics.record_auto_repair(repair.code)

            for item in outcome.lint.review_items:
                store.enqueue_review(
                    self.conn,
                    item.id,
                    "violation",
                    cause_ref=canonical_json(
                        {
                            "path": path,
                            "code": item.code,
                            "line_nos": item.line_nos,
                            "message": item.message,
                        }
                    ).decode(),
                )
            for extra in extra_review:
                store.enqueue_review(
                    self.conn,
                    extra.id,
                    "violation",
                    cause_ref=canonical_json(
                        {
                            "path": path,
                            "code": extra.code,
                            "line_nos": extra.line_nos,
                            "message": extra.message,
                        }
                    ).decode(),
                )

            # Precompute hub_changed_since ONCE per node id, using the store
            # state as it stood BEFORE this cycle applies anything -- reused
            # by every op targeting that id (e.g. a co-occurring modified +
            # reparented pair) so an earlier op's own write within this same
            # loop never contaminates a later op's conflict verdict.
            hub_changed_map: dict[str, bool | None] = {}
            for op in ops:
                if op.kind == "created" or op.node_id is None or op.base_block is None:
                    continue
                if op.node_id in hub_changed_map:
                    continue
                try:
                    hub_changed_map[op.node_id] = hub_changed_since(
                        self.conn, op.base_block, op.node_id
                    )
                except store.NodeNotFoundError:
                    hub_changed_map[op.node_id] = None

            vault_lines = repaired_text.split("\n")
            for op in ops:
                if op.kind == "created":
                    new_id = kernel_apply(self.conn, op, author=SYNC_AUTHOR)
                    if op.new_request is not None and new_id is not None:
                        idx = op.new_request.line_no - 1
                        if 0 <= idx < len(vault_lines):
                            vault_lines[idx] = _render_new_line(op.new_request, new_id)
                    continue

                assert op.node_id is not None
                changed = hub_changed_map.get(op.node_id)
                if changed is None:
                    # Node vanished from the hub entirely before we got to
                    # it this cycle -- nothing left to reconcile against.
                    continue
                if not changed:
                    kernel_apply(self.conn, op, author=SYNC_AUTHOR)
                    continue
                vault_matches = op.vault_block is not None and _vault_matches_hub(
                    self.conn, op.node_id, op.vault_block
                )
                if vault_matches:
                    continue  # convergent no-op: both sides already agree
                self.conflict_handler(self.conn, op, path)

            vault_final_text = canonicalize_text("\n".join(vault_lines))
            final_blocks = parse(vault_final_text)
            hub2_blockset = hub_state_for(self.conn, final_blocks, path=path)
            hub2_text = render(hub2_blockset)

            self.write_if_diff(path, hub2_text)
            # base_store.put unconditionally -- agreement may be new even if
            # the bytes happen to be unchanged (spec §4.8 point 6).
            base_store.put(self.conn, sync_root_id, path, hub2_text)
            self.projection.update(path, set(hub2_blockset.blocks.keys()))
        finally:
            metrics.record_sync_cycle_ms((time.monotonic() - cycle_start) * 1000.0)


# --- startup reconcile / crash recovery (task T5.6) --------------------------


def reconcile_all(
    conn: sqlite3.Connection,
    origin: OriginTracker | None = None,
    *,
    projection: ProjectionIndex | None = None,
) -> dict[str, int]:
    """Reconcile every tracked managed file once; the daemon-startup entry point (spec §4.8).

    Spec §4.8: "Startup: run ``on_change`` for every managed file
    (idempotent -- this is also crash recovery)." Because ``Reconciler.on_change``
    is already fully idempotent (content-addressed ``objects``, the quiet/
    hub-only shortcuts of T5.4, and the conflict-branch dedup gate of
    T5.5), a crash at ANY point mid-``on_change`` -- before a commit, after a
    commit but before the canonical write-back, after the write-back but
    before ``base_store.put`` -- is recovered from purely by re-running this
    same pipeline on restart: whatever partial state survived the crash is
    exactly the ``(V, B, H)`` triple a fresh ``on_change`` call re-derives
    from durable state (the vault file on disk, ``base_store``, and the
    hub), so it converges to the same stable canonical result a clean run
    would have produced, with no anchor/block ever silently dropped. This
    function does not re-implement any reconcile logic -- it only
    constructs one shared ``Reconciler``/``ProjectionIndex`` pair and drives
    ``.on_change`` over every ``store.list_sync_files`` path.

    ``origin`` defaults to a fresh, empty :class:`~akasha.sync.origin.OriginTracker`
    when omitted -- a startup/rescan run has no live filesystem watcher to
    share echo-suppression state with (T5.6 wires the daemon's long-lived
    watcher+reconciler pair separately in ``daemon.serve``), and every write
    this function performs is idempotent regardless, so an unsuppressed
    echo at worst causes one redundant (still idempotent, zero-diff) watcher
    cycle later, never incorrect state.

    A ``sync_files`` row whose file has since vanished from disk (deleted
    or moved away while the daemon was down) is *skipped and counted*, not
    a crash -- one missing file must not abort convergence of every other
    managed file (same resilience as T5.7's ``POST /sync/rescan``, which
    this function is the shared, reusable core of).

    Returns a small summary dict: ``files_reconciled`` (successfully ran
    ``on_change``), ``files_missing`` (tracked path no longer exists on
    disk), ``reviews_open`` (total open review-queue rows after this pass,
    across every ``cause_kind``).
    """
    if origin is None:
        from akasha.sync.origin import OriginTracker as _OriginTracker

        origin = _OriginTracker()

    reconciler = Reconciler(conn, origin, projection=projection)
    files_reconciled = 0
    files_missing = 0
    for f in store.list_sync_files(conn):
        try:
            reconciler.on_change(f["path"])
        except FileNotFoundError:
            files_missing += 1
            continue
        files_reconciled += 1

    reviews_open = len(store.find_open_reviews(conn))
    return {
        "files_reconciled": files_reconciled,
        "files_missing": files_missing,
        "reviews_open": reviews_open,
    }
