"""Unit tests for the contract parser (build-plan task T3.2, spec §4.7)."""

from __future__ import annotations

from akasha.contract import parser
from akasha.kernel import ids


def _id() -> str:
    return ids.mint()


def _managed(body: str) -> str:
    """Wrap ``body`` in a minimal `tm: 1` front-matter block."""
    return "---\ntm: 1\n---\n" + body


# --- unmanaged files -----------------------------------------------------------


def test_unmanaged_file_no_front_matter_returns_empty() -> None:
    id_ = _id()
    text = f"Some claim ^tm-{id_}\n"
    bs = parser.parse(text)
    assert bs.managed is False
    assert bs.blocks == {}
    assert bs.embeds == []
    assert bs.refs == []
    assert bs.new_requests == []


def test_unmanaged_file_empty_text_returns_empty() -> None:
    bs = parser.parse("")
    assert bs.managed is False
    assert bs.blocks == {}


def test_unmanaged_file_front_matter_without_tm_key() -> None:
    id_ = _id()
    text = "---\ntitle: foo\n---\n" + f"Some claim ^tm-{id_}\n"
    bs = parser.parse(text)
    assert bs.managed is False
    assert bs.blocks == {}


def test_unmanaged_file_tm_version_mismatch_is_unmanaged() -> None:
    """Narrowest reading (SPEC-QUESTION): only an exact CONTRACT_VERSION match

    counts as managed; a present-but-different `tm:` version is unmanaged.
    """
    id_ = _id()
    text = "---\ntm: 2\n---\n" + f"Some claim ^tm-{id_}\n"
    bs = parser.parse(text)
    assert bs.managed is False
    assert bs.blocks == {}


# --- managed paragraph -----------------------------------------------------------


def test_managed_paragraph_is_parsed() -> None:
    id_ = _id()
    text = _managed(f"Water boils at 100C at sea level ^tm-{id_}\n")
    bs = parser.parse(text)
    assert bs.managed is True
    assert bs.contract_version == 1
    assert id_ in bs.blocks
    block = bs.blocks[id_]
    assert block.kind == "paragraph"
    assert block.text == "Water boils at 100C at sea level"
    assert block.line_no == 4
    assert block.task_state is None


# --- managed task -----------------------------------------------------------------


def test_managed_task_states() -> None:
    id_done = _id()
    id_open = _id()
    body = f"- [x] Buy milk ^tm-{id_done}\n- [ ] Buy eggs ^tm-{id_open}\n"
    bs = parser.parse(_managed(body))
    assert bs.blocks[id_done].kind == "task"
    assert bs.blocks[id_done].task_state == "done"
    assert bs.blocks[id_open].task_state == "open"


def test_managed_task_root_has_no_parent() -> None:
    id_ = _id()
    body = f"- [ ] Root task ^tm-{id_}\n"
    bs = parser.parse(_managed(body))
    assert bs.blocks[id_].depth == 0
    assert bs.blocks[id_].parent_id is None


# --- nested tasks: composes(parent->child) derivable from depth -------------------


def test_nested_task_parent_child_by_depth() -> None:
    parent_id = _id()
    child_id = _id()
    grandchild_id = _id()
    body = (
        f"- [ ] Parent task ^tm-{parent_id}\n"
        f"  - [ ] Child task ^tm-{child_id}\n"
        f"    - [ ] Grandchild task ^tm-{grandchild_id}\n"
    )
    bs = parser.parse(_managed(body))

    assert bs.blocks[parent_id].depth == 0
    assert bs.blocks[parent_id].parent_id is None

    assert bs.blocks[child_id].depth == 1
    assert bs.blocks[child_id].parent_id == parent_id

    assert bs.blocks[grandchild_id].depth == 2
    assert bs.blocks[grandchild_id].parent_id == child_id


def test_nested_task_siblings_share_parent_not_each_other() -> None:
    parent_id = _id()
    child_a = _id()
    child_b = _id()
    body = (
        f"- [ ] Parent task ^tm-{parent_id}\n"
        f"  - [ ] Child A ^tm-{child_a}\n"
        f"  - [ ] Child B ^tm-{child_b}\n"
    )
    bs = parser.parse(_managed(body))

    assert bs.blocks[child_a].parent_id == parent_id
    assert bs.blocks[child_b].parent_id == parent_id


def test_nested_task_dedent_back_to_sibling_of_ancestor() -> None:
    root_a = _id()
    child = _id()
    root_b = _id()
    body = (
        f"- [ ] Root A ^tm-{root_a}\n"
        f"  - [ ] Child of A ^tm-{child}\n"
        f"- [ ] Root B ^tm-{root_b}\n"
    )
    bs = parser.parse(_managed(body))

    assert bs.blocks[child].parent_id == root_a
    assert bs.blocks[root_b].depth == 0
    assert bs.blocks[root_b].parent_id is None


# --- ^tm-new requests -----------------------------------------------------------


def test_new_paragraph_request_recorded() -> None:
    body = "A brand new claim ^tm-new\n"
    bs = parser.parse(_managed(body))
    assert len(bs.new_requests) == 1
    req = bs.new_requests[0]
    assert req.shape == "paragraph"
    assert req.text == "A brand new claim"
    assert req.task_state is None
    assert req.line_no == 4
    # not recorded as a real block (no id minted yet)
    assert bs.blocks == {}


def test_new_task_request_recorded() -> None:
    body = "- [ ] A brand new task ^tm-new\n"
    bs = parser.parse(_managed(body))
    assert len(bs.new_requests) == 1
    req = bs.new_requests[0]
    assert req.shape == "task"
    assert req.text == "A brand new task"
    assert req.task_state == "open"
    assert req.depth == 0


def test_new_nested_task_request_records_depth_and_done_state() -> None:
    body = "- [ ] Parent ^tm-new\n  - [x] Nested new done task ^tm-new\n"
    bs = parser.parse(_managed(body))
    assert len(bs.new_requests) == 2
    nested = bs.new_requests[1]
    assert nested.shape == "task"
    assert nested.task_state == "done"
    assert nested.depth == 1


# --- embeds / refs -----------------------------------------------------------------


def test_embed_and_ref_recorded_alongside_owning_paragraph() -> None:
    embed_id = _id()
    ref_id = _id()
    par_id = _id()
    body = (
        f"See ![[Other Note#^tm-{embed_id}]] embed and "
        f"[[Another#^tm-{ref_id}]] ref, standalone ^tm-{par_id}\n"
    )
    bs = parser.parse(_managed(body))

    assert len(bs.embeds) == 1
    assert bs.embeds[0].path == "Other Note"
    assert bs.embeds[0].id == embed_id

    assert len(bs.refs) == 1
    assert bs.refs[0].path == "Another"
    assert bs.refs[0].id == ref_id

    assert par_id in bs.blocks
    assert bs.blocks[par_id].kind == "paragraph"


def test_embed_alone_not_confused_for_ref() -> None:
    embed_id = _id()
    body = f"![[Some Note#^tm-{embed_id}]]\n"
    bs = parser.parse(_managed(body))
    assert len(bs.embeds) == 1
    assert bs.refs == []


# --- fenced code: everything inside is ignored -----------------------------------


def test_fenced_code_ignores_anchor_task_and_embed_patterns() -> None:
    fenced_task_id = _id()
    fenced_par_id = _id()
    fenced_embed_id = _id()
    real_id = _id()
    body = (
        "```\n"
        f"- [ ] fenced task ^tm-{fenced_task_id}\n"
        f"plain fenced paragraph ^tm-{fenced_par_id}\n"
        f"![[Fenced Note#^tm-{fenced_embed_id}]]\n"
        "```\n"
        f"- [ ] real task ^tm-{real_id}\n"
    )
    bs = parser.parse(_managed(body))

    assert fenced_task_id not in bs.blocks
    assert fenced_par_id not in bs.blocks
    assert bs.embeds == []
    assert real_id in bs.blocks
    assert len(bs.blocks) == 1


def test_fenced_code_with_language_tag_still_ignored() -> None:
    fenced_id = _id()
    real_id = _id()
    body = (
        "```python\n"
        f"x = 1  # ^tm-{fenced_id}\n"
        "```\n"
        f"Real paragraph ^tm-{real_id}\n"
    )
    bs = parser.parse(_managed(body))
    assert fenced_id not in bs.blocks
    assert real_id in bs.blocks


# --- anchor mid-line is plain text, not a block -----------------------------------


def test_anchor_mid_line_is_plain_text_not_a_block() -> None:
    id_ = _id()
    body = f"see ^tm-{id_} for details, then more text\n"
    bs = parser.parse(_managed(body))
    assert bs.blocks == {}
    assert bs.new_requests == []
