"""Round-trip property tests for the contract parser/renderer.

Build-plan task T3.4, spec §4.7, M3 DoD: "hypothesis: for generated
in-contract docs D, render(parse(D)) == D and for generated hub graphs G,
parse(render(G)) == G".

Two directions:

* **Direction 1** (``test_render_parse_round_trip_on_generated_documents``):
  generate canonical, in-contract vault *text* D directly (front matter +
  grammar-legal lines with valid checksummed id8s) and assert
  ``render(parse(D)) == D``.
* **Direction 2** (``test_parse_render_round_trip_on_generated_block_sets``):
  generate a valid :class:`~akasha.contract.parser.BlockSet` G directly via
  the parser's pydantic models and assert ``parse(render(G)) == G``.

Both strategies mint ids via ``kernel.ids.checksum`` (never hand-rolled
8-char strings) so every generated anchor is checksum-valid, and draw the
7-char *core* through hypothesis (rather than calling ``ids.mint()``, which
uses ``secrets`` and would not be reproducible/shrinkable).
"""

from __future__ import annotations

import string

from hypothesis import given, settings
from hypothesis import strategies as st

from akasha.contract import grammar
from akasha.contract.parser import Block, BlockSet, Embed, Ref, _parent_for_depth, parse
from akasha.contract.render import render
from akasha.kernel.canonical import canonicalize_text
from akasha.kernel.ids import CORE_LEN, A, checksum, vault_anchor

# --- shared building blocks --------------------------------------------------

# id8 core strategy: 7 chars from kernel.ids's base32 alphabet; the checksum
# char is always computed via kernel.ids.checksum (never hand-rolled), so
# every id produced below is guaranteed checksum-valid.
_id_core_strategy = st.text(alphabet=A, min_size=CORE_LEN, max_size=CORE_LEN)


def _id8(core: str) -> str:
    return core + checksum(core)


# Free text used for paragraph/task bodies and embed/ref paths. Deliberately
# excludes "^", "[", "]", "`", "#" so generated text can never accidentally
# form an anchor-like substring (``^tm-...``), a wiki-link delimiter
# (``[[``/``![[``), or a fence marker (`````) -- those are grammar-significant
# tokens per spec §4.7 and mixing them into free text would test regex
# ambiguity-resolution, not round-trip fidelity, which is out of scope here.
_SAFE_TEXT_ALPHABET = string.ascii_letters + string.digits + " .,!?-_':;()"

_safe_text_strategy = st.text(
    alphabet=_SAFE_TEXT_ALPHABET, min_size=1, max_size=24
).filter(lambda s: s.strip() == s)  # no leading/trailing whitespace (grammar: text SP anchor)

_PATH_ALPHABET = string.ascii_letters + string.digits + " _-"
_path_strategy = st.text(alphabet=_PATH_ALPHABET, min_size=1, max_size=16).filter(
    lambda s: s.strip() == s
)


# --- Direction 1: render(parse(D)) == D --------------------------------------


@st.composite
def _canonical_document_strategy(draw: st.DrawFn) -> str:
    """Build canonical, in-contract vault text directly (spec §4.7).

    Front matter (``tm: <CONTRACT_VERSION>``) is always present. The body is
    a sequence of grammar-legal lines -- managed paragraphs, task lines
    (with nesting depth bounded by ``max(prior depth) + 1``, mimicking a
    plausible nested list a human/daemon might write), standalone embeds,
    and standalone refs -- each with a unique, checksum-valid id8.
    """
    n_lines = draw(st.integers(min_value=0, max_value=8))
    cores = draw(
        st.lists(_id_core_strategy, min_size=n_lines, max_size=n_lines, unique=True)
    )
    ids = [_id8(c) for c in cores]

    lines: list[str] = []
    depth_max = -1  # no task seen yet; first task line must be depth 0

    for id_ in ids:
        kind = draw(st.sampled_from(["paragraph", "task", "embed", "ref"]))
        if kind == "paragraph":
            text = draw(_safe_text_strategy)
            lines.append(f"{text} {vault_anchor(id_)}")
        elif kind == "task":
            text = draw(_safe_text_strategy)
            state = draw(st.sampled_from(["x", " "]))
            depth = draw(st.integers(min_value=0, max_value=depth_max + 1))
            depth_max = depth
            indent = grammar.INDENT_UNIT * depth
            lines.append(f"{indent}- [{state}] {text} {vault_anchor(id_)}")
        elif kind == "embed":
            path = draw(_path_strategy)
            lines.append(f"![[{path}#{vault_anchor(id_)}]]")
        else:  # ref
            path = draw(_path_strategy)
            lines.append(f"[[{path}#{vault_anchor(id_)}]]")

    body = "".join(line + "\n" for line in lines)
    return f"---\ntm: {grammar.CONTRACT_VERSION}\n---\n{body}"


@settings(max_examples=100, deadline=None)
@given(_canonical_document_strategy())
def test_render_parse_round_trip_on_generated_documents(doc: str) -> None:
    """DoD: render(parse(D)) == D for generated canonical in-contract D."""
    # Sanity check on the generator itself: D must already be canonical
    # (LF, single trailing newline, no trailing whitespace per line) for
    # this property to be meaningful -- render()'s output is canonical by
    # construction (spec §4.3), so a non-canonical D could never round-trip.
    assert canonicalize_text(doc) == doc

    block_set = parse(doc)
    assert render(block_set) == doc


# --- Direction 2: parse(render(G)) == G --------------------------------------


@st.composite
def _block_set_strategy(draw: st.DrawFn) -> BlockSet:
    """Build a random valid, managed :class:`BlockSet` directly.

    Every item (block/embed/ref) is assigned a unique, strictly increasing
    ``line_no`` starting at 4 (the first body line after the 3-line front
    matter, matching the convention observed in the parser/render unit
    tests). Because line numbers are already unique and monotonic, and no
    embed/ref shares a ``line_no`` with a block, ``render()`` emits exactly
    one line per item in draw order and re-parsing recovers the identical
    ``line_no`` for each -- so full structural equality (including
    ``line_no``) is a meaningful, checkable property here rather than one
    that has to be special-cased away.

    Task nesting is generated through the parser's own
    ``_parent_for_depth`` stack helper so ``parent_id`` is *exactly* what
    ``parse()`` would derive for the corresponding depth sequence -- this
    is what makes exact equality (not just a subset-of-fields comparison)
    achievable in Direction 2.

    Scoping decisions (documented per task T3.4 step 2):

    * ``new_requests`` are excluded from G entirely. Per render.py's own
      docstring, "``^tm-new`` requests are hub-side minting inputs, not
      hub projections, and are not emitted" -- they have no textual vault
      projection to round-trip through, so including them wouldn't test
      render/parse fidelity, it would just assert data loss that render.py
      documents as intentional (spec §4.7: "`^tm-new` ⇒ daemon mints an ID
      and rewrites the line" -- the mint+rewrite is a caller-side
      operation, not something render() does).
    * Embeds/refs are kept on lines of their own (never sharing a
      ``line_no`` with a block). render.py's docstring states that an
      embed/ref sharing a block's ``line_no`` is assumed to already be
      textually inline in that block's ``text`` (and is therefore skipped
      on output) -- constructing such a case would require synthesizing
      block text containing a matching wiki-link substring, which is a
      separate (and separately testable) concern from the
      render/parse inverse property itself.
    """
    n_items = draw(st.integers(min_value=0, max_value=8))
    cores = draw(
        st.lists(_id_core_strategy, min_size=n_items, max_size=n_items, unique=True)
    )
    ids = [_id8(c) for c in cores]

    blocks: dict[str, Block] = {}
    embeds: list[Embed] = []
    refs: list[Ref] = []
    task_stack: list[tuple[int, str]] = []
    depth_max = -1
    line_no = 4

    for id_ in ids:
        kind = draw(st.sampled_from(["paragraph", "task", "embed", "ref"]))
        if kind == "paragraph":
            text = draw(_safe_text_strategy)
            blocks[id_] = Block(id=id_, kind="paragraph", text=text, line_no=line_no)
        elif kind == "task":
            text = draw(_safe_text_strategy)
            state = draw(st.sampled_from(["open", "done"]))
            depth = draw(st.integers(min_value=0, max_value=depth_max + 1))
            depth_max = depth
            parent_id = _parent_for_depth(task_stack, depth)
            blocks[id_] = Block(
                id=id_,
                kind="task",
                text=text,
                line_no=line_no,
                task_state=state,
                depth=depth,
                parent_id=parent_id,
            )
            task_stack.append((depth, id_))
        elif kind == "embed":
            path = draw(_path_strategy)
            embeds.append(Embed(path=path, id=id_, line_no=line_no))
        else:  # ref
            path = draw(_path_strategy)
            refs.append(Ref(path=path, id=id_, line_no=line_no))
        line_no += 1

    return BlockSet(
        managed=True,
        contract_version=grammar.CONTRACT_VERSION,
        blocks=blocks,
        embeds=embeds,
        refs=refs,
        new_requests=[],
    )


@settings(max_examples=100, deadline=None)
@given(_block_set_strategy())
def test_parse_render_round_trip_on_generated_block_sets(block_set: BlockSet) -> None:
    """DoD: parse(render(G)) == G for generated hub graphs G (scoped per docstring above)."""
    rendered = render(block_set)
    reparsed = parse(rendered)
    assert reparsed == block_set
