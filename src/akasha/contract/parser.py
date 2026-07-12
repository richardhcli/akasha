"""Parser: vault text -> ``BlockSet`` (build-plan task T3.2, spec §4.7).

This module turns the raw text of a single vault file into the anchored
block/task structure described by the contract grammar v1
(``akasha.contract.grammar``). It is line-oriented and reuses every
token/regex from ``grammar.py`` verbatim — no pattern is redefined here.

File-level rule (spec §4.7): a file is only "managed" if its YAML front
matter contains a `tm: <version>` line matching ``grammar.CONTRACT_VERSION``.
Files without that marker are "never parsed for management" — ``parse()``
returns an empty, ``managed=False`` :class:`BlockSet` for them rather than
raising.

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
    """Parsed structure of one vault file (spec §4.7).

    ``blocks`` is keyed by anchor id and holds both paragraph and task
    blocks in a single namespace (ids are globally unique per vault
    regardless of block kind); iteration order follows insertion order,
    which mirrors document order since ``parse()`` walks top-to-bottom.
    """

    managed: bool
    contract_version: int | None = None
    blocks: dict[str, Block] = {}
    embeds: list[Embed] = []
    refs: list[Ref] = []
    new_requests: list[NewRequest] = []


# --- front matter ------------------------------------------------------------


def _front_matter_bounds(lines: list[str]) -> tuple[list[str], int]:
    """Return ``(front_matter_lines, body_start_index)``.

    ``body_start_index`` is the index into ``lines`` of the first line after
    the closing ``---`` delimiter. If there is no well-formed front-matter
    block (opening and closing ``---`` delimiter lines), returns
    ``([], 0)`` — the whole file is then treated as file body with no
    front matter, which makes it unmanaged (no `tm:` key found).
    """
    if not lines or lines[0].strip() != "---":
        return [], 0
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[1:i], i + 1
    return [], 0


def _contract_version(front_matter_lines: list[str]) -> int | None:
    for line in front_matter_lines:
        m = grammar.FRONT_MATTER_TM_RE.match(line)
        if m:
            return int(m.group("version"))
    return None


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
    """Parse vault file text into a :class:`BlockSet` (spec §4.7).

    ``text`` is split on ``"\\n"``; no canonicalization is performed here.
    Files lacking a front-matter `tm: <version>` line matching
    ``grammar.CONTRACT_VERSION`` are unmanaged: returns an empty
    ``BlockSet(managed=False)`` rather than raising.
    """
    lines = text.split("\n")
    front_matter_lines, body_start = _front_matter_bounds(lines)
    version = _contract_version(front_matter_lines)

    # SPEC-QUESTION (narrowest reading, see docs/spec-questions.md T3.2
    # entry): spec §4.7 says "front-matter key `tm: 1` marks a managed
    # file" without specifying behavior for a `tm:` value that does not
    # match CONTRACT_VERSION (e.g. a future contract version). Narrowest
    # reading: only an exact match to CONTRACT_VERSION counts as managed;
    # anything else (including a present-but-mismatched `tm:` key) is
    # treated as unmanaged rather than guessing at a migration path.
    if version != grammar.CONTRACT_VERSION:
        return BlockSet(managed=False)

    blocks: dict[str, Block] = {}
    embeds: list[Embed] = []
    refs: list[Ref] = []
    new_requests: list[NewRequest] = []
    task_stack: list[tuple[int, str]] = []
    in_fence = False

    for i in range(body_start, len(lines)):
        line = lines[i]
        line_no = i + 1

        if grammar.FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        task_m = grammar.TASK_LINE_RE.match(line)
        par_m = None if task_m else grammar.MANAGED_PAR_RE.match(line)
        new_m = None if (task_m or par_m) else grammar.NEW_LINE_RE.match(line)

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

        for em in grammar.EMBED_RE.finditer(line):
            embeds.append(Embed(path=em.group("path"), id=em.group("id"), line_no=line_no))
        for rf in grammar.REF_RE.finditer(line):
            refs.append(Ref(path=rf.group("path"), id=rf.group("id"), line_no=line_no))

    return BlockSet(
        managed=True,
        contract_version=version,
        blocks=blocks,
        embeds=embeds,
        refs=refs,
        new_requests=new_requests,
    )
