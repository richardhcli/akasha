"""Renderer: ``BlockSet`` → canonical contract text (build-plan task T3.3, spec §4.7).

Deterministic inverse of :func:`akasha.contract.parser.parse`: projects hub
block/task/embed/ref structure into the Obsidian contract sublanguage.
Output is already canonical (spec §4.3 / §1): ``canonicalize_text(render(x))
== render(x)``.

This module is a pure function of its arguments — it never opens a DB
connection. Embed targets' current head bodies are obtained only via a
caller-supplied ``resolve_body`` callback (typically wrapping
``store.get_node(...).body``), so render stays free of a live store
dependency.
"""

from __future__ import annotations

from collections.abc import Callable

from akasha.contract import grammar
from akasha.contract.parser import Block, BlockSet, Embed, Ref
from akasha.kernel.canonical import canonicalize_text
from akasha.kernel.ids import contract_anchor

# Tie-break when several items share a ``line_no``: blocks first (they may
# already carry inline embed/ref wiki-links in ``text``), then standalone
# embeds, then standalone refs, then raw (non-contract-construct) lines.
_KIND_BLOCK = 0
_KIND_EMBED = 1
_KIND_REF = 2
_KIND_RAW = 3


def _render_paragraph(block: Block) -> str:
    """``managed_par := text SP anchor EOL`` (spec §4.7)."""
    return f"{block.text} {contract_anchor(block.id)}"


def _render_task(block: Block) -> str:
    """``task_line := indent "- [" ("x"|" ") "] " text SP anchor EOL``."""
    indent = grammar.INDENT_UNIT * block.depth
    # Narrowest reading: missing task_state ⇒ open (``" "`` checkbox).
    mark = "x" if block.task_state == "done" else " "
    return f"{indent}- [{mark}] {block.text} {contract_anchor(block.id)}"


def _render_block(block: Block) -> str:
    if block.kind == "task":
        return _render_task(block)
    return _render_paragraph(block)


def _render_embed(embed: Embed) -> str:
    """``embed := "![[" path "#^tm-" id8 "]]"`` (spec §4.7)."""
    return f"![[{embed.path}#{contract_anchor(embed.id)}]]"


def _render_ref(ref: Ref) -> str:
    """``ref := "[[" path "#^tm-" id8 "]]"`` (spec §4.7)."""
    return f"[[{ref.path}#{contract_anchor(ref.id)}]]"


def render(
    block_set: BlockSet,
    *,
    resolve_body: Callable[[str], str] | None = None,
) -> str:
    """Project a :class:`BlockSet` to canonical managed-file markdown (spec §4.7).

    When ``block_set.managed`` is true, emits a YAML front-matter block with
    ``tm: <CONTRACT_VERSION>`` (or ``block_set.contract_version`` when set).

    Blocks are emitted in ``line_no`` order (dict insertion order is the
    fallback when line numbers coincide with the kind tie-break). Standalone
    embeds/refs (no block at the same ``line_no``) become their own lines.
    Embeds/refs that share a ``line_no`` with a block are treated as inline
    (already present in that block's ``text``) and are not re-emitted.

    Lossless container (spec §4.7, T5.8-2): ``block_set.raw_lines`` -- every
    non-contract-construct line ``parse()`` captured (prose, blanks, fenced
    examples, unknown/malformed anchors) -- is interleaved back in at its
    original ``line_no``, verbatim; a raw line sharing a ``line_no`` with a
    block is skipped (already inline in that block's text, same rule as
    embeds/refs). ``block_set.front_matter`` (when not ``None``) replaces
    the canonical 3-line ``tm:`` front matter verbatim (e.g. a file with
    extra ``title:``/``tags:`` keys).

    For each embed, if ``resolve_body`` is supplied it is called with the
    embed target id (read-only body lookup). The contract form remains the
    wiki-link ``![[path#^tm-id8]]`` — Obsidian expands the transclusion at
    display time (spec §4.7: "embeds render the target's current body
    (read-only in Obsidian by nature)"). ``^tm-new`` requests are hub-side
    minting inputs, not hub projections, and are not emitted.
    """
    # SPEC-QUESTION: §4.7 does not say how a hub projection should order
    # co-located inline wiki-links vs. their owning block; skipping the
    # duplicate standalone line (same line_no as a Block) is the narrowest
    # reading that keeps render(parse(D)) lossless for inline embeds/refs.
    lines: list[str] = []

    if block_set.managed:
        version = (
            block_set.contract_version
            if block_set.contract_version is not None
            else grammar.CONTRACT_VERSION
        )
        lines.extend(
            block_set.front_matter
            if block_set.front_matter is not None
            else ["---", f"tm: {version}", "---"]
        )

    # Lossless-container write-back (task T5.8-2, human-decided 2026-07-13,
    # fable-designed): a raw line sharing a block's line_no is skipped just
    # like a co-located embed/ref (it is understood to already be part of
    # that block's own inline text); every other raw line becomes its own
    # output line, in position, verbatim.
    block_line_nos = {block.line_no for block in block_set.blocks.values()}
    skip_line_nos = block_line_nos | set(block_set.raw_lines.keys())
    items: list[tuple[int, int, int, str]] = []
    # (line_no, kind_tie, seq, rendered) — seq preserves insertion order
    # among equal (line_no, kind) for stability.
    seq = 0

    for block in block_set.blocks.values():
        items.append((block.line_no, _KIND_BLOCK, seq, _render_block(block)))
        seq += 1

    for embed in block_set.embeds:
        # Resolve target body when a resolver is provided (pure DI for the
        # hub read); managed-file bytes stay the wiki-link form.
        if resolve_body is not None:
            resolve_body(embed.id)
        if embed.line_no in skip_line_nos:
            continue
        items.append((embed.line_no, _KIND_EMBED, seq, _render_embed(embed)))
        seq += 1

    for ref in block_set.refs:
        if ref.line_no in skip_line_nos:
            continue
        items.append((ref.line_no, _KIND_REF, seq, _render_ref(ref)))
        seq += 1

    for line_no, raw_text in block_set.raw_lines.items():
        items.append((line_no, _KIND_RAW, seq, raw_text))
        seq += 1

    items.sort(key=lambda t: (t[0], t[1], t[2]))
    lines.extend(rendered for _, _, _, rendered in items)

    # Join with LF and force exactly one trailing newline via canonicalize.
    # Construction already avoids trailing whitespace; canonicalize_text
    # is the sole normalizer (spec §4.3) and guarantees the idempotence DoD.
    if not lines:
        return canonicalize_text("")
    return canonicalize_text("\n".join(lines) + "\n")
