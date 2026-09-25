"""Parser: contract text -> ``BlockSet`` (spec §4.7).

Line-oriented, reusing every token and regex of ``grammar.py``. There is no file marker (M20-C):
every file is parsed and one with no construct yields an empty :class:`BlockSet`; an initial
front-matter block is never interpreted (its lines pass through as raw lines, see
:func:`front_matter_end`). Text is not canonicalized here (``kernel/canonical.py``, §4.3): the
input is split on ``"\\n"``, one logical line per element. Fenced code is tracked line by line and
ignored.

Nesting: an indented task records only the id of the nearest preceding SHALLOWER task block as its
parent (creating the ``composes`` edge is ``store.create_edge``'s job); a skipped depth is
tolerated.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

from akasha.contract import grammar
from akasha.kernel import ids

# --- BlockSet data model ------------------------------------------------------


class Block(BaseModel):
    """An anchored block: a managed paragraph, a task line, or a span. Task-only fields
    (``task_state``, ``depth``, ``parent_id``) stay at their defaults otherwise.

    A ``span`` (M20-A/B/E) shares only the text between its braces, possibly across lines:
    ``line_no``/``col`` locate its opening brace, ``end_line_no``/``end_col`` the character just
    past its ``{tm-id}`` wrapper, ``lead``/``trail`` the whitespace just inside the braces: per
    file, never part of ``text`` (which is trimmed) and never edited by the daemon.
    """

    id: str
    kind: Literal["paragraph", "task", "span"]
    text: str
    line_no: int  # 1-indexed source line number
    task_state: Literal["open", "done"] | None = None  # task blocks only
    depth: int = 0  # task blocks only; nesting depth per grammar indent/2
    parent_id: str | None = None  # task blocks only; nearest shallower task
    end_line_no: int = 0  # span blocks only
    col: int = 0  # span blocks only
    end_col: int = 0  # span blocks only
    lead: str = ""  # span blocks only
    trail: str = ""  # span blocks only


class Embed(BaseModel):
    """A read-only transclusion: ``![[path#^tm-id8]]`` (spec §4.7)."""

    path: str
    id: str
    line_no: int


class Ref(BaseModel):
    """An inline reference: ``[[path#^tm-id8]]`` (spec §4.7)."""

    path: str
    id: str
    line_no: int


class NewRequest(BaseModel):
    """A ``^tm-new`` marker (or a ``{text}{tm-new}`` span): a user request to mint an id.

    No id is minted here — ``kernel.ids`` mints and a caller rewrites the
    line elsewhere. This just captures enough for that caller to act. For a ``span`` request
    ``line_no`` is where the span opens and ``marker_line``/``marker_col`` locate its
    ``{tm-new}`` marker (the only thing rewritten).
    """

    line_no: int
    text: str  # the text (or task text) preceding the "^tm-new" marker
    shape: Literal["paragraph", "task", "span"]
    task_state: Literal["open", "done"] | None = None  # shape == "task" only
    depth: int = 0  # shape == "task" only
    marker_line: int = 0  # shape == "span" only
    marker_col: int = 0  # shape == "span" only


class DuplicateSpan(BaseModel):
    """A span whose id is already used earlier in the same file (like ``E_DUP_ID``, M20-G)."""

    id: str
    marker_line: int  # the line holding this copy's ``{tm-id}`` wrapper
    marker_col: int


class BlockSet(BaseModel):
    """Parsed structure of one file (spec §4.7).

    ``blocks`` maps anchor id to block (paragraphs, tasks and spans share one namespace, ids being
    unique per sync root) in document order. A file is a lossless container: every line that is not
    a construct (prose, blanks, fenced examples, unknown anchors, an un-minted ``^tm-new``, a
    front-matter block) survives write-back verbatim in ``raw_lines``, keyed by 1-indexed line
    number.
    """

    blocks: dict[str, Block] = {}
    embeds: list[Embed] = []
    refs: list[Ref] = []
    new_requests: list[NewRequest] = []
    raw_lines: dict[int, str] = {}
    duplicate_spans: list[DuplicateSpan] = []

    def has_constructs(self) -> bool:
        """True iff the file holds anything to project (a block, ``^tm-new``, embed or ref)."""
        return bool(self.blocks or self.new_requests or self.embeds or self.refs)


# --- front matter ------------------------------------------------------------


def front_matter_end(lines: list[str]) -> int:
    """Index of the first line after an initial front-matter block, or ``0`` if there is none.

    A front-matter block is an opening ``---`` on the first line and a closing ``---``
    later, **containing no contract construct** (no end-of-line anchor and no ``^tm-new``):
    a thematic break at the top of a note that happens to be followed by another one must
    not swallow real blocks. The daemon never interprets or edits these lines (M20-C).
    """
    if not lines or lines[0].strip() != "---":
        return 0
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            inner = lines[1:i]
            if any(
                grammar.ANCHOR_EOL_RE.search(x) or grammar.NEW_MARKER_EOL_RE.search(x)
                for x in inner
            ):
                return 0
            return i + 1
    return 0


# --- spans -----------------------------------------------------------------------


class _FoundSpan(BaseModel):
    id: str  # a valid id8, or "new"
    text: str
    lead: str
    trail: str
    line_no: int
    col: int
    end_line_no: int
    end_col: int
    marker_col: int  # column of the id wrapper in end_line_no


def _token_re() -> re.Pattern[str]:
    tokens = sorted({grammar.SPAN_OPEN, grammar.SPAN_CLOSE}, key=len, reverse=True)
    return re.compile("|".join(re.escape(t) for t in tokens))


def _find_span_at(
    lines: list[str],
    prose: set[int],
    start_line: int,
    start_col: int,
    budget: list[int],
    tokens: re.Pattern[str],
) -> _FoundSpan | None:
    """The span whose opening token is at (``start_line``, ``start_col``), or ``None``.

    Counts open/close tokens; the span ends at the first close that returns the depth to 0 AND is
    immediately followed by the id wrapper (``{tm-<id8>}`` / ``{tm-new}``). Anything else (depth 0
    with no wrapper, unbalanced braces, a non-prose line in range, or a cap on lines, characters or
    the file's token ``budget``) makes the token literal prose.
    """
    open_tok = grammar.SPAN_OPEN
    depth = 0
    chars = 0
    line_no = start_line
    col = start_col
    while line_no <= len(lines) and line_no - start_line < grammar.SPAN_MAX_LINES:
        if line_no not in prose:
            return None
        line = lines[line_no - 1]
        for m in tokens.finditer(line, col):
            budget[0] -= 1
            if budget[0] < 0:
                return None
            if m.group() == open_tok:
                depth += 1
                continue
            depth -= 1
            if depth == 0:
                tail = grammar.SPAN_ID_TAIL_RE.match(line, m.end())
                if tail is None:
                    return None
                return _finish_span(lines, start_line, start_col, line_no, m.start(), tail)
        chars += len(line) - col
        if chars > grammar.SPAN_MAX_CHARS:
            return None
        line_no += 1
        col = 0
    return None


def _finish_span(
    lines: list[str],
    start_line: int,
    start_col: int,
    end_line: int,
    close_col: int,
    tail: re.Match[str],
) -> _FoundSpan | None:
    id_ = tail.group("id")
    if id_ != "new" and not ids.is_valid(id_):
        return None  # a checksum-invalid id names nothing: the braces are just prose
    open_len = len(grammar.SPAN_OPEN)
    if start_line == end_line:
        inner = lines[start_line - 1][start_col + open_len : close_col]
    else:
        inner = "\n".join(
            [
                lines[start_line - 1][start_col + open_len :],
                *lines[start_line : end_line - 1],
                lines[end_line - 1][:close_col],
            ]
        )
    text = inner.strip()
    if not text:
        return None  # nothing to share
    lead = inner[: len(inner) - len(inner.lstrip())]
    trail = inner[len(inner.rstrip()) :]
    return _FoundSpan(
        id=id_,
        text=text,
        lead=lead,
        trail=trail,
        line_no=start_line,
        col=start_col,
        end_line_no=end_line,
        end_col=tail.end(),
        marker_col=close_col + len(grammar.SPAN_CLOSE),
    )


def _scan_spans(lines: list[str], prose: set[int]) -> list[_FoundSpan]:
    """Every span in the prose lines, in document order (each text position used once)."""
    found: list[_FoundSpan] = []
    open_tok = grammar.SPAN_OPEN
    budget = [grammar.SPAN_SCAN_BUDGET]
    tokens = _token_re()
    for line_no in sorted(prose):
        if budget[0] < 0:
            break  # the file is too brace-heavy to scan further: the rest is prose
        line = lines[line_no - 1]
        if open_tok not in line:
            continue
        # a span that started on an earlier line may already own the start of this one
        floor = 0
        if found and found[-1].end_line_no == line_no:
            floor = found[-1].end_col
        elif found and found[-1].end_line_no > line_no:
            continue
        col = line.find(open_tok, floor)
        while col != -1:
            span = _find_span_at(lines, prose, line_no, col, budget, tokens)
            if span is None:
                col = line.find(open_tok, col + len(open_tok))
                continue
            found.append(span)
            if span.end_line_no != line_no:
                break  # the rest of this line belongs to the span's last line: handled there
            col = line.find(open_tok, span.end_col)
    return found


# --- parent/child stack --------------------------------------------------------


def _parent_for_depth(stack: list[tuple[int, str]], depth: int) -> str | None:
    """Pop stack entries at depth >= ``depth``; return the id atop what's left.

    ``stack`` holds ``(depth, id)`` pairs for the current chain of ancestor
    tasks seen so far, shallowest first. Mutates ``stack`` in place.
    """
    while stack and stack[-1][0] >= depth:
        stack.pop()
    return stack[-1][1] if stack else None


# --- parse ---------------------------------------------------------------------


def parse(text: str) -> BlockSet:
    """Parse file text into a :class:`BlockSet` (spec §4.7); no canonicalization.

    Every line is a construct (a ``Block``, standalone ``Embed``/``Ref``, or ``^tm-new``
    :class:`NewRequest`) or a verbatim ``raw_lines`` entry, never dropped, so ``render(parse(D)) ==
    D`` for canonical ``D``; the one exception is the empty element ``split`` yields after a
    trailing newline. A front-matter block is all raw lines, and a file with no construct has
    ``has_constructs()`` false.
    """
    raw_split = text.split("\n")
    lines = raw_split[:-1] if raw_split and raw_split[-1] == "" else raw_split

    body_start = front_matter_end(lines)

    blocks: dict[str, Block] = {}
    embeds: list[Embed] = []
    refs: list[Ref] = []
    new_requests: list[NewRequest] = []
    raw_lines: dict[int, str] = dict(enumerate(lines[:body_start], start=1))
    task_stack: list[tuple[int, str]] = []
    prose_lines: set[int] = set()  # plain lines: the only place a span may live
    in_fence = False

    for i in range(body_start, len(lines)):
        line = lines[i]
        line_no = i + 1

        if "```" in line and grammar.FENCE_RE.match(line):
            in_fence = not in_fence
            raw_lines[line_no] = line
            continue
        if in_fence:
            raw_lines[line_no] = line
            continue

        # every block/request form ends in "^tm-...": prose lines skip all three patterns
        has_marker = "^tm-" in line
        task_m = grammar.TASK_LINE_RE.match(line) if has_marker else None
        par_m = grammar.MANAGED_PAR_RE.match(line) if has_marker and not task_m else None
        new_m = grammar.NEW_LINE_RE.match(line) if has_marker and not (task_m or par_m) else None

        matched_structural = True
        if task_m:
            depth = grammar.indent_depth(task_m.group("indent"))
            state: Literal["open", "done"] = "done" if task_m.group("state") == "x" else "open"
            id_ = task_m.group("id")
            parent_id = _parent_for_depth(task_stack, depth)
            blocks[id_] = Block(
                id=id_,
                kind="task",
                text=task_m.group("text"),
                line_no=line_no,
                task_state=state,
                depth=depth,
                parent_id=parent_id,
            )
            task_stack.append((depth, id_))
        elif par_m:
            id_ = par_m.group("id")
            blocks[id_] = Block(
                id=id_,
                kind="paragraph",
                text=par_m.group("text"),
                line_no=line_no,
            )
        elif new_m:
            if new_m.group("task_text") is not None:
                new_depth = grammar.indent_depth(new_m.group("indent") or "")
                new_state: Literal["open", "done"] = (
                    "done" if new_m.group("state") == "x" else "open"
                )
                new_requests.append(
                    NewRequest(
                        line_no=line_no,
                        text=new_m.group("task_text"),
                        shape="task",
                        task_state=new_state,
                        depth=new_depth,
                    )
                )
            else:
                new_requests.append(
                    NewRequest(
                        line_no=line_no,
                        text=new_m.group("text"),
                        shape="paragraph",
                    )
                )
            # An un-minted ^tm-new marker also survives as a raw line (spec
            # T5.8-2 rule 5): render() never emits NewRequests, so without
            # this the line would vanish on write-back if, for any reason,
            # it isn't rewritten to a real anchor before the final parse.
            raw_lines[line_no] = line
        else:
            matched_structural = False

        if not matched_structural:
            # Rule 6: a line that is EXACTLY one standalone embed/ref token
            # becomes that Embed/Ref only -- not a raw line -- so render()'s
            # existing block-free standalone-embed/ref reconstruction (and
            # Direction-2 round-trip equality) is preserved verbatim.
            embed_full = grammar.EMBED_RE.fullmatch(line) if "[[" in line else None
            ref_full = grammar.REF_RE.fullmatch(line) if "[[" in line else None
            if embed_full:
                embeds.append(
                    Embed(path=embed_full.group("path"), id=embed_full.group("id"), line_no=line_no)
                )
                continue
            if ref_full:
                refs.append(
                    Ref(path=ref_full.group("path"), id=ref_full.group("id"), line_no=line_no)
                )
                continue
            # Rule 7: everything else (blanks, prose, mid-line-anchor text,
            # prose with inline wiki-links, multi-embed lines) survives
            # verbatim as a raw line; inline embed/ref link metadata is
            # still recorded via the finditer capture below.
            raw_lines[line_no] = line
            prose_lines.add(line_no)

        if "[[" in line:
            for em in grammar.EMBED_RE.finditer(line):
                embeds.append(Embed(path=em.group("path"), id=em.group("id"), line_no=line_no))
            for rf in grammar.REF_RE.finditer(line):
                refs.append(Ref(path=rf.group("path"), id=rf.group("id"), line_no=line_no))

    duplicate_spans: list[DuplicateSpan] = []
    if prose_lines:
        for span in _scan_spans(lines, prose_lines):
            if span.id == "new":
                new_requests.append(
                    NewRequest(
                        line_no=span.line_no,
                        text=span.text,
                        shape="span",
                        marker_line=span.end_line_no,
                        marker_col=span.marker_col,
                    )
                )
            elif span.id in blocks:
                duplicate_spans.append(
                    DuplicateSpan(
                        id=span.id, marker_line=span.end_line_no, marker_col=span.marker_col
                    )
                )
            else:
                blocks[span.id] = Block(
                    id=span.id,
                    kind="span",
                    text=span.text,
                    line_no=span.line_no,
                    end_line_no=span.end_line_no,
                    col=span.col,
                    end_col=span.end_col,
                    lead=span.lead,
                    trail=span.trail,
                )
        if any(b.kind == "span" for b in blocks.values()):
            blocks = dict(sorted(blocks.items(), key=lambda kv: (kv[1].line_no, kv[1].col)))
        new_requests.sort(key=lambda r: (r.line_no, r.marker_col))

    return BlockSet(
        blocks=blocks,
        embeds=embeds,
        refs=refs,
        new_requests=new_requests,
        raw_lines=raw_lines,
        duplicate_spans=duplicate_spans,
    )
