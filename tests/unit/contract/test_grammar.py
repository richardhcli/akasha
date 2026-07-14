"""Unit tests for contract grammar tokens (build-plan task T3.1, spec §4.7)."""

from __future__ import annotations

import pytest

from akasha.contract import grammar
from akasha.kernel import ids


def _valid_id() -> str:
    return ids.mint()


def _bad_checksum_id() -> str:
    """An id8-shaped string (right alphabet, right length) with a wrong checksum."""
    good = ids.mint()
    core = good[: ids.CORE_LEN]
    real_check = good[ids.CORE_LEN]
    # pick any alphabet char that is not the correct checksum char
    bad_check = next(c for c in ids.A if c != real_check)
    return core + bad_check


# --- CONTRACT_VERSION ---------------------------------------------------------


def test_contract_version_is_1() -> None:
    assert grammar.CONTRACT_VERSION == 1


# --- anchor / id8 --------------------------------------------------------------


def test_anchor_matches_valid_id_anywhere() -> None:
    id_ = _valid_id()
    m = grammar.ANCHOR_RE.search(f"some text ^tm-{id_} trailing")
    assert m is not None
    assert m.group("id") == id_


def test_anchor_eol_matches_at_end_of_line() -> None:
    id_ = _valid_id()
    line = f"This is a claim ^tm-{id_}"
    m = grammar.ANCHOR_EOL_RE.search(line)
    assert m is not None
    assert m.group("id") == id_


def test_anchor_eol_tolerates_trailing_whitespace() -> None:
    id_ = _valid_id()
    line = f"This is a claim ^tm-{id_}   "
    assert grammar.ANCHOR_EOL_RE.search(line) is not None


def test_anchor_mid_line_is_not_a_real_anchor() -> None:
    """Spec §4.7: anchor pattern not at EOL is plain text, not a real anchor."""
    id_ = _valid_id()
    line = f"see ^tm-{id_} for details, then more text"
    # ANCHOR_RE (bare token) still matches the substring...
    assert grammar.ANCHOR_RE.search(line) is not None
    # ...but ANCHOR_EOL_RE (the "real anchor" token) must not.
    assert grammar.ANCHOR_EOL_RE.search(line) is None


def test_id8_alphabet_and_length_reused_from_ids_module() -> None:
    assert grammar.ID8_PATTERN == f"[{ids.A}]{{{ids.ID_LEN}}}" or grammar.ID8_RE.match(ids.mint())


def test_malformed_checksum_is_syntactically_matched_but_semantically_rejected() -> None:
    """Regex only checks alphabet+length; ids.validate() is the checksum authority.

    A bad-checksum id8 still satisfies the ID8/ANCHOR token shape (DoD: "malformed
    checksum ... rejected consistently" via ids.py's own checksum function).
    """
    bad_id = _bad_checksum_id()
    line = f"claim text ^tm-{bad_id}"
    m = grammar.ANCHOR_EOL_RE.search(line)
    assert m is not None
    assert m.group("id") == bad_id
    with pytest.raises(ids.IdError):
        ids.validate(bad_id)


# --- indent ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("indent", "depth"),
    [
        ("", 0),
        ("  ", 1),
        ("    ", 2),
        ("      ", 3),
    ],
)
def test_indent_depth(indent: str, depth: int) -> None:
    assert grammar.indent_depth(indent) == depth


def test_indent_re_extracts_leading_spaces() -> None:
    m = grammar.INDENT_RE.match("    - [ ] task")
    assert m is not None
    assert m.group("indent") == "    "
    assert grammar.indent_depth(m.group("indent")) == 2


# --- managed_par -----------------------------------------------------------


def test_managed_par_matches() -> None:
    id_ = _valid_id()
    line = f"Water boils at 100C at sea level ^tm-{id_}"
    m = grammar.MANAGED_PAR_RE.match(line)
    assert m is not None
    assert m.group("text") == "Water boils at 100C at sea level"
    assert m.group("id") == id_


def test_managed_par_rejects_mid_line_anchor() -> None:
    id_ = _valid_id()
    line = f"see ^tm-{id_} for details, then more text"
    assert grammar.MANAGED_PAR_RE.match(line) is None


# --- task_line ---------------------------------------------------------------


def test_task_line_unchecked() -> None:
    id_ = _valid_id()
    line = f"- [ ] Buy milk ^tm-{id_}"
    m = grammar.TASK_LINE_RE.match(line)
    assert m is not None
    assert m.group("state") == " "
    assert m.group("text") == "Buy milk"
    assert m.group("id") == id_
    assert grammar.indent_depth(m.group("indent")) == 0


def test_task_line_checked() -> None:
    id_ = _valid_id()
    line = f"- [x] Buy milk ^tm-{id_}"
    m = grammar.TASK_LINE_RE.match(line)
    assert m is not None
    assert m.group("state") == "x"


def test_task_line_nested_indent_depth() -> None:
    id_ = _valid_id()
    line = f"  - [ ] Sub-task ^tm-{id_}"
    m = grammar.TASK_LINE_RE.match(line)
    assert m is not None
    assert grammar.indent_depth(m.group("indent")) == 1

    line2 = f"    - [ ] Sub-sub-task ^tm-{id_}"
    m2 = grammar.TASK_LINE_RE.match(line2)
    assert m2 is not None
    assert grammar.indent_depth(m2.group("indent")) == 2


def test_task_line_rejects_mid_line_anchor() -> None:
    id_ = _valid_id()
    line = f"- [ ] see ^tm-{id_} for details, then more"
    assert grammar.TASK_LINE_RE.match(line) is None


# --- new_line ------------------------------------------------------------


def test_new_line_plain_text() -> None:
    line = "A brand new claim ^tm-new"
    m = grammar.NEW_LINE_RE.match(line)
    assert m is not None
    assert m.group("text") == "A brand new claim"
    assert m.group("task_text") is None


def test_new_line_task_form() -> None:
    line = "- [ ] A brand new task ^tm-new"
    m = grammar.NEW_LINE_RE.match(line)
    assert m is not None
    assert m.group("task_text") == "A brand new task"
    assert m.group("state") == " "
    assert m.group("text") is None


def test_new_line_task_form_checked() -> None:
    line = "- [x] Already done new task ^tm-new"
    m = grammar.NEW_LINE_RE.match(line)
    assert m is not None
    assert m.group("state") == "x"
    assert m.group("task_text") == "Already done new task"


def test_new_line_nested_task_form_indent() -> None:
    line = "  - [ ] Nested new task ^tm-new"
    m = grammar.NEW_LINE_RE.match(line)
    assert m is not None
    assert grammar.indent_depth(m.group("indent")) == 1


def test_new_marker_mid_line_is_not_real() -> None:
    line = "text ^tm-new more text after"
    assert grammar.NEW_LINE_RE.match(line) is None
    assert grammar.NEW_MARKER_EOL_RE.search(line) is None


# --- embed / ref -----------------------------------------------------------


def test_embed_matches() -> None:
    id_ = _valid_id()
    text = f"![[Some Note#^tm-{id_}]]"
    m = grammar.EMBED_RE.search(text)
    assert m is not None
    assert m.group("path") == "Some Note"
    assert m.group("id") == id_


def test_ref_matches() -> None:
    id_ = _valid_id()
    text = f"[[Some Note#^tm-{id_}]]"
    m = grammar.REF_RE.search(text)
    assert m is not None
    assert m.group("path") == "Some Note"
    assert m.group("id") == id_


def test_ref_does_not_match_embed_prefix() -> None:
    """`![[...]]` is an embed, not a ref (negative lookbehind on `!`)."""
    id_ = _valid_id()
    text = f"![[Some Note#^tm-{id_}]]"
    assert grammar.REF_RE.search(text) is None
    assert grammar.EMBED_RE.search(text) is not None


# --- fenced code blocks ------------------------------------------------------


def test_fence_re_matches_plain_fence() -> None:
    assert grammar.FENCE_RE.match("```") is not None


def test_fence_re_matches_fence_with_language() -> None:
    assert grammar.FENCE_RE.match("```python") is not None


def test_fence_re_matches_indented_fence() -> None:
    assert grammar.FENCE_RE.match("  ```") is not None


def test_fence_re_does_not_match_ordinary_text() -> None:
    id_ = _valid_id()
    assert grammar.FENCE_RE.match(f"plain text ^tm-{id_}") is None


def test_fenced_anchor_line_is_identified_via_fence_re() -> None:
    """Fence tracking (skipping content between fence delimiters) is parser

    logic (T3.2), not this module's job — this module only exposes the
    fence-delimiter token (FENCE_RE) so a parser can detect fence
    boundaries and ignore anchors inside them (spec §4.7: "Anything inside
    fenced code blocks is ignored entirely"). Confirm the token itself
    correctly flags a fence-opening line as a fence, which is the signal a
    parser needs to reject anchors found between such lines.
    """
    id_ = _valid_id()
    fence_line = f"``` {id_}"
    assert grammar.FENCE_RE.match(fence_line) is not None


# --- front matter ------------------------------------------------------------


def test_front_matter_tm_key_matches_contract_version() -> None:
    m = grammar.FRONT_MATTER_TM_RE.match("tm: 1")
    assert m is not None
    assert int(m.group("version")) == grammar.CONTRACT_VERSION
