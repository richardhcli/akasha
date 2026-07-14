"""Round-trip property tests for the contract parser/renderer.

Build-plan task T3.4, spec §4.7, M3 DoD: "hypothesis: for generated
in-contract docs D, render(parse(D)) == D and for generated hub graphs G,
parse(render(G)) == G".

Two directions:

* **Direction 1** (``test_render_parse_round_trip_on_generated_documents``):
  generate canonical, in-contract vault *text* D directly (front matter +
  grammar-legal lines with valid checksummed id8s) and assert
  ``render(parse(D)) == D``. Task T5.8-2 (human-decided 2026-07-13,
  fable-designed: a managed file is a lossless container) extended this
  generator to also interleave non-contract-construct lines -- free prose,
  blank lines, fenced code blocks (including a fake ``^tm-`` anchor inside
  one, which must survive verbatim since fenced content is "ignored
  entirely" by the grammar, not stripped) -- and to sometimes build a
  non-canonical front-matter block (extra ``title:``/``tags:`` keys). This
  is the property that actually locks the T5.8-2 losslessness invariant:
  every one of those non-block line kinds must round-trip byte-for-byte
  through ``parse()``'s new ``raw_lines``/``front_matter`` fields and back
  out through ``render()``.
* **Direction 2** (``test_parse_render_round_trip_on_generated_block_sets``):
  generate a valid :class:`~akasha.contract.parser.BlockSet` G directly via
  the parser's pydantic models and assert ``parse(render(G)) == G``. G is
  built with ``raw_lines``/``front_matter`` left at their defaults
  (``{}``/``None``): ``render(G)`` then emits canonical front matter plus
  pure block/standalone-embed/ref lines only, so re-parsing recovers
  ``raw_lines == {}`` and ``front_matter is None`` -- full equality holds
  without special-casing the new fields.

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
from akasha.kernel.ids import CORE_LEN, A, checksum, contract_anchor

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

_safe_text_strategy = st.text(alphabet=_SAFE_TEXT_ALPHABET, min_size=1, max_size=24).filter(
    lambda s: s.strip() == s
)  # no leading/trailing whitespace (grammar: text SP anchor)

_PATH_ALPHABET = string.ascii_letters + string.digits + " _-"
_path_strategy = st.text(alphabet=_PATH_ALPHABET, min_size=1, max_size=16).filter(
    lambda s: s.strip() == s
)

# Non-contract-construct ("raw", spec T5.8-2) line text: deliberately the
# SAME restricted alphabet as `_safe_text_strategy` (no "^", "[", "]", "`",
# "#") so a generated prose/fence-content line can never accidentally form
# an anchor, a wiki-link delimiter, or a fence marker -- keeping this a test
# of raw-line *position* fidelity, not of grammar-token ambiguity.
_prose_line_strategy = st.text(alphabet=_SAFE_TEXT_ALPHABET, min_size=1, max_size=40).filter(
    lambda s: s.strip() == s
)
_fence_content_strategy = st.text(alphabet=_SAFE_TEXT_ALPHABET, min_size=0, max_size=30).filter(
    lambda s: s == s.rstrip()
)

# Line kinds a generated document interleaves (spec T5.8-2): the original
# four contract-construct kinds, plus three non-contract-construct kinds
# that must survive write-back verbatim by position.
_LINE_KINDS = ["paragraph", "task", "embed", "ref", "prose", "blank", "fence"]
_ID_CONSUMING_KINDS = {"paragraph", "task", "embed", "ref"}


# --- Direction 1: render(parse(D)) == D --------------------------------------


@st.composite
def _canonical_document_strategy(draw: st.DrawFn) -> str:
    """Build canonical, in-contract vault text directly (spec §4.7).

    Front matter is always a `tm: <CONTRACT_VERSION>` block -- either the
    canonical 3-line form, or (task T5.8-2) a non-canonical form with extra
    ``title:``/``tags:`` keys, exercising ``BlockSet.front_matter``. The
    body is a sequence of interleaved lines:

    * the original four grammar-legal, id-bearing kinds -- managed
      paragraphs, task lines (nesting depth bounded by
      ``max(prior depth) + 1``, mimicking a plausible nested list a
      human/daemon might write), standalone embeds, and standalone refs --
      each with a unique, checksum-valid id8;
    * three non-contract-construct ("raw", spec T5.8-2) kinds a managed
      file's lossless-container invariant must preserve verbatim by
      position: free prose text, blank lines, and a fenced code block
      (``` ``` ```-delimited, 0-3 inner lines, one of which may be a FAKE
      ``^tm-`` anchor -- fenced content is "ignored entirely" by the
      grammar, meaning it must round-trip untouched, not be stripped).

    A generated document is never allowed to end on a blank line (that
    would make it end in two newlines, which is not canonical -- the
    ``canonicalize_text(doc) == doc`` sanity assertion in the test below
    would then correctly reject the generator's own output as malformed
    input, not a real property failure).
    """
    extra_front_matter = draw(st.booleans())

    n_items = draw(st.integers(min_value=0, max_value=12))
    kinds = [draw(st.sampled_from(_LINE_KINDS)) for _ in range(n_items)]
    n_ids = sum(1 for k in kinds if k in _ID_CONSUMING_KINDS)
    cores = draw(st.lists(_id_core_strategy, min_size=n_ids, max_size=n_ids, unique=True))
    ids = iter(_id8(c) for c in cores)

    lines: list[str] = []
    depth_max = -1  # no task seen yet; first task line must be depth 0

    for kind in kinds:
        if kind == "paragraph":
            id_ = next(ids)
            text = draw(_safe_text_strategy)
            lines.append(f"{text} {contract_anchor(id_)}")
        elif kind == "task":
            id_ = next(ids)
            text = draw(_safe_text_strategy)
            state = draw(st.sampled_from(["x", " "]))
            depth = draw(st.integers(min_value=0, max_value=depth_max + 1))
            depth_max = depth
            indent = grammar.INDENT_UNIT * depth
            lines.append(f"{indent}- [{state}] {text} {contract_anchor(id_)}")
        elif kind == "embed":
            id_ = next(ids)
            path = draw(_path_strategy)
            lines.append(f"![[{path}#{contract_anchor(id_)}]]")
        elif kind == "ref":
            id_ = next(ids)
            path = draw(_path_strategy)
            lines.append(f"[[{path}#{contract_anchor(id_)}]]")
        elif kind == "prose":
            lines.append(draw(_prose_line_strategy))
        elif kind == "blank":
            lines.append("")
        else:  # fence
            n_inner = draw(st.integers(min_value=0, max_value=3))
            lines.append("```")
            for _ in range(n_inner):
                if draw(st.booleans()):
                    fake_core = draw(_id_core_strategy)
                    lines.append(f"fake block ^tm-{_id8(fake_core)}")
                else:
                    lines.append(draw(_fence_content_strategy))
            lines.append("```")

    # A trailing blank line would make the assembled doc end in "\n\n"
    # (non-canonical) -- drop any (the fence's own closing "```" line is
    # never blank, so this only ever removes standalone "blank"-kind atoms).
    while lines and lines[-1] == "":
        lines.pop()

    body = "".join(line + "\n" for line in lines)
    if extra_front_matter:
        front = f"---\ntitle: Extra\ntm: {grammar.CONTRACT_VERSION}\ntags: sample\n---\n"
    else:
        front = f"---\ntm: {grammar.CONTRACT_VERSION}\n---\n"
    return front + body


@settings(max_examples=500, deadline=None)
@given(_canonical_document_strategy())
def test_render_parse_round_trip_on_generated_documents(doc: str) -> None:
    """DoD: render(parse(D)) == D for generated canonical in-contract D.

    Extended per task T5.8-2 to cover the lossless-container invariant: D
    now also interleaves prose/blank/fenced (incl. a fake in-fence anchor)
    lines and a non-canonical front-matter form, so this property actually
    locks byte-exact write-back of every non-block line, not just blocks.
    """
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
    cores = draw(st.lists(_id_core_strategy, min_size=n_items, max_size=n_items, unique=True))
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
