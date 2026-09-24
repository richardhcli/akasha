"""Contract linter: violation codes + certain-repair (build-plan T3.5, spec §4.7).

Pure functions only — no DB or filesystem I/O. Callers pass already-parsed
:class:`~akasha.contract.parser.BlockSet` values, raw vault text, and a
maturity lookup (callable or mapping). Repairs are structured, undoable
records; this module never mutates files.

Violation codes (spec §4.7, resolution per M20-G: a file is never paused; every finding
is resolved on the spot -- repaired, or the line gets a new node -- and only a deleted
S1+ node needs a human):

* ``E_ID_CHECKSUM`` — EOL anchor whose id fails ``kernel.ids.validate`` → the line's
  anchor is replaced by ``^tm-new`` (a new node)
* ``E_DUP_ID`` — same anchor id appears 2+ times in one file / BlockSet → the copy
  byte-identical to base (else the first copy) keeps the id; every other copy is
  replaced by ``^tm-new``
* ``E_LOST_ANCHOR`` — base block text found in vault (fuzzy ≥ 0.9) without an anchor →
  byte-identical except the anchor: re-insert it; otherwise the line becomes a new node
  (``^tm-new`` appended) and the old node follows the ordinary delete rules
* ``E_DELETED_S1`` — base block gone from vault (or replaced by a new node) and maturity
  is S1+ → the only review item; nothing is deleted

Every repair is structured and undoable (``before``/``after`` per line); this module never
mutates files. PRD F13 forbids re-attaching a damaged line to its old node by similarity,
so "changing the ID" -- a new node -- is the fallback wherever identity is ambiguous.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from difflib import SequenceMatcher
from typing import Literal

from pydantic import BaseModel

from akasha.contract import grammar
from akasha.contract.parser import Block, BlockSet, front_matter_end
from akasha.kernel import ids
from akasha.kernel.ids import contract_anchor
from akasha.kernel.model import Maturity

# --- public constants ---------------------------------------------------------

ViolationCode = Literal[
    "E_ID_CHECKSUM",
    "E_DUP_ID",
    "E_LOST_ANCHOR",
    "E_DELETED_S1",
]

RepairAction = Literal["reinsert_anchor", "propose_tm_new"]

# Spec §4.7: fuzzy match threshold for E_LOST_ANCHOR.
LOST_ANCHOR_SIMILARITY = 0.9

# Maturity stages that make a full deletion a contract violation (S1+).
_S1_PLUS: frozenset[str] = frozenset({"S1", "S2", "S3", "S4"})

# Task line without a trailing anchor — used to recover comparable text from
# unanchored vault lines when hunting for E_LOST_ANCHOR.
_UNANCHORED_TASK_RE = re.compile(r"^(?P<indent>(?: {2})*)- \[(?P<state>[x ])\] (?P<text>\S.*?)\s*$")

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

    code: Literal["E_LOST_ANCHOR", "E_DUP_ID", "E_ID_CHECKSUM"]
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
        return f"{indent}- [{mark}] {block.text} {contract_anchor(block.id)}"
    return f"{block.text} {contract_anchor(block.id)}"


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


def _iter_non_fence_lines(text: str) -> list[tuple[int, str]]:
    """Body lines outside fenced code blocks (spec §4.7: fences ignored)."""
    lines = text.split("\n")
    body_start = front_matter_end(lines)
    out: list[tuple[int, str]] = []
    in_fence = False
    for line_no, line in ((j + 1, lines[j]) for j in range(body_start, len(lines))):
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


def _line_with_new_marker(line: str, anchor: str) -> str | None:
    """``line`` with its trailing ``anchor`` replaced by ``^tm-new`` (one space before it)."""
    stripped = line.rstrip("\r")
    if not stripped.rstrip().endswith(anchor):
        return None  # should not happen for lines collected via ANCHOR_EOL_RE; never guess
    head = stripped.rstrip()[: -len(anchor)].rstrip()
    return f"{head} ^tm-new{line[len(stripped) :]}"


def _detect_id_checksum(
    vault_lines: Sequence[tuple[int, str]],
) -> tuple[list[Violation], list[Repair]]:
    """EOL anchors whose id fails ``ids.validate`` → the line gets a new node (M20-G)."""
    violations: list[Violation] = []
    repairs: list[Repair] = []
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
            violations.append(
                Violation(code="E_ID_CHECKSUM", id=id_, line_nos=[line_no], message=msg)
            )
            after = _line_with_new_marker(line, contract_anchor(id_))
            if after is not None:
                repairs.append(
                    Repair(
                        code="E_ID_CHECKSUM",
                        action="propose_tm_new",
                        id=id_,
                        line_no=line_no,
                        before=line,
                        after=after,
                    )
                )
    return violations, repairs


def _detect_dup_id(
    vault_lines: Sequence[tuple[int, str]],
    base: BlockSet,
) -> tuple[list[Violation], list[Repair]]:
    """Same EOL anchor id twice+ in one file → one copy keeps the id, the rest are new nodes.

    The keeper is the first copy byte-identical to base, else (no base block, or no copy
    identical to it) simply the first copy in document order: deterministic, never a
    similarity guess (PRD F13). Every other copy is proposed for ``^tm-new``.

    # SPEC-QUESTION (narrowest reading, see docs/spec-questions.md):
    # §4.7 says E_DUP_ID is "same anchor twice in vault (copy without cut)"
    # without stating whether the scope is one file or the whole vault.
    # Narrowest reading: one BlockSet / one file (the unit this linter
    # receives). The same anchor in different files is a mirror (M19).
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

    for id_, copies in by_id.items():
        if len(copies) < 2:
            continue
        line_nos = [ln for ln, _ in copies]
        msg = f"anchor id {id_!r} appears {len(copies)} times in one file"
        violations.append(Violation(code="E_DUP_ID", id=id_, line_nos=line_nos, message=msg))

        keeper = 0
        base_block = base.blocks.get(id_)
        if base_block is not None:
            canonical = _canonical_block_line(base_block)
            identical = [i for i, (_, line) in enumerate(copies) if line.rstrip("\r") == canonical]
            if identical:
                keeper = identical[0]
        anchor = contract_anchor(id_)
        for i, (line_no, line) in enumerate(copies):
            if i == keeper:
                continue
            after = _line_with_new_marker(line, anchor)
            if after is None:
                continue
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

    return violations, repairs


def _detect_dup_spans(
    file_lines: Sequence[str], current: BlockSet
) -> tuple[list[Violation], list[Repair]]:
    """A span id used again later in the same file: the copy becomes a new node (M20-G)."""
    violations: list[Violation] = []
    repairs: list[Repair] = []
    marker_len = len(grammar.SPAN_ID_OPEN) + len("tm-") + ids.ID_LEN + len(grammar.SPAN_ID_CLOSE)
    for dup in current.duplicate_spans:
        msg = f"span id {dup.id!r} appears more than once in one file"
        violations.append(
            Violation(code="E_DUP_ID", id=dup.id, line_nos=[dup.marker_line], message=msg)
        )
        idx = dup.marker_line - 1
        if not 0 <= idx < len(file_lines):
            continue
        line = file_lines[idx]
        new_marker = f"{grammar.SPAN_ID_OPEN}tm-new{grammar.SPAN_ID_CLOSE}"
        after = line[: dup.marker_col] + new_marker + line[dup.marker_col + marker_len :]
        repairs.append(
            Repair(
                code="E_DUP_ID",
                action="propose_tm_new",
                id=dup.id,
                line_no=dup.marker_line,
                before=line,
                after=after,
            )
        )
    return violations, repairs


def _restore_span_id(
    file_lines: Sequence[str], eligible: set[int], block: Block
) -> Repair | None:
    """Certain repair: the base span's braces and text survive exactly, only its id wrapper is gone.

    The one place ``{lead text trail}`` still appears (in eligible prose lines, not followed by an
    id wrapper) gets ``{tm-<id>}`` put back. Byte-identical text is the same certainty as an
    exact lost anchor; anything less is not guessed (PRD F13): the node follows the delete rules.
    """
    core = f"{grammar.SPAN_OPEN}{block.lead}{block.text}{block.trail}{grammar.SPAN_CLOSE}"
    joined = "\n".join(file_lines)
    starts = [m.start() for m in re.finditer(re.escape(core), joined)]
    hits: list[tuple[int, int]] = []  # (end line_no, column just past the closing token)
    for start in starts:
        end = start + len(core)
        if grammar.SPAN_ID_TAIL_RE.match(joined, end):
            continue  # it still has an id wrapper (its own, or another node's)
        first = joined.count("\n", 0, start) + 1
        last = joined.count("\n", 0, end) + 1
        if all(n in eligible for n in range(first, last + 1)):
            hits.append((last, end - (joined.rfind("\n", 0, end) + 1)))
    if len(hits) != 1:
        return None
    line_no, col = hits[0]
    line = file_lines[line_no - 1]
    marker = f"{grammar.SPAN_ID_OPEN}tm-{block.id}{grammar.SPAN_ID_CLOSE}"
    return Repair(
        code="E_LOST_ANCHOR",
        action="reinsert_anchor",
        id=block.id,
        line_no=line_no,
        before=line,
        after=line[:col] + marker + line[col:],
    )


def _detect_lost_and_deleted(
    vault_lines: Sequence[tuple[int, str]],
    base: BlockSet,
    vault: BlockSet,
    maturity: MaturityLookup,
    file_lines: Sequence[str] = (),
) -> tuple[list[Violation], list[Repair], list[ReviewItem]]:
    """E_LOST_ANCHOR (certain or review) and E_DELETED_S1 (always review)."""
    violations: list[Violation] = []
    repairs: list[Repair] = []
    review_items: list[ReviewItem] = []

    # Candidate vault lines: no real EOL anchor (anchor deleted or never had one). A line whose
    # EOL anchor is CORRUPTED (fails the checksum) is a candidate too, but for an EXACT match
    # only, comparing the text with the bad anchor stripped: byte-identical text is the same
    # certainty as a deleted anchor, so the base id is restored instead of a new node minted.
    candidates: list[tuple[int, str, str, bool]] = []  # (line_no, line, body, exact_only)
    for line_no, line in vault_lines:
        if not line.strip():
            continue
        eol_id = _eol_anchor_id(line)
        if eol_id is None:
            candidates.append((line_no, line, line.rstrip("\r"), False))
            continue
        try:
            ids.validate(eol_id)
        except ids.IdError:
            body = grammar.ANCHOR_EOL_RE.sub("", line.rstrip("\r"))
            candidates.append((line_no, line, body, True))

    used_candidate_idxs: set[int] = set()

    # Preserve base document order (BlockSet insertion order).
    for base_id, base_block in base.blocks.items():
        if base_id in vault.blocks:
            continue

        if base_block.kind == "span":
            # A span is never fuzzy-matched: its text survives exactly with only the id wrapper
            # gone (certain repair), or it is simply gone and the delete rules apply.
            fix = _restore_span_id(file_lines, {n for n, _ in vault_lines}, base_block)
            if fix is not None:
                violations.append(
                    Violation(
                        code="E_LOST_ANCHOR",
                        id=base_id,
                        line_nos=[fix.line_no],
                        message=f"span id wrapper for {base_id!r} missing; text intact",
                    )
                )
                repairs.append(fix)
                continue
            expected_body = ""
        else:
            expected_body = _block_body_without_anchor(base_block)
        is_span = base_block.kind == "span"
        best_idx: int | None = None
        best_ratio = 0.0
        exact = False

        for i, (line_no, line, body, exact_only) in enumerate([] if is_span else candidates):
            if i in used_candidate_idxs:
                continue
            if body == expected_body:
                best_idx = i
                best_ratio = 1.0
                exact = True
                break
            if exact_only:
                continue
            ratio = SequenceMatcher(None, _comparable_text(body), base_block.text).ratio()
            if ratio >= LOST_ANCHOR_SIMILARITY and ratio > best_ratio:
                best_idx = i
                best_ratio = ratio
                exact = False

        if best_idx is not None:
            used_candidate_idxs.add(best_idx)
            line_no, line, body, _exact_only = candidates[best_idx]
            msg = (
                f"anchor for id {base_id!r} missing; vault text similarity {best_ratio:.3f} to base"
            )
            violations.append(
                Violation(code="E_LOST_ANCHOR", id=base_id, line_nos=[line_no], message=msg)
            )
            if exact:
                # Certain repair: re-insert the anchor (replacing a corrupted one, if any).
                after = f"{body} {contract_anchor(base_id)}"
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
                continue  # same block, anchor restored: nothing was deleted
            else:
                # Fuzzy but not exact: identity is ambiguous (PRD F13 forbids re-anchoring by
                # similarity), so CHANGE THE ID (M20-G): the matched line becomes a new node and
                # the old node falls through to the ordinary delete rules below.
                after = f"{line.rstrip(chr(13))} ^tm-new"
                repairs.append(
                    Repair(
                        code="E_LOST_ANCHOR",
                        action="propose_tm_new",
                        id=base_id,
                        line_no=line_no,
                        before=line,
                        after=after,
                    )
                )

        # The block is gone from the vault (no line kept its identity) → possible E_DELETED_S1.
        stage = _maturity_of(maturity, base_id)
        if stage is not None and stage in _S1_PLUS:
            msg = (
                f"managed block {base_id!r} deleted from vault and maturity "
                f"is {stage} (S1+); requires review"
            )
            violations.append(Violation(code="E_DELETED_S1", id=base_id, line_nos=[], message=msg))
            review_items.append(
                ReviewItem(code="E_DELETED_S1", id=base_id, line_nos=[], message=msg)
            )
        # S0 (or unknown maturity): not a linter violation — hard-delete is OK.

    return violations, repairs, review_items


# --- public API ---------------------------------------------------------------


def lint(
    base: BlockSet,
    current: BlockSet,
    file_text: str,
    maturity: MaturityLookup | None = None,
) -> LintResult:
    """Detect §4.7 contract violations and emit certain-repair / review records.

    Parameters
    ----------
    base:
        Last-agreed :class:`BlockSet` (may be empty).
    current:
        Current :class:`BlockSet` from ``parse(file_text)``.
    file_text:
        Raw file text (needed because ``BlockSet.blocks`` collapses
        duplicate ids and drops unanchored lines).
    maturity:
        Callable ``id -> Maturity | None`` or ``Mapping[str, Maturity]`` used
        for ``E_DELETED_S1``. Defaults to "all unknown" (no ``E_DELETED_S1``).

    Returns
    -------
    LintResult
        ``violations`` lists every finding; ``repairs`` holds every resolution (re-insert an
        anchor, or give the line a new node via ``^tm-new``; M20-G); ``review_items`` holds
        only ``E_DELETED_S1``.
    """
    if maturity is None:
        maturity = {}

    result = LintResult()

    current_lines = _iter_non_fence_lines(file_text)

    ck_v, ck_repairs = _detect_id_checksum(current_lines)
    result.violations.extend(ck_v)
    result.repairs.extend(ck_repairs)

    dup_v, dup_repairs = _detect_dup_id(current_lines, base)
    result.violations.extend(dup_v)
    result.repairs.extend(dup_repairs)

    file_lines = file_text.split("\n")
    span_dup_v, span_dup_repairs = _detect_dup_spans(file_lines, current)
    result.violations.extend(span_dup_v)
    result.repairs.extend(span_dup_repairs)

    lost_v, lost_repairs, lost_review = _detect_lost_and_deleted(
        current_lines, base, current, maturity, file_lines
    )
    # A corrupted id on a line that is byte-identical to a base block is restored to that
    # block's id (above); the "give it a new node" checksum repair for the same line yields.
    restored = {r.line_no for r in lost_repairs if r.action == "reinsert_anchor"}
    result.repairs[:] = [
        r for r in result.repairs if not (r.code == "E_ID_CHECKSUM" and r.line_no in restored)
    ]
    result.violations.extend(lost_v)
    result.repairs.extend(lost_repairs)
    result.review_items.extend(lost_review)

    return result
