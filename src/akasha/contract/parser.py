"""Parser: managed-file contract text -> ``BlockSet`` (task T3.2, spec §4.7).

This module turns one managed file's raw text into the anchored
block/task structure described by the contract grammar v1
(``akasha.contract.grammar``). It is line-oriented and reuses every
token/regex from ``grammar.py`` verbatim — no pattern is redefined here.

File-level rule (spec §4.7, M20-C): there is no file marker. Every file is
parsed; one with no contract construct yields an empty :class:`BlockSet`. An
initial YAML front-matter block is **never interpreted**: its lines pass
through as raw lines (see :func:`front_matter_end`).

Text handling: this module does **not** normalize/canonicalize text (that is
``kernel/canonical.py``'s job, spec §4.3) — it simply splits the input on
``"\\n"`` and treats each resulting element as one logical line.

Fenced code (```` ``` ````-delimited, detected via ``grammar.FENCE_RE``) is
tracked line-by-line and its contents are ignored entirely, per spec §4.7:
"Anything inside fenced code blocks is ignored entirely."

Parent/child derivation for nested tasks: an indented ``task_line`` under
another task implies a `composes(parent->child)` edge downstream (creating
that edge is T1.4's ``store.create_edge`` job, not this module's); this
parser only records, per task block, the id of the nearest preceding task
block whose indent depth is smaller than its own (the narrowest reading of
"the nearest preceding task block at depth-1 is the parent" — a well-formed
document never skips a depth, so "nearest shallower" and "depth-1" coincide;
this parser does not reject documents that skip a depth, it just picks the
nearest shallower task as parent).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from akasha.contract import grammar

# --- BlockSet data model ------------------------------------------------------


class Block(BaseModel):
    """A single anchored block: a managed paragraph or a task line.

    Mirrors ``kernel.model.Node``'s convention of a single model with
    task-only fields left at their defaults for non-task blocks
    (``task_state``, ``depth``, ``parent_id``).
    """

    id: str
    kind: Literal["paragraph", "task"]
    text: str
    line_no: int  # 1-indexed source line number
    task_state: Literal["open", "done"] | None = None  # task blocks only
    depth: int = 0  # task blocks only; nesting depth per grammar indent/2
    parent_id: str | None = None  # task blocks only; nearest shallower task


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
    """A ``^tm-new`` marker: a user request to mint an id (spec §4.7).

    No id is minted here — ``kernel.ids`` mints and a caller rewrites the
    line elsewhere. This just captures enough for that caller to act.
    """

    line_no: int
    text: str  # the text (or task text) preceding the "^tm-new" marker
    shape: Literal["paragraph", "task"]
    task_state: Literal["open", "done"] | None = None  # shape == "task" only
    depth: int = 0  # shape == "task" only


class BlockSet(BaseModel):
    """Parsed structure of one file (spec §4.7).

    ``blocks`` is keyed by anchor id and holds both paragraph and task
    blocks in a single namespace (ids are globally unique per sync root
    regardless of block kind); iteration order follows insertion order,
    which mirrors document order since ``parse()`` walks top-to-bottom.

    Lossless-container field (task T5.8-2, fable-designed): a file is a
    lossless container -- lines that are not contract constructs (prose,
    blanks, fenced examples, unknown/malformed anchors, an un-minted
    ``^tm-new`` line, and an initial front-matter block) survive write-back
    verbatim by position. ``raw_lines`` holds those verbatim lines,
    1-indexed by source ``line_no`` (mirrors ``Block.line_no``'s convention).
    """

    blocks: dict[str, Block] = {}
    embeds: list[Embed] = []
    refs: list[Ref] = []
    new_requests: list[NewRequest] = []
    raw_lines: dict[int, str] = {}

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
    """Parse file text into a :class:`BlockSet` (spec §4.7).

    ``text`` is split on ``"\\n"``; no canonicalization is performed here. There is no
    file marker (M20-C): every file is parsed, and a file with no contract construct
    yields a :class:`BlockSet` with no blocks (``has_constructs()`` is false).

    Lossless-container classification (task T5.8-2, fable-designed): every source
    line is either a recognized contract construct (a ``Block``, a standalone
    ``Embed``/``Ref`` token, or a ``^tm-new`` :class:`NewRequest`) or a verbatim
    ``raw_lines`` entry -- never silently dropped. An initial front-matter block is
    all raw lines. The one exception is the single trailing ``""`` artifact
    ``str.split("\\n")`` produces when ``text`` ends with a newline (or is itself
    ``""``): that element is not a logical line and is never captured, which keeps
    ``render(parse(D)) == D`` exact for already-canonical (single-trailing-newline) ``D``.
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
    in_fence = False

    for i in range(body_start, len(lines)):
        line = lines[i]
        line_no = i + 1

        if grammar.FENCE_RE.match(line):
            in_fence = not in_fence
            raw_lines[line_no] = line
            continue
        if in_fence:
            raw_lines[line_no] = line
            continue

        task_m = grammar.TASK_LINE_RE.match(line)
        par_m = None if task_m else grammar.MANAGED_PAR_RE.match(line)
        new_m = None if (task_m or par_m) else grammar.NEW_LINE_RE.match(line)

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
            embed_full = grammar.EMBED_RE.fullmatch(line)
            ref_full = grammar.REF_RE.fullmatch(line)
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

        for em in grammar.EMBED_RE.finditer(line):
            embeds.append(Embed(path=em.group("path"), id=em.group("id"), line_no=line_no))
        for rf in grammar.REF_RE.finditer(line):
            refs.append(Ref(path=rf.group("path"), id=rf.group("id"), line_no=line_no))

    return BlockSet(
        blocks=blocks,
        embeds=embeds,
        refs=refs,
        new_requests=new_requests,
        raw_lines=raw_lines,
    )
