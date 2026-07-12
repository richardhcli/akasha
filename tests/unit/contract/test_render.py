"""Unit tests for the contract renderer (build-plan task T3.3, spec §4.7)."""

from __future__ import annotations

from akasha.contract import grammar
from akasha.contract.parser import Block, BlockSet, Embed, Ref
from akasha.contract.render import render
from akasha.kernel import ids
from akasha.kernel.canonical import canonicalize_text


def _id() -> str:
    return ids.mint()


def _managed_blocks(
    blocks: dict[str, Block] | None = None,
    embeds: list[Embed] | None = None,
    refs: list[Ref] | None = None,
) -> BlockSet:
    """Minimal managed BlockSet; callers override blocks/embeds/refs."""
    return BlockSet(
        managed=True,
        contract_version=grammar.CONTRACT_VERSION,
        blocks=blocks or {},
        embeds=embeds or [],
        refs=refs or [],
    )


# --- paragraph -----------------------------------------------------------------


def test_paragraph_rendering() -> None:
    id_ = _id()
    bs = _managed_blocks(
        blocks={
            id_: Block(
                id=id_,
                kind="paragraph",
                text="Water boils at 100C at sea level",
                line_no=1,
            )
        }
    )
    out = render(bs)
    assert out == (
        "---\n"
        f"tm: {grammar.CONTRACT_VERSION}\n"
        "---\n"
        f"Water boils at 100C at sea level ^tm-{id_}\n"
    )


# --- tasks: both states + nesting -----------------------------------------------


def test_task_rendering_both_states() -> None:
    id_done = _id()
    id_open = _id()
    bs = _managed_blocks(
        blocks={
            id_done: Block(
                id=id_done,
                kind="task",
                text="Buy milk",
                line_no=1,
                task_state="done",
                depth=0,
            ),
            id_open: Block(
                id=id_open,
                kind="task",
                text="Buy eggs",
                line_no=2,
                task_state="open",
                depth=0,
            ),
        }
    )
    out = render(bs)
    body = out.split("---\n", 2)[-1]
    assert body == f"- [x] Buy milk ^tm-{id_done}\n- [ ] Buy eggs ^tm-{id_open}\n"


def test_nested_task_indent() -> None:
    parent_id = _id()
    child_id = _id()
    grandchild_id = _id()
    bs = _managed_blocks(
        blocks={
            parent_id: Block(
                id=parent_id,
                kind="task",
                text="Parent task",
                line_no=1,
                task_state="open",
                depth=0,
            ),
            child_id: Block(
                id=child_id,
                kind="task",
                text="Child task",
                line_no=2,
                task_state="open",
                depth=1,
                parent_id=parent_id,
            ),
            grandchild_id: Block(
                id=grandchild_id,
                kind="task",
                text="Grandchild task",
                line_no=3,
                task_state="done",
                depth=2,
                parent_id=child_id,
            ),
        }
    )
    out = render(bs)
    body = out.split("---\n", 2)[-1]
    assert body == (
        f"- [ ] Parent task ^tm-{parent_id}\n"
        f"  - [ ] Child task ^tm-{child_id}\n"
        f"    - [x] Grandchild task ^tm-{grandchild_id}\n"
    )
    # indent is exactly grammar.INDENT_UNIT repeated per depth
    lines = body.splitlines()
    assert lines[1].startswith(grammar.INDENT_UNIT)
    assert lines[2].startswith(grammar.INDENT_UNIT * 2)


# --- embed with resolved body ---------------------------------------------------


def test_embed_rendering_with_resolved_body() -> None:
    embed_id = _id()
    target_body = "The target's current head body"
    resolved: list[tuple[str, str]] = []

    def resolve_body(node_id: str) -> str:
        assert node_id == embed_id
        resolved.append((node_id, target_body))
        return target_body

    bs = _managed_blocks(
        embeds=[Embed(path="Other Note", id=embed_id, line_no=1)],
    )
    out = render(bs, resolve_body=resolve_body)

    assert resolved == [(embed_id, target_body)]
    body = out.split("---\n", 2)[-1]
    assert body == f"![[Other Note#^tm-{embed_id}]]\n"


# --- ref ------------------------------------------------------------------------


def test_ref_rendering() -> None:
    ref_id = _id()
    bs = _managed_blocks(
        refs=[Ref(path="Another", id=ref_id, line_no=1)],
    )
    out = render(bs)
    body = out.split("---\n", 2)[-1]
    assert body == f"[[Another#^tm-{ref_id}]]\n"


# --- front-matter ---------------------------------------------------------------


def test_front_matter_emitted_when_managed() -> None:
    id_ = _id()
    bs = _managed_blocks(
        blocks={
            id_: Block(
                id=id_,
                kind="paragraph",
                text="A claim",
                line_no=1,
            )
        }
    )
    out = render(bs)
    assert out.startswith(f"---\ntm: {grammar.CONTRACT_VERSION}\n---\n")


def test_front_matter_absent_when_unmanaged() -> None:
    id_ = _id()
    bs = BlockSet(
        managed=False,
        blocks={
            id_: Block(
                id=id_,
                kind="paragraph",
                text="Unmanaged claim",
                line_no=1,
            )
        },
    )
    out = render(bs)
    assert not out.startswith("---")
    assert out == f"Unmanaged claim ^tm-{id_}\n"


# --- canonical idempotence ------------------------------------------------------


def test_render_output_is_canonical_idempotent() -> None:
    """DoD: canonicalize_text(render(x)) == render(x)."""
    par_id = _id()
    task_id = _id()
    child_id = _id()
    embed_id = _id()
    ref_id = _id()
    bodies = {embed_id: "embed target body"}

    bs = _managed_blocks(
        blocks={
            par_id: Block(
                id=par_id,
                kind="paragraph",
                text="A managed paragraph",
                line_no=1,
            ),
            task_id: Block(
                id=task_id,
                kind="task",
                text="Root task",
                line_no=2,
                task_state="open",
                depth=0,
            ),
            child_id: Block(
                id=child_id,
                kind="task",
                text="Nested done",
                line_no=3,
                task_state="done",
                depth=1,
                parent_id=task_id,
            ),
        },
        embeds=[Embed(path="Note", id=embed_id, line_no=4)],
        refs=[Ref(path="RefNote", id=ref_id, line_no=5)],
    )
    out = render(bs, resolve_body=bodies.__getitem__)
    assert canonicalize_text(out) == out
    # no trailing whitespace on any line; exactly one trailing newline
    assert out.endswith("\n")
    assert not out.endswith("\n\n")
    for line in out.split("\n")[:-1]:
        assert line == line.rstrip()
