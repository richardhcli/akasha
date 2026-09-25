"""Reconcile pipeline: the §4.8 per-file three-way merge.

``Reconciler.on_change(path)`` reconciles a file's current text (V) against the last-agreed base
(B) and the hub's projection (H): certain repairs, ops keyed by anchor id, per-node conflict
resolution, canonical write-back, plus mirror propagation to every other file holding a changed
anchor.

Layout: (1) ``Op`` / ``DiffOutcome`` / ``ReconcileReviewItem`` result shapes; (2) the pure,
zero-I/O layer ``apply_repairs`` / ``diff_blocks`` / ``_compute_ops``; (3) ``ProjectionIndex``, the
rebuildable id -> owning-paths map (cross-file moves, mirrors); (4) the store-facing primitives
``hub_state_for`` / ``hub_changed_since`` / ``kernel_apply`` (every write goes through
``kernel/store.py``, rule 0.4); (5) ``Reconciler`` (root resolution, the staged cycle, conflict
seam, echo-recorded write-back); (6) ``discover_untracked_files`` / ``reconcile_all`` /
``project_node_change`` for startup, rescan and hub-side edits.

Ambiguities in §4.8 were resolved by the T5.4 architecture review (human-decided 2026-07-12) and
are marked inline as ``# design note``; they are not open SPEC-QUESTIONs.
"""

from __future__ import annotations

import bisect
import json
import logging
import os
import secrets
import sqlite3
import time
import unicodedata
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from pydantic import BaseModel

from akasha import metrics
from akasha.contract import grammar, linter
from akasha.contract.linter import LintResult, MaturityLookup, Repair
from akasha.contract.parser import Block, BlockSet, NewRequest, parse
from akasha.contract.render import render
from akasha.kernel import commits, ids, store
from akasha.kernel.canonical import canonical_json, canonicalize_text, object_hash
from akasha.kernel.ids import contract_anchor
from akasha.kernel.model import Maturity
from akasha.sync import base_store
from akasha.sync.watcher import (
    detect_cloud_path,
    iter_tracked_markdown,
    load_tmignore,
    path_is_ignored,
    retry_with_backoff,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from akasha.sync.origin import OriginTracker

logger = logging.getLogger("akasha")

# Runaway guard for `Reconciler.on_change`'s mirror fan-out (debug-plan D12): the most
# per-file cycles one source event may trigger. Legitimate fan-out is one cycle per
# distinct mirror file plus one per relayed edit; this is far above any real vault.
MAX_PROPAGATION_CYCLES = 1000

# M20-D: a file time more than this far in the future is a broken clock or a sync client's
# doing, not evidence the user just changed the file.
JOIN_CLOCK_SLACK_SECONDS = 300.0

# design note (T5.4, fable-reviewed, human-decided 2026-07-12) -- DECIDED
# gap #1: the node_type minted for a `^tm-new` paragraph (non-task) block.
# spec §4.2's NodeType has no generic "note"/"paragraph" member; "claim" is
# the closest existing type for free-standing managed prose and is used as
# a fixed module constant (a swappable seam, not a guess re-derived per
# call site).
PARAGRAPH_NODE_TYPE: Literal["claim"] = "claim"

# design note (T5.4): the change class of every sync-authored commit. "patch" is the least
# invalidating (§4.9 only walks on "major"); this constant is the seam for a real classifier
# (T7.2).
SYNC_CHANGE_CLASS: Literal["patch"] = "patch"

# The reserved author literal for every sync-originated store write (spec
# §4.8: "kernel.apply(op) # via store API, origin='sync'"; mirrors the
# existing author="system" literal already used by create_node's default).
SYNC_AUTHOR = "sync"


# --- pure result models -------------------------------------------------------


class Op(BaseModel):
    """One reconcile operation keyed by anchor id (spec §4.8).

    ``kind`` is modified | created | deleted | moved | checkbox_toggled | reparented.
    ``vault_block`` / ``base_block`` are the parsed blocks on each side (``None`` where they cannot
    exist). ``new_request`` is set for a ``created`` op from a ``^tm-new`` marker; a cross-file
    adopt sets ``node_id`` instead.

    ``parent_id`` carries the ``composes`` parent of a new task from ops-computation (full document
    order) to apply time, where ``kernel_apply`` sees one op at a time. ``mirror`` marks a
    ``created`` op whose anchor is already live in another file: this file is joining a node, not
    adopting a moved one. ``adopt_unknown`` marks a ``created`` op for a well-formed anchor the hub
    has never seen (a reset or second hub over an existing vault): the node is created UNDER THAT
    ID so transclusion links survive instead of splitting (M20-G).
    """

    kind: Literal["modified", "created", "deleted", "moved", "checkbox_toggled", "reparented"]
    node_id: str | None
    vault_block: Block | None = None
    base_block: Block | None = None
    new_request: NewRequest | None = None
    parent_id: str | None = None
    mirror: bool = False
    adopt_unknown: bool = False


class ReconcileReviewItem(BaseModel):
    """A reconcile-level review annotation outside ``linter.ViolationCode``.

    ``code`` is free-form and persisted through ``store.enqueue_review``'s ``cause_ref`` JSON.
    Nothing emits one today: an unknown anchor is adopted (M20-G) and a cross-file duplicate is a
    mirror (T19.3); the type stays as the seam ``extra_review_items`` flows through.
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
    """Apply every certain repair (spec §4.7) to ``text`` and return the result.

    A repair whose recorded ``before`` no longer matches its line (``line_no`` is 1-indexed) is
    skipped rather than guessed; ``lint`` computes all repairs against one text, so that should not
    happen.
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
    """The STABLE (non-moved) id set in O(n log n), replacing the O(n*m) LCS that blew E20's budget
    (T5.8-1: ~15 s for 5,000 blocks vs the <2 s spec limit).

    ``b_order`` and ``v_order`` are permutations of one id set, so their longest common subsequence
    is the longest increasing subsequence of ``v_order`` remapped through ``b_order`` positions. It
    must return exactly the set the old DP did (a Hypothesis oracle in ``test_reconcile.py`` checks
    this): golden fixtures pin the moved ops (E03/E17). Fast path: identical orders mean everything
    is stable. General case: patience-sort LIS, reconstructed greedily from the left to match the
    old left-to-right tie-break: ``s_len[i]`` is the longest increasing subsequence starting at
    ``i``, then ``v_order[i]`` is taken whenever ``s_len[i] == need`` and ``seq[i] > last``.
    """
    if b_order == v_order:
        return set(b_order)

    pos_in_b = {x: i for i, x in enumerate(b_order)}
    seq = [pos_in_b[x] for x in v_order]
    n = len(seq)
    if n == 0:
        return set()

    # ``s_len[i]`` = longest strictly-increasing subsequence of ``seq`` starting at i: the standard
    # "LIS ending here" patience pass run right-to-left over the negated values. ``tails`` stays
    # sorted ascending; only its length and insertion positions are used.
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
    """In-memory, rebuildable ``node_id -> owning paths`` map (spec §7, §4.7 "Mirrors").

    Built from durable state only (``store.list_sync_files`` + each base snapshot), so it is
    crash-safe and rebuildable via :meth:`build`. The ``Reconciler`` updates it after every cycle
    (:meth:`update`), so it reflects each file as of its own last reconcile; :meth:`refresh` learns
    files another instance reconciled. A node has a *set* of owners (:meth:`owners`); no table
    backs it. :meth:`owner` keeps the pre-mirror meaning: the most recently updated path still
    holding the id.
    """

    def __init__(self) -> None:
        self._owner: dict[str, str] = {}
        self._owners: dict[str, set[str]] = {}
        self._by_path: dict[str, set[str]] = {}
        self._base_hash: dict[str, str | None] = {}  # the base each path's entry was read from

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
            index.update(path, set(block_set.blocks.keys()), base_hash=row["base_hash"])
        return index

    def refresh(self, conn: sqlite3.Connection) -> None:
        """Re-sync with the database: learn files this instance never reconciled itself.

        The daemon's long-lived index is built at start-up, before any vault exists, and the vault
        is then reconciled by a throwaway ``Reconciler`` (``POST /v1/sync/rescan``) whose updates
        are discarded (D13): an edit typed into a copy could not find the original as an owner.
        Only files whose stored base hash differs from the last one read are re-parsed, so a quiet
        cycle costs one small query.
        """
        seen: set[str] = set()
        for row in store.list_sync_files(conn):
            path = row["path"]
            seen.add(path)
            if path in self._by_path and self._base_hash.get(path) == row["base_hash"]:
                continue
            base_text = store.read_base_snapshot(conn, row["sync_root_id"], path)
            ids: set[str] = set(parse(base_text).blocks) if base_text is not None else set()
            self.update(path, ids, base_hash=row["base_hash"])
        for gone in set(self._by_path) - seen:
            self.update(gone, set())
            self._base_hash.pop(gone, None)

    def owner(self, node_id: str) -> str | None:
        """Return the path currently believed to own ``node_id``, or ``None``."""
        return self._owner.get(node_id)

    def owners(self, node_id: str) -> frozenset[str]:
        """Return EVERY path currently holding ``node_id`` (empty if none).

        The mirror-aware counterpart of :meth:`owner`: a node whose anchor
        is live in two files (spec §4.7 "Mirrors") has both paths here.
        """
        return frozenset(self._owners.get(node_id, ()))

    def update(self, path: str, block_ids: set[str], *, base_hash: str | None = None) -> None:
        """Record that ``path``'s base snapshot (stored under ``base_hash``, which lets
        :meth:`refresh` skip untouched files) contains exactly ``block_ids``.

        Ids the path no longer holds are dropped from their owner sets; :meth:`owner` falls back to
        the lowest remaining holder when the last writer lets go of an id another file still holds.
        """
        previous = self._by_path.get(path, set())
        for stale_id in previous - block_ids:
            holders = self._owners.get(stale_id)
            if holders is not None:
                holders.discard(path)
                if not holders:
                    del self._owners[stale_id]
            if self._owner.get(stale_id) == path:
                remaining = self._owners.get(stale_id)
                if remaining:
                    self._owner[stale_id] = min(remaining)
                else:
                    self._owner.pop(stale_id, None)
        self._by_path[path] = set(block_ids)
        self._base_hash[path] = base_hash
        for node_id in block_ids:
            self._owners.setdefault(node_id, set()).add(path)
            self._owner[node_id] = path


# --- diff_blocks / _compute_ops (zero-I/O) --------------------------------------


def _new_request_parent(blocks_v: BlockSet, nr: NewRequest) -> str | None:
    """Nearest shallower already-anchored task before ``nr`` in document order (the ``composes``
    parent for a new task). Another unminted ``^tm-new`` sibling in the same cycle is never a
    parent: a documented limitation.
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

    ``blocks_v`` is the vault side the caller chose (post-repair or raw); this function is
    repair-agnostic. ``anchor_elsewhere(id)`` (T5.8-3) returns another tracked file in the same
    root whose LIVE bytes hold a managed block with that id, or ``None``: proof of a move in flight
    for an S0 node, catching the case where the source file's cycle runs before the destination was
    ever reconciled (``projection.owner`` still empty). Omitted, no delete is withheld.
    """
    ops: list[Op] = []
    extra_review: list[ReconcileReviewItem] = []

    # Only a certain repair (anchor re-inserted) keeps the block alive. A fuzzy lost anchor was
    # resolved as "change the ID" (M20-G): the line becomes a new node and the old id follows
    # the ordinary delete rules, so it is NOT withheld.
    withheld_lost_anchor = {
        r.id
        for r in lint_result.repairs
        if r.code == "E_LOST_ANCHOR" and r.action == "reinsert_anchor"
    }
    withheld_deleted_s1 = {
        item.id for item in lint_result.review_items if item.code == "E_DELETED_S1" and item.id
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
        vault_block = blocks_v.blocks[node_id]
        if not ids.is_valid(node_id):
            continue  # a checksum-invalid anchor: E_ID_CHECKSUM's repair gives that line a new node
        stage = _maturity_of(maturity, node_id)
        if stage is None:
            # M20-G: a well-formed anchor the hub has never seen is ADOPTED under its own id
            # (never re-minted, which would split every mirror after a hub reset).
            ops.append(
                Op(
                    kind="created",
                    node_id=node_id,
                    vault_block=vault_block,
                    parent_id=vault_block.parent_id,
                    adopt_unknown=True,
                )
            )
            continue
        # T19.3 (spec §4.7 "Mirrors"): an anchor already live in ANOTHER
        # file is not a duplicate-anchor violation -- this file is joining
        # the node as a mirror. Same adopt op as a move, flagged ``mirror``
        # so the pipeline can tell a join (hub wins on differing text)
        # from a move (the vault's text wins).
        others = projection.owners(node_id) - {current_path}
        ops.append(
            Op(kind="created", node_id=node_id, vault_block=vault_block, mirror=bool(others))
        )

    # --- deleted: base-only ids, excluding withheld/cross-file-move-out ----
    withheld_delete = withheld_lost_anchor | withheld_deleted_s1
    b_only_ids = b_ids - v_ids
    for node_id, base_block in blocks_b.blocks.items():
        if node_id not in b_only_ids:
            continue
        if node_id in withheld_delete:
            continue
        if projection.owners(node_id) - {current_path}:
            # Another file still holds this anchor: either a cross-file
            # move-out (that file already adopted the id as of its own last
            # reconcile) or -- since T19.3 -- simply the removal of one
            # mirror of a node that lives on elsewhere. Silent either way:
            # no data loss, and never a hub delete while any file shows it.
            continue
        # design note (T5.8-3): withhold the hard delete iff a live block with this exact id can be
        # PROVEN to exist right now in another file of the same root: a move in flight, never a
        # guess. The destination's own cycle adopts the id; this cycle's ``projection.update``
        # vacates this file's ownership so that adopt lands on an unowned id.
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


def _without_mirror_removals(
    result: LintResult, projection: ProjectionIndex, current_path: str
) -> LintResult:
    """Drop ``E_DELETED_S1`` findings for blocks another file still holds (T19.3).

    ``linter.lint`` sees one file, so a vanished S1+ block reads as a deletion; if a mirror still
    holds the anchor, this file merely stopped showing a node that lives on. Removal from the LAST
    file still surfaces ``E_DELETED_S1``.
    """

    def is_mirror_removal(code: str, node_id: str | None) -> bool:
        return (
            code == "E_DELETED_S1"
            and node_id is not None
            and bool(projection.owners(node_id) - {current_path})
        )

    return result.model_copy(
        update={
            "violations": [v for v in result.violations if not is_mirror_removal(v.code, v.id)],
            "review_items": [
                r for r in result.review_items if not is_mirror_removal(r.code, r.id)
            ],
        }
    )


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
    """Lint, certain-repair and diff one file's parsed blocks (spec §4.8), zero I/O.

    ``maturity`` and ``projection`` are pure lookups. Calls ``linter.lint``, applies its repairs to
    a working copy of ``vault_text``, re-parses that as V' and computes the ops against it.
    ``anchor_elsewhere`` is forwarded to ``_compute_ops``. An anchor appears in ``ops`` OR in the
    review items, never both: ids withheld by an open violation never reach ``ops``.
    """
    lint_result = _without_mirror_removals(
        linter.lint(blocks_b, blocks_v, vault_text, maturity), projection, current_path
    )
    repaired_text = apply_repairs(vault_text, lint_result.repairs)
    # apply_repairs returns `text` verbatim (same string) when there are no
    # repairs to apply -- re-parsing it would just reproduce `blocks_v`,
    # already computed by the caller from the same `vault_text`. Reusing it
    # matters at scale: E20's 5,000-block perf case has zero repairs in its
    # clean-edit scenario, so this was a third full-file parse for nothing.
    blocks_v_prime = blocks_v if not lint_result.repairs else parse(repaired_text)
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

    Canonical bodies end in exactly one newline that ``Block.text`` never includes, so every
    hub/vault comparison strips it; a body with an internal newline stays multi-line (unprojectable
    for a whole-line block, see :func:`hub_state_for`).
    """
    return body.rstrip("\n")


def _queue_violation(conn: sqlite3.Connection, node_id: str | None, **fields: Any) -> None:
    """Queue one ``violation`` review whose ``cause_ref`` is the canonical JSON of ``fields``."""
    store.enqueue_review(conn, node_id, "violation", cause_ref=canonical_json(fields).decode())


def hub_state_for(
    conn: sqlite3.Connection,
    structure: BlockSet,
    *,
    path: str | None = None,
    read_only: bool = False,
) -> BlockSet:
    """Project the hub's CURRENT state onto ``structure``'s skeleton (spec §4.8).

    ``structure`` says which anchors exist, in what order, at what depth; each block's
    text/task_state is replaced by its node's head. Raw lines, embeds and refs ride through
    untouched. Blocks whose node is unknown to the hub keep their skeleton text (the lossless
    container invariant: ``render(hub_state_for(parse(B))) == B`` on a quiet cycle); a
    tombstoned node's block is dropped. A whole-line block whose hub body contains a newline
    cannot be projected (one line per block): it keeps its skeleton text and one
    ``E_UNPROJECTABLE_BODY`` review is queued (``read_only`` suppresses that write, for
    ``GET /sync/export``). A span may hold newlines.
    """
    rows = store.get_projection_bulk(conn, list(structure.blocks))
    new_blocks: dict[str, Block] = {}
    for node_id, block in structure.blocks.items():
        row = rows.get(node_id)
        if row is None:
            new_blocks[node_id] = block
            continue
        status, body, task_state = row
        if status == "tombstone":
            continue
        text = _body_line(body)
        if "\n" in text and block.kind != "span":
            if not read_only:
                _queue_violation(
                    conn,
                    node_id,
                    code="E_UNPROJECTABLE_BODY",
                    path=path,
                    id=node_id,
                    message=(
                        "hub body contains a newline; the line-oriented contract grammar cannot "
                        "project it -- base text kept for this block"
                    ),
                )
            new_blocks[node_id] = block
        elif block.text == text and (block.kind != "task" or block.task_state == task_state):
            new_blocks[node_id] = block  # unchanged: no copy
        else:
            update: dict[str, Any] = {"text": text}
            if block.kind == "task":
                update["task_state"] = task_state
            new_blocks[node_id] = block.model_copy(update=update)
    return structure.model_copy(update={"blocks": new_blocks})


def hub_changed_since(conn: sqlite3.Connection, base_block: Block, node_id: str) -> bool:
    """True iff the hub's head diverges from ``base_block`` (spec §4.8): compares the current body
    and task_state with the base block's recorded text and state. A hub edit reverted within one
    cycle reads as unchanged; accepted.
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


_SPAN_NEW_MARKER = f"{grammar.SPAN_ID_OPEN}tm-new{grammar.SPAN_ID_CLOSE}"


def _mint_span_marker(line: str, nr: NewRequest, node_id: str, shift: int) -> tuple[str, int]:
    """``line`` with a span request's ``{tm-new}`` replaced by ``{tm-<id>}``; also the new shift.

    Only the marker changes (the text and the file's own padding stay as typed). ``shift`` is
    how many characters earlier markers on this same line have already added, since a line can
    hold several span requests and each mint lengthens it.
    """
    at = nr.marker_col + shift
    if not line.startswith(_SPAN_NEW_MARKER, at):
        return line, shift  # the line changed under us; the next cycle will see it again
    minted = f"{grammar.SPAN_ID_OPEN}tm-{node_id}{grammar.SPAN_ID_CLOSE}"
    new_line = line[:at] + minted + line[at + len(_SPAN_NEW_MARKER) :]
    return new_line, shift + len(minted) - len(_SPAN_NEW_MARKER)


def kernel_apply(conn: sqlite3.Connection, op: Op, *, author: str = SYNC_AUTHOR) -> str | None:
    """Apply one :class:`Op` via the store API only (spec §4.8: "origin='sync'").

    Returns the freshly-minted node id for a ``^tm-new`` ``created`` op (so
    the caller can rewrite that line in the vault text), else ``None``.
    Never writes SQLite directly (rule 0.4) -- every branch below calls a
    ``kernel/store.py`` function.
    """
    if op.kind == "created":
        if op.adopt_unknown:
            assert op.node_id is not None and op.vault_block is not None
            vb = op.vault_block
            node = store.create_node(
                conn,
                node_type="task" if vb.kind == "task" else PARAGRAPH_NODE_TYPE,
                body=vb.text,
                task_state=vb.task_state if vb.kind == "task" else None,
                author=author,
                node_id=op.node_id,
            )
            if vb.kind == "task" and op.parent_id is not None:
                store.create_edge(
                    conn,
                    src=op.parent_id,
                    dst=node.id,
                    edge_type="composes",
                    facet_binding=None,
                    provenance="human",
                )
            return None
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
    """Conflict handling (T5.5): branch the vault version and enqueue one review; nothing is lost.

    The hub head already holds whatever won the file this cycle; the vault's divergent version is
    additionally recorded as a non-head branch commit (``store.record_conflict_branch``, parented
    on the current head). One ``conflict`` review per distinct conflict, deduplicated against the
    deterministic ``cause_ref`` via ``find_open_reviews``, so a crash replay writes nothing more. A
    ``deleted`` op (the vault removed the anchor while the hub edited it) has no second version to
    branch: it gets a review without a branch commit (SPEC-QUESTION).
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
    """The §4.8 ``on_change(path)`` pipeline wired to a live store and origin tracker.

    ``on_change(path)`` has the ``Callable[[str], None]`` shape ``Watcher`` expects.
    ``conflict_handler`` is a swappable seam (default :func:`conflict_branch_handler`).
    ``projection`` defaults to a fresh ``ProjectionIndex.build(conn)``.
    """

    conn: sqlite3.Connection
    origin: OriginTracker
    conflict_handler: Callable[[sqlite3.Connection, Op, str], None] = conflict_branch_handler
    projection: ProjectionIndex | None = None

    def __post_init__(self) -> None:
        if self.projection is None:
            self.projection = ProjectionIndex.build(self.conn)
        self._roots_cache: list[dict[str, Any]] | None = None
        self._pauses_checked: set[str] = set()

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
        """Longest-prefix match of ``path`` against the durable sync roots (spec §4.4/§4.8).

        Both sides are NFC-normalized and absolutized. Roots are cached; a miss forces one refresh
        (a root registered after the last cache load is picked up on the next unmatched path).
        """
        normalized = self._normalize(path)
        match = self._match_root(normalized, self._load_roots())
        if match is None:
            match = self._match_root(normalized, self._load_roots(force=True))
        return match

    # -- write-back ----------------------------------------------------------

    def write_if_diff(self, path: str, text: str) -> bool:
        """Write ``text`` (already canonical) to ``path`` iff it differs; return whether it wrote.

        Atomic (temp file + ``os.replace``); the read and the rename are wrapped in
        :func:`retry_with_backoff` for transient Windows lock/AV errors (T9.1). Every write is
        recorded via ``self.origin``, a ``^tm-new`` rewrite included: "origin-tagged, not an echo"
        (spec §4.7).
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
        # newline="" disables Path.write_text's platform-default newline
        # translation (os.linesep) -- without it, every LF in this already-
        # canonical `text` (spec §4.3: LF only) gets silently rewritten to
        # CRLF on disk on Windows, corrupting the on-disk canonicalization
        # invariant despite the in-memory string being correct.
        tmp_path.write_text(text, encoding="utf-8", newline="")
        retry_with_backoff(lambda: os.replace(tmp_path, target))
        self.origin.record_write(path, object_hash(text.encode("utf-8")))
        return True

    # -- cross-file anchor scan (T5.8-3) ---------------------------------------

    @staticmethod
    def _make_anchor_elsewhere(root_path: str, current_path: str) -> Callable[[str], str | None]:
        """Build the ``anchor_elsewhere`` callable for one cycle (never cached across cycles).

        Scoped to ``root_path`` (ids are unique per root, §4.7). The walk and each file's bytes are
        read at most once per cycle, however many ids are queried; a file is parsed only after a
        substring prefilter (``contract_anchor(id) in text``) and that parse is cached, to confirm
        a genuine managed anchor (not fenced or mid-line). Returns the first matching file's path,
        else ``None``.
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
        """The §4.8 ``on_change(path)`` pipeline for ``path``, plus mirror propagation.

        Runs :meth:`_cycle`; if it committed a node's text or checkbox, every other file holding
        that anchor is brought up to date by the same three-way cycle (never a blind write, so its
        other edits survive). What such a propagated cycle itself commits (that file's own pending
        edit to another mirrored line: D12, M19-D) is relayed to that node's other owners. A
        hub-to-file write-back commits nothing and is never relayed, so there is no ping-pong;
        fan-out is capped. A vanished mirror is skipped; a failing one is logged without failing
        the source cycle, and heals on its next event or the startup reconcile.
        """
        committed = self._cycle(path)
        if not committed:
            return
        assert self.projection is not None
        relay: deque[tuple[str, set[str]]] = deque([(path, committed)])
        budget = MAX_PROPAGATION_CYCLES
        while relay:
            source, node_ids = relay.popleft()
            targets: list[str] = []
            for node_id in sorted(node_ids):
                for other in sorted(self.projection.owners(node_id) - {source}):
                    if other not in targets:
                        targets.append(other)
            for other in targets:
                if budget == 0:
                    logger.warning(
                        "mirror propagation from %r stopped after %d cycles; the remaining "
                        "mirrors heal on their next event or the startup reconcile",
                        path,
                        MAX_PROPAGATION_CYCLES,
                    )
                    return
                budget -= 1
                try:
                    own_edits = self._cycle(other)
                except FileNotFoundError:
                    continue
                except Exception:
                    logger.warning(
                        "mirror propagation to %r (from %r) failed; it will heal on its "
                        "next event or the startup reconcile",
                        other,
                        source,
                        exc_info=True,
                    )
                    continue
                # SPEC-QUESTION: M19-D -- §4.8 said a propagated cycle never propagates further;
                # relaying what it COMMITTED (its own pending edit) is the narrowest reading
                # that keeps the §4.7 promise (docs/spec-questions.md M19-D, debug-plan D12).
                if own_edits:
                    relay.append((other, own_edits))

    def _cycle(self, path: str) -> set[str]:
        """One §4.8 cycle for ``path`` ONLY (no propagation), in stages: read, quiet/hub-only
        shortcut, parse + lint + repair + diff, repair routing, per-op conflict resolution,
        canonical write-back + ``base_store.put`` + projection update.

        Returns the ids of nodes whose text/checkbox this cycle COMMITTED (empty for quiet,
        hub-only, conflicted or already-convergent ops): what :meth:`on_change` fans out to the
        other owners.
        """
        root = self._cycle_root(path)
        if root is None:
            return set()
        assert self.projection is not None
        self.projection.refresh(self.conn)  # D13: learn files another Reconciler reconciled

        # design note (T9.2c): timing starts after the unregistered-root guard (an event outside
        # any root is not a §7 "sync cycle"); ``finally`` also counts a cycle that raises mid-way
        # (e.g. a file vanishing under ``retry_with_backoff``).
        cycle_start = time.monotonic()
        try:
            return self._run_cycle(path, root)
        finally:
            metrics.record_sync_cycle_ms((time.monotonic() - cycle_start) * 1000.0)

    # -- the cycle's stages (T20.2: named, so each later change touches one) ----------

    def _cycle_root(self, path: str) -> dict[str, Any] | None:
        """The sync root ``path`` belongs to, or ``None`` when the cycle must be inert."""
        root = self.resolve_sync_root(path)
        if root is None:
            logger.warning("on_change: %r matches no registered sync root; ignoring", path)
            return None
        # build-plan T18.10b (ruling M18-B): a path the root's `.tmignore`
        # deny-list excludes is inert -- never read, parsed or written, whoever
        # asked (watcher, startup reconcile, rescan, hub-side reprojection, a
        # mirror fan-out). Its `sync_files` row and base snapshot stay in place.
        if path_is_ignored(path, root["root_path"], load_tmignore(root["root_path"], logger)):
            return None
        return root

    def _read_vault_text(self, path: str) -> str:
        """The file's canonical text."""
        # build-plan T9.1: the vault file may be transiently locked by
        # another process (AV scanner, editor autosave) right as its
        # watcher event fires -- retry with backoff rather than
        # surfacing a raw OSError for what is, on Windows, a routine
        # sharing violation. See ``sync.watcher``'s module docstring
        # ("Windows locking-retry / AV-noise tolerance") for the
        # reconcile.py/watcher.py split.
        raw = retry_with_backoff(lambda: Path(path).read_text(encoding="utf-8"))
        return canonicalize_text(raw)

    def _node_maturity(self, node_id: str) -> Maturity | None:
        try:
            return cast("Maturity", store.get_maturity(self.conn, node_id))
        except store.NodeNotFoundError:
            return None

    def _record_agreement(
        self, path: str, sync_root_id: str, text: str, blockset: BlockSet
    ) -> None:
        """Write ``text`` to the file (if it differs) and record it as the new agreed base."""
        assert self.projection is not None
        self.write_if_diff(path, text)
        # base_store.put unconditionally -- agreement may be new even if
        # the bytes happen to be unchanged (spec §4.8 point 6).
        base_hash = base_store.put(self.conn, sync_root_id, path, text)
        self.projection.update(path, set(blockset.blocks.keys()), base_hash=base_hash)

    def _run_cycle(self, path: str, root: dict[str, Any]) -> set[str]:
        sync_root_id = root["id"]
        assert self.projection is not None
        self._dismiss_stale_pauses(path)
        vault_text = self._read_vault_text(path)
        base_text = base_store.get(self.conn, sync_root_id, path)

        blocks_b = parse(base_text or "")
        blocks_v = blocks_b if vault_text == base_text else parse(vault_text)
        if not (blocks_b.has_constructs() or blocks_v.has_constructs()):
            return set()  # prose only (M20-C): never written, never tracked

        if vault_text == base_text:
            # the file is as last agreed: only a hub-side change can need writing
            hub_blockset = hub_state_for(self.conn, blocks_b, path=path)
            hub_text = render(hub_blockset)
            if hub_text != base_text:
                self._record_agreement(path, sync_root_id, hub_text, hub_blockset)
            return set()

        anchor_elsewhere = self._make_anchor_elsewhere(root["root_path"], path)
        outcome = diff_blocks(
            blocks_b,
            blocks_v,
            base_text=base_text or "",
            vault_text=vault_text,
            maturity=self._node_maturity,
            projection=self.projection,
            current_path=path,
            anchor_elsewhere=anchor_elsewhere,
        )

        conservative = detect_cloud_path(root["root_path"]) is not None
        ops, extra_review, repaired_text = self._resolve_repairs(
            path,
            outcome,
            blocks_b,
            blocks_v,
            anchor_elsewhere,
            vault_text,
            conservative=conservative,
        )
        self._enqueue_findings(path, outcome, extra_review)
        changed_at = None if conservative else self._file_changed_at(path)
        committed, vault_lines = self._apply_ops(path, ops, repaired_text, changed_at)

        final_text = canonicalize_text("\n".join(vault_lines))
        final_blocks = blocks_v if final_text == vault_text else parse(final_text)
        hub2_blockset = hub_state_for(self.conn, final_blocks, path=path)
        self._record_agreement(path, sync_root_id, render(hub2_blockset), hub2_blockset)
        return committed

    def _dismiss_stale_pauses(self, path: str) -> None:
        """Dismiss ``path``'s open pause reviews left by versions that could pause a file.

        M20-G: a file is never paused any more, so any pause review still open for it is stale
        the moment the file is cycled again.
        """
        if path in self._pauses_checked:
            return  # nothing can create a pause any more, so one look per path is enough
        self._pauses_checked.add(path)
        for review in store.find_open_reviews(self.conn, cause_kind="violation"):
            try:
                ref: Any = json.loads(review["cause_ref"] or "{}")
            except ValueError:
                continue
            if isinstance(ref, dict) and ref.get("pause") is True and ref.get("path") == path:  # pyright: ignore[reportUnknownMemberType]
                store.resolve_review(self.conn, review["id"], "dismissed")

    def _resolve_repairs(
        self,
        path: str,
        outcome: DiffOutcome,
        blocks_b: BlockSet,
        blocks_v: BlockSet,
        anchor_elsewhere: Callable[[str], str | None],
        vault_text: str,
        *,
        conservative: bool,
    ) -> tuple[list[Op], list[ReconcileReviewItem], str]:
        """Apply (or, on a conservative root, review) the certain repairs; return the ops to run."""
        if not (conservative and outcome.lint.repairs):
            # design note (T9.2c): ``outcome.repaired_text`` ==
            # ``apply_repairs(vault_text, outcome.lint.repairs)`` (see
            # ``diff_blocks``) -- every item in ``outcome.lint.repairs`` is
            # silently applied to the vault text that will be written back
            # this cycle, so each one is exactly one real §4.7 certain-repair
            # application. Empty when there is nothing to repair -- never
            # double-counted.
            for repair in outcome.lint.repairs:
                metrics.record_auto_repair(repair.code)
            return outcome.ops, outcome.extra_review_items, outcome.repaired_text
        # design note (T5.4/T9.2c): a conservative root (cloud-synced path) never applies certain
        # repairs silently; they are routed to review, ops are recomputed against the RAW vault
        # blocks, and ``record_auto_repair`` must not fire.
        for repair in outcome.lint.repairs:
            _queue_violation(
                self.conn,
                repair.id,
                path=path,
                code=repair.code,
                action=repair.action,
                line_no=repair.line_no,
                before=repair.before,
                after=repair.after,
            )
        assert self.projection is not None
        ops, extra_review = _compute_ops(
            blocks_b,
            blocks_v,
            maturity=self._node_maturity,
            projection=self.projection,
            current_path=path,
            lint_result=outcome.lint,
            anchor_elsewhere=anchor_elsewhere,
        )
        return ops, extra_review, vault_text

    def _enqueue_findings(
        self, path: str, outcome: DiffOutcome, extra_review: list[ReconcileReviewItem]
    ) -> None:
        """Queue every lint review item and every reconcile-level finding for a human."""
        for item in (*outcome.lint.review_items, *extra_review):
            _queue_violation(
                self.conn,
                item.id,
                path=path,
                code=item.code,
                line_nos=item.line_nos,
                message=item.message,
            )

    def _apply_ops(
        self, path: str, ops: list[Op], repaired_text: str, changed_at: float | None = None
    ) -> tuple[set[str], list[str]]:
        """Apply ``ops`` through the store; return (committed node ids, the vault's lines).

        The returned lines are ``repaired_text`` with each minted ``^tm-new`` request rewritten
        to its real anchor; the caller projects the hub onto them.
        """
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

        committed: set[str] = set()
        vault_lines = repaired_text.split("\n")
        shifts: dict[int, int] = {}  # per line: characters already added by span-marker mints
        for op in ops:
            if op.kind == "created":
                self._apply_created(op, path, vault_lines, committed, shifts, changed_at)
            else:
                self._apply_existing(op, path, hub_changed_map, committed)
        return committed, vault_lines

    def _apply_created(
        self,
        op: Op,
        path: str,
        vault_lines: list[str],
        committed: set[str],
        shifts: dict[int, int],
        changed_at: float | None = None,
    ) -> None:
        if op.mirror:
            # T19.4 / M20-D: this file JOINS a node another file already shows.
            assert op.node_id is not None and op.vault_block is not None
            try:
                verdict = self._classify_join(op, changed_at)
            except store.NodeNotFoundError:
                return
            if verdict == "conflict":
                # The hub head is newer than the file (or the file time cannot be trusted):
                # the hub wins, the file's version is preserved as a conflict branch + one
                # review (nothing lost, nothing guessed), and the write-back below rewrites
                # this line to the hub's text.
                self.conflict_handler(self.conn, op, path)
            elif verdict == "newer":
                # A change nobody has seen, made after the hub's last one: it wins, is
                # committed as a sync edit and reaches every other mirror.
                kernel_apply(self.conn, op, author=SYNC_AUTHOR)
                committed.add(op.node_id)
            # "quiet" (identical) and "stale" (an old version pasted back): the hub wins
            # silently; the write-back below rewrites this line to the hub's text.
            return
        new_id = kernel_apply(self.conn, op, author=SYNC_AUTHOR)
        if op.new_request is not None and new_id is not None:
            nr = op.new_request
            if nr.shape == "span":
                idx = nr.marker_line - 1
                if 0 <= idx < len(vault_lines):
                    vault_lines[idx], shifts[idx] = _mint_span_marker(
                        vault_lines[idx], nr, new_id, shifts.get(idx, 0)
                    )
            else:
                idx = nr.line_no - 1
                if 0 <= idx < len(vault_lines):
                    vault_lines[idx] = _render_new_line(nr, new_id)
        elif op.node_id is not None:
            # cross-file adopt (a move): may have committed the
            # vault's text/state to the hub head.
            committed.add(op.node_id)

    @staticmethod
    def _file_changed_at(path: str) -> float | None:
        """When the file last changed, or ``None`` if that cannot be trusted (a future time)."""
        try:
            mtime = os.stat(path).st_mtime
        except OSError:
            return None
        return mtime if mtime <= time.time() + JOIN_CLOCK_SLACK_SECONDS else None

    def _classify_join(
        self, op: Op, changed_at: float | None
    ) -> Literal["quiet", "stale", "newer", "conflict"]:
        """Decide a mirror join whose text differs from the hub's (M20-D), in this order.

        ``quiet``: same as the hub head. ``stale``: equal to an EARLIER version in the node's
        history (file times cannot see this: yesterday's text saved now has a fresh time).
        ``newer``: text the hub never held, in a file changed after the hub head's commit.
        ``conflict``: never-held text but the hub head is newer, or the file time is unreliable.
        """
        assert op.node_id is not None and op.vault_block is not None
        vb = op.vault_block
        node = store.get_node(self.conn, op.node_id)
        text_same = _body_line(node.body) == vb.text
        state_same = vb.kind != "task" or node.task_state == vb.task_state
        if text_same and state_same:
            return "quiet"
        versions = store.node_versions(self.conn, op.node_id)
        for v in versions:
            if v["is_head"]:
                continue
            if _body_line(v["body"]) == vb.text and (
                vb.kind != "task" or v["task_state"] == vb.task_state
            ):
                return "stale"
        if changed_at is None:
            return "conflict"
        head_ts = max((v["ts"] for v in versions if v["is_head"]), default=None)
        if head_ts is None:
            return "conflict"
        return "newer" if changed_at > datetime.fromisoformat(head_ts).timestamp() else "conflict"

    def _apply_existing(
        self,
        op: Op,
        path: str,
        hub_changed_map: dict[str, bool | None],
        committed: set[str],
    ) -> None:
        assert op.node_id is not None
        changed = hub_changed_map.get(op.node_id)
        if changed is None:
            # Node vanished from the hub entirely before we got to
            # it this cycle -- nothing left to reconcile against.
            return
        if not changed:
            kernel_apply(self.conn, op, author=SYNC_AUTHOR)
            if op.kind in ("modified", "checkbox_toggled"):
                committed.add(op.node_id)
            return
        vault_matches = op.vault_block is not None and _vault_matches_hub(
            self.conn, op.node_id, op.vault_block
        )
        if vault_matches:
            return  # convergent no-op: both sides already agree
        self.conflict_handler(self.conn, op, path)


# --- filesystem discovery for newly registered sync roots (task T11.3) -------


def discover_untracked_files(conn: sqlite3.Connection) -> list[str]:
    """Paths under a registered sync root that have no ``sync_files`` row yet.

    ``POST /v1/sync/roots`` only inserts a row (spec §4.10), so files already in the folder would
    never be reconciled; §4.8's "run ``on_change`` for every managed file" is read to include them
    (T11.1). Skips what the root's ``.tmignore`` excludes (``watcher.iter_tracked_markdown``) and a
    root directory that does not exist yet. Read-only.
    """
    known = {f["path"] for f in store.list_sync_files(conn)}
    discovered: list[str] = []
    for root in store.list_sync_roots(conn):
        root_dir = Path(root["root_path"])
        if not root_dir.exists():
            continue
        for candidate_str in iter_tracked_markdown(
            str(root_dir), load_tmignore(str(root_dir), logger)
        ):
            if candidate_str in known:
                continue
            known.add(candidate_str)
            discovered.append(candidate_str)
    return discovered


# --- startup reconcile / crash recovery (task T5.6) --------------------------


def reconcile_all(
    conn: sqlite3.Connection,
    origin: OriginTracker | None = None,
    *,
    projection: ProjectionIndex | None = None,
) -> dict[str, int]:
    """Reconcile every tracked file once: the daemon-startup entry point (spec §4.8), also crash
    recovery.

    ``on_change`` is idempotent (content-addressed objects, the quiet/hub-only shortcuts, the
    conflict-branch dedup), so a crash at any point is recovered by re-running the pipeline:
    whatever survived is exactly the (V, B, H) triple a fresh call re-derives. This only drives one
    shared ``Reconciler`` over ``store.list_sync_files``. ``origin`` defaults to a fresh tracker (a
    startup or rescan run shares no echo state with the watcher; an unsuppressed echo costs one
    idempotent zero-diff cycle). A row whose file vanished is skipped and counted (one missing file
    must not stop the rest; ``POST /sync/rescan`` shares this). Returns ``files_reconciled``,
    ``files_missing`` and ``reviews_open``.
    """
    if origin is None:
        from akasha.sync.origin import OriginTracker as _OriginTracker

        origin = _OriginTracker()

    reconciler = Reconciler(conn, origin, projection=projection)
    files_reconciled = 0
    files_missing = 0
    known_paths = [f["path"] for f in store.list_sync_files(conn)]
    # build-plan T11.3: also discover files that exist on disk under a
    # registered sync root but have no ``sync_files`` row yet (see
    # ``discover_untracked_files``'s docstring) -- computed BEFORE the
    # known-file loop runs so a file this same pass just adopted via
    # ``on_change`` below is never double-processed.
    discovered_paths = discover_untracked_files(conn)
    for path in known_paths + discovered_paths:
        try:
            reconciler.on_change(path)
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


# --- API-mutation-triggered reprojection (task T13.2) -------------------------


def project_node_change(
    conn: sqlite3.Connection,
    node_ids: list[str],
    origin_tracker: OriginTracker,
) -> list[str]:
    """Re-run ``on_change`` for the files that own ``node_ids`` after a hub-side edit
    (T13.2/T13.3).

    The three production ``on_change`` entry points (startup, watcher, rescan) never fire after
    ``PATCH /nodes/{id}``, the CLI or the UI, so §4.8's hub-only branch would not run in
    production. For each id a fresh ``ProjectionIndex`` finds EVERY owning file (mirrors, T19.4);
    paths are de-duplicated (two nodes in one file cost one call) and each gets a fresh
    ``Reconciler(conn, origin_tracker)`` sharing the caller's tracker so the write-back is
    echo-suppressed. A node in no file stays unfiled (no file-assignment mechanism: that would be
    new spec). A vanished path is skipped, as in ``reconcile_all``. Returns the paths reconciled,
    in first-encountered order; a second call is a quiet no-op.
    """
    index = ProjectionIndex.build(conn)
    paths: list[str] = []
    seen: set[str] = set()
    for node_id in node_ids:
        # T19.4: EVERY file holding the anchor (a mirror set), not just the
        # last one reconciled -- a hub-side edit must rewrite them all.
        for path in sorted(index.owners(node_id)):
            if path in seen:
                continue
            seen.add(path)
            paths.append(path)

    reconciled: list[str] = []
    for path in paths:
        reconciler = Reconciler(conn, origin_tracker)
        try:
            reconciler.on_change(path)
        except FileNotFoundError:
            continue
        reconciled.append(path)
    return reconciled
