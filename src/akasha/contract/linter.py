"""Contract linter: violation codes + certain-repair (build-plan T3.5, spec §4.7).

Pure functions only — no DB or filesystem I/O. Callers pass already-parsed
:class:`~akasha.contract.parser.BlockSet` values, raw vault text, and a
maturity lookup (callable or mapping). Repairs are structured, undoable
records; this module never mutates files.

Violation codes (spec §4.7):

* ``E_ID_CHECKSUM`` — EOL anchor whose id fails ``kernel.ids.validate``
* ``E_DUP_ID`` — same anchor id appears 2+ times in one file / BlockSet
* ``E_LOST_ANCHOR`` — base block text found in vault (fuzzy ≥ 0.9) without
  an anchor
* ``E_DELETED_S1`` — base block gone from vault (no fuzzy match) and
  maturity is S1+
* ``W_UNMANAGED_ANCHOR`` — advisory: ``ANCHOR_RE`` hit in an unmanaged file

Certain auto-repairs only (everything else → review item, never a guess):

1. ``E_LOST_ANCHOR`` where vault line body is byte-identical to the base
   block body except the missing anchor → re-insert anchor.
2. ``E_DUP_ID`` where at least one duplicate copy is byte-identical to its
   base line → that copy keeps the id; every other copy is proposed for
   ``^tm-new`` minting.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from difflib import SequenceMatcher, unified_diff
from typing import Literal

from pydantic import BaseModel

from akasha.contract import grammar
from akasha.contract.parser import Block, BlockSet
from akasha.kernel import ids
from akasha.kernel.ids import vault_anchor
from akasha.kernel.model import Maturity

# --- public constants ---------------------------------------------------------

ViolationCode = Literal[
    "E_ID_CHECKSUM",
    "E_DUP_ID",
    "E_LOST_ANCHOR",
    "E_DELETED_S1",
    "W_UNMANAGED_ANCHOR",
]

RepairAction = Literal["reinsert_anchor", "propose_tm_new"]

# Spec §4.7: fuzzy match threshold for E_LOST_ANCHOR.
LOST_ANCHOR_SIMILARITY = 0.9

# Maturity stages that make a full deletion a contract violation (S1+).
_S1_PLUS: frozenset[str] = frozenset({"S1", "S2", "S3", "S4"})

# Task line without a trailing anchor — used to recover comparable text from
# unanchored vault lines when hunting for E_LOST_ANCHOR.
_UNANCHORED_TASK_RE = re.compile(
    r"^(?P<indent>(?: {2})*)- \[(?P<state>[x ])\] (?P<text>\S.*?)\s*$"
)

MaturityLookup = Mapping[str, Maturity] | Callable[[str], Maturity | None]

# --- result models ------------------------------------------------------------


class Violation(BaseModel):
    """One detected contract violation (or advisory)."""

    code: ViolationCode
    id: str | None = None
    line_nos: list[int] = []
    message: str


class Repair(BaseModel):
    """Structured, undoable certain-repair proposal (no I/O).

    Callers apply ``after`` in place of ``before`` at ``line_no`` (1-indexed
    into the vault text that was linted). Logging/undo is the caller's job;
    this record carries enough to reverse the edit (``before``).
    """

    code: Literal["E_LOST_ANCHOR", "E_DUP_ID"]
    action: RepairAction
    id: str
    line_no: int
    before: str
    after: str


class ReviewItem(BaseModel):
    """Uncertain / non-auto-repairable finding — human review, never a guess."""

    code: ViolationCode
    id: str | None = None
    line_nos: list[int] = []
    message: str


class LintResult(BaseModel):
    """Outcome of :func:`lint`: all findings, split into repairs vs review."""

    violations: list[Violation] = []
    repairs: list[Repair] = []
    review_items: list[ReviewItem] = []


# --- helpers ------------------------------------------------------------------


def _maturity_of(lookup: MaturityLookup, node_id: str) -> Maturity | None:
    if callable(lookup):
        return lookup(node_id)
    return lookup.get(node_id)


def _canonical_block_line(block: Block) -> str:
    """Full vault line for a block, including its EOL anchor (spec §4.7)."""
    if block.kind == "task":
        indent = grammar.INDENT_UNIT * block.depth
        mark = "x" if block.task_state == "done" else " "
        return f"{indent}- [{mark}] {block.text} {vault_anchor(block.id)}"
    return f"{block.text} {vault_anchor(block.id)}"


def _block_body_without_anchor(block: Block) -> str:
    """Block line content with the trailing `` SP anchor`` removed."""
    if block.kind == "task":
        indent = grammar.INDENT_UNIT * block.depth
        mark = "x" if block.task_state == "done" else " "
        return f"{indent}- [{mark}] {block.text}"
    return block.text


def _comparable_text(line: str) -> str:
    """Text used for fuzzy similarity (block body text, no task chrome)."""
    task_m = _UNANCHORED_TASK_RE.match(line)
    if task_m:
        return task_m.group("text")
    par_m = grammar.MANAGED_PAR_RE.match(line)
    if par_m:
        return par_m.group("text")
    task_anchored = grammar.TASK_LINE_RE.match(line)
    if task_anchored:
        return task_anchored.group("text")
    return line.strip()


def _strip_front_matter(lines: list[str]) -> tuple[int, list[tuple[int, str]]]:
    """Return ``(body_start_index, [(line_no, line), ...])`` for the file body.

    ``line_no`` is 1-indexed into the original ``lines`` list (same convention
    as :mod:`akasha.contract.parser`).
    """
    if not lines or lines[0].strip() != "---":
        return 0, [(i + 1, lines[i]) for i in range(len(lines))]
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            body_start = i + 1
            return body_start, [(j + 1, lines[j]) for j in range(body_start, len(lines))]
    return 0, [(i + 1, lines[i]) for i in range(len(lines))]


def _iter_non_fence_lines(text: str) -> list[tuple[int, str]]:
    """Body lines outside fenced code blocks (spec §4.7: fences ignored)."""
    lines = text.split("\n")
    _, body = _strip_front_matter(lines)
    out: list[tuple[int, str]] = []
    in_fence = False
    for line_no, line in body:
        if grammar.FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        out.append((line_no, line))
    return out


def _eol_anchor_id(line: str) -> str | None:
    """Return the id8 of a real EOL anchor on ``line``, or None."""
    m = grammar.ANCHOR_EOL_RE.search(line)
    return m.group("id") if m else None


# --- detectors ----------------------------------------------------------------


def _detect_unmanaged_anchors(vault_text: str) -> list[tuple[Violation, ReviewItem]]:
    findings: list[tuple[Violation, ReviewItem]] = []
    for line_no, line in _iter_non_fence_lines(vault_text):
        for m in grammar.ANCHOR_RE.finditer(line):
            id_ = m.group("id")
            msg = (
                f"anchor ^tm-{id_} found in unmanaged file "
                f"(no matching tm: {grammar.CONTRACT_VERSION} front matter)"
            )
            v = Violation(code="W_UNMANAGED_ANCHOR", id=id_, line_nos=[line_no], message=msg)
            r = ReviewItem(code="W_UNMANAGED_ANCHOR", id=id_, line_nos=[line_no], message=msg)
            findings.append((v, r))
    return findings


def _detect_id_checksum(
    vault_lines: Sequence[tuple[int, str]],
) -> list[tuple[Violation, ReviewItem]]:
    """EOL anchors whose id fails ``ids.validate`` → always review (never repair)."""
    findings: list[tuple[Violation, ReviewItem]] = []
    seen: set[tuple[str, int]] = set()
    for line_no, line in vault_lines:
        id_ = _eol_anchor_id(line)
        if id_ is None:
            continue
        try:
            ids.validate(id_)
        except ids.IdError:
            key = (id_, line_no)
            if key in seen:
                continue
            seen.add(key)
            msg = f"malformed or checksum-invalid anchor id {id_!r}"
            v = Violation(code="E_ID_CHECKSUM", id=id_, line_nos=[line_no], message=msg)
            r = ReviewItem(code="E_ID_CHECKSUM", id=id_, line_nos=[line_no], message=msg)
            findings.append((v, r))
    return findings


def _detect_dup_id(
    vault_lines: Sequence[tuple[int, str]],
    base: BlockSet,
) -> tuple[list[Violation], list[Repair], list[ReviewItem]]:
    """Same EOL anchor id twice+ in one file.

    # SPEC-QUESTION (narrowest reading, see docs/spec-questions.md):
    # §4.7 says E_DUP_ID is "same anchor twice in vault (copy without cut)"
    # without stating whether the scope is one file or the whole vault.
    # Narrowest reading: one BlockSet / one file (the unit this linter
    # receives). Cross-file duplicate detection belongs to a higher layer.
    """
    by_id: dict[str, list[tuple[int, str]]] = {}
    for line_no, line in vault_lines:
        id_ = _eol_anchor_id(line)
        if id_ is None:
            continue
        # Skip structurally-shaped but checksum-invalid ids — those are
        # already reported as E_ID_CHECKSUM; dup semantics assume a real id.
        try:
            ids.validate(id_)
        except ids.IdError:
            continue
        by_id.setdefault(id_, []).append((line_no, line))

    violations: list[Violation] = []
    repairs: list[Repair] = []
    review_items: list[ReviewItem] = []

    for id_, copies in by_id.items():
        if len(copies) < 2:
            continue
        line_nos = [ln for ln, _ in copies]
        msg = f"anchor id {id_!r} appears {len(copies)} times in one file"
        violations.append(Violation(code="E_DUP_ID", id=id_, line_nos=line_nos, message=msg))

        base_block = base.blocks.get(id_)
        if base_block is None:
            # No base to compare against — cannot be certain which copy to keep.
            review_items.append(
                ReviewItem(code="E_DUP_ID", id=id_, line_nos=line_nos, message=msg)
            )
            continue

        canonical = _canonical_block_line(base_block)
        identical_idxs = [
            i for i, (_, line) in enumerate(copies) if line.rstrip("\r") == canonical
        ]

        if not identical_idxs:
            # Ambiguous: no copy is byte-identical to base → review, no guess.
            review_items.append(
                ReviewItem(code="E_DUP_ID", id=id_, line_nos=line_nos, message=msg)
            )
            continue

        # Certain: first byte-identical copy keeps the id; every other copy
        # is proposed for ^tm-new (including other identical copies).
        keeper = identical_idxs[0]
        anchor = vault_anchor(id_)
        for i, (line_no, line) in enumerate(copies):
            if i == keeper:
                continue
            # Replace the EOL anchor with ^tm-new, preserving leading SP / trailing ws.
            stripped = line.rstrip("\r")
            if not stripped.endswith(anchor):
                # Should not happen for copies collected via ANCHOR_EOL_RE; skip
                # rather than guess a rewrite.
                continue
            head = stripped[: -len(anchor)]
            trailing = line[len(stripped) :]
            after = f"{head}^tm-new{trailing}"
            repairs.append(
                Repair(
                    code="E_DUP_ID",
                    action="propose_tm_new",
                    id=id_,
                    line_no=line_no,
                    before=line,
                    after=after,
                )
            )

    return violations, repairs, review_items


def _detect_lost_and_deleted(
    vault_lines: Sequence[tuple[int, str]],
    base: BlockSet,
    vault: BlockSet,
    maturity: MaturityLookup,
) -> tuple[list[Violation], list[Repair], list[ReviewItem]]:
    """E_LOST_ANCHOR (certain or review) and E_DELETED_S1 (always review)."""
    violations: list[Violation] = []
    repairs: list[Repair] = []
    review_items: list[ReviewItem] = []

    # Candidate vault lines: no real EOL anchor (anchor deleted or never had one).
    candidates: list[tuple[int, str]] = []
    for line_no, line in vault_lines:
        if not line.strip():
            continue
        if _eol_anchor_id(line) is not None:
            continue
        # Skip pure fence/front-matter leftovers already filtered; also skip
        # lines that are only structural noise.
        candidates.append((line_no, line))

    used_candidate_idxs: set[int] = set()

    # Preserve base document order (BlockSet insertion order).
    for base_id, base_block in base.blocks.items():
        if base_id in vault.blocks:
            continue

        expected_body = _block_body_without_anchor(base_block)
        best_idx: int | None = None
        best_ratio = 0.0
        exact = False

        for i, (line_no, line) in enumerate(candidates):
            if i in used_candidate_idxs:
                continue
            body = line.rstrip("\r")
            if body == expected_body:
                best_idx = i
                best_ratio = 1.0
                exact = True
                break
            ratio = SequenceMatcher(
                None, _comparable_text(body), base_block.text
            ).ratio()
            if ratio >= LOST_ANCHOR_SIMILARITY and ratio > best_ratio:
                best_idx = i
                best_ratio = ratio
                exact = False

        if best_idx is not None:
            used_candidate_idxs.add(best_idx)
            line_no, line = candidates[best_idx]
            msg = (
                f"anchor for id {base_id!r} missing; vault text similarity "
                f"{best_ratio:.3f} to base"
            )
            violations.append(
                Violation(code="E_LOST_ANCHOR", id=base_id, line_nos=[line_no], message=msg)
            )
            if exact:
                # Certain repair: re-insert the anchor.
                body = line.rstrip("\r")
                after = f"{body} {vault_anchor(base_id)}"
                repairs.append(
                    Repair(
                        code="E_LOST_ANCHOR",
                        action="reinsert_anchor",
                        id=base_id,
                        line_no=line_no,
                        before=line,
                        after=after,
                    )
                )
            else:
                # Fuzzy but not exact → review, never guess.
                review_items.append(
                    ReviewItem(
                        code="E_LOST_ANCHOR", id=base_id, line_nos=[line_no], message=msg
                    )
                )
            continue

        # No fuzzy match at all → possible E_DELETED_S1.
        stage = _maturity_of(maturity, base_id)
        if stage is not None and stage in _S1_PLUS:
            msg = (
                f"managed block {base_id!r} deleted from vault and maturity "
                f"is {stage} (S1+); requires review"
            )
            violations.append(
                Violation(code="E_DELETED_S1", id=base_id, line_nos=[], message=msg)
            )
            review_items.append(
                ReviewItem(code="E_DELETED_S1", id=base_id, line_nos=[], message=msg)
            )
        # S0 (or unknown maturity): not a linter violation — hard-delete is OK.

    return violations, repairs, review_items


# --- public API ---------------------------------------------------------------


def lint(
    base: BlockSet,
    vault: BlockSet,
    vault_text: str,
    maturity: MaturityLookup | None = None,
) -> LintResult:
    """Detect §4.7 contract violations and emit certain-repair / review records.

    Parameters
    ----------
    base:
        Last-agreed :class:`BlockSet` (may be empty / unmanaged).
    vault:
        Current vault :class:`BlockSet` from ``parse(vault_text)``.
    vault_text:
        Raw vault file text (needed because ``BlockSet.blocks`` collapses
        duplicate ids and drops unanchored lines).
    maturity:
        Callable ``id -> Maturity | None`` or ``Mapping[str, Maturity]`` used
        for ``E_DELETED_S1``. Defaults to "all unknown" (no ``E_DELETED_S1``).

    Returns
    -------
    LintResult
        ``violations`` lists every finding; ``repairs`` holds only the two
        certain auto-repair classes; ``review_items`` holds everything else
        (including all ``E_ID_CHECKSUM``, ``E_DELETED_S1``, advisories, and
        ambiguous repair-eligible cases).
    """
    if maturity is None:
        maturity = {}

    result = LintResult()

    # Unmanaged files are never parsed for management; only the advisory.
    if not vault.managed:
        for v, r in _detect_unmanaged_anchors(vault_text):
            result.violations.append(v)
            result.review_items.append(r)
        return result

    vault_lines = _iter_non_fence_lines(vault_text)

    for v, r in _detect_id_checksum(vault_lines):
        result.violations.append(v)
        result.review_items.append(r)

    dup_v, dup_repairs, dup_review = _detect_dup_id(vault_lines, base)
    result.violations.extend(dup_v)
    result.repairs.extend(dup_repairs)
    result.review_items.extend(dup_review)

    lost_v, lost_repairs, lost_review = _detect_lost_and_deleted(
        vault_lines, base, vault, maturity
    )
    result.violations.extend(lost_v)
    result.repairs.extend(lost_repairs)
    result.review_items.extend(lost_review)

    return result


# --- pause & diff (formatter-storm guard, spec §4.7 / §4.8) --------------------

# Spec §4.7: pause when violations affect *more than* 25% of managed blocks.
PAUSE_THRESHOLD = 0.25


class PauseDecision(BaseModel):
    """Pure pause signal for a formatter storm — no I/O.

    Callers persist ``snapshot`` and enqueue ``review_item``; this module
    never writes the DB or filesystem.
    """

    snapshot: str
    review_item: ReviewItem


def _affected_block_ids(result: LintResult) -> set[str]:
    """Distinct non-None violation ids (numerator for the pause ratio)."""
    return {v.id for v in result.violations if v.id is not None}


def pause_threshold(result: LintResult, base: BlockSet) -> bool:
    """Return True if violations affect more than 25% of ``base``'s blocks.

    Denominator is ``len(base.blocks)`` (blocks that existed before this sync
    cycle). Numerator is the count of distinct violation ids (``id=None``
    ignored). The 25% boundary is exclusive: exactly 25% does not pause.

    # SPEC-QUESTION (narrowest reading, see docs/spec-questions.md):
    # §4.7 is silent on an empty base (no prior managed blocks). Narrowest
    # reading: never pause — there is no prior state to disturb, and the
    # ratio is undefined (division by zero).
    """
    total = len(base.blocks)
    if total == 0:
        return False
    return len(_affected_block_ids(result)) / total > PAUSE_THRESHOLD


def pause_and_diff(
    result: LintResult,
    base: BlockSet,
    base_text: str,
    vault_text: str,
) -> PauseDecision | None:
    """If the formatter-storm guard fires, return a pause decision; else None.

    When paused, the decision carries a snapshot of ``vault_text`` and exactly
    one :class:`ReviewItem` whose ``message`` is a ``difflib.unified_diff`` of
    ``base_text`` → ``vault_text``. No writes, no side effects.

    # SPEC-QUESTION (narrowest reading, see docs/spec-questions.md):
    # §4.7 says "open one review item with a diff" but does not name a
    # ViolationCode for that item. Narrowest reading: reuse ReviewItem with
    # ``message`` = the unified diff; ``code`` taken from the first violation
    # that has a non-None id (lint() order is deterministic); ``id`` /
    # ``line_nos`` left empty because the pause is file-scoped, not per-block.
    """
    if not pause_threshold(result, base):
        return None

    diff_text = "".join(
        unified_diff(
            base_text.splitlines(keepends=True),
            vault_text.splitlines(keepends=True),
            fromfile="base",
            tofile="vault",
        )
    )

    first = next(v for v in result.violations if v.id is not None)
    review_item = ReviewItem(
        code=first.code,
        id=None,
        line_nos=[],
        message=diff_text,
    )
    return PauseDecision(snapshot=vault_text, review_item=review_item)
