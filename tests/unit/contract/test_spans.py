"""Spans (M20-A/B/E, spec §4.7 "Spans"): ``{text}{tm-id}`` shares only the braced text.

Parser, renderer and linter behaviour. The reconcile behaviour is in
``tests/unit/sync/test_reconcile_spans.py``.
"""

from __future__ import annotations

from akasha.contract import grammar, linter, parser
from akasha.contract.render import render

A, B, C = "4cgfdxpi", "dyo6vafb", "fr5wvmjg"  # valid ids


def _spans(text: str) -> dict[str, parser.Block]:
    return {k: v for k, v in parser.parse(text).blocks.items() if v.kind == "span"}


# --- parsing ---------------------------------------------------------------------------------


def test_a_span_shares_only_the_braced_text() -> None:
    text = f"before {{shared idea}}{{tm-{A}}} after\n"
    [span] = _spans(text).values()
    assert (span.id, span.text, span.line_no, span.end_line_no) == (A, "shared idea", 1, 1)
    assert span.col == 7 and span.end_col == len(text) - len(" after\n")
    assert render(parser.parse(text)) == text  # everything outside it is untouched


def test_padding_inside_the_braces_is_per_file_and_not_part_of_the_text() -> None:
    padded = f"{{ shared idea }}{{tm-{A}}}\n"
    bare = f"{{shared idea}}{{tm-{A}}}\n"
    [p] = _spans(padded).values()
    [b] = _spans(bare).values()
    assert p.text == b.text == "shared idea"  # the same node text
    assert (p.lead, p.trail, b.lead, b.trail) == (" ", " ", "", "")
    assert render(parser.parse(padded)) == padded and render(parser.parse(bare)) == bare


def test_several_spans_on_one_line() -> None:
    text = f"{{one}}{{tm-{A}}} and {{two}}{{tm-{B}}}\n"
    spans = _spans(text)
    assert [s.text for s in spans.values()] == ["one", "two"]
    assert render(parser.parse(text)) == text


def test_a_span_may_cross_lines_and_keep_blank_lines() -> None:
    text = f"intro\n{{ first\n\n  second }}{{tm-{A}}}!\nafter\n"
    [span] = _spans(text).values()
    assert span.text == "first\n\n  second"
    assert (span.line_no, span.end_line_no) == (2, 4)
    assert (span.lead, span.trail) == (" ", " ")
    assert render(parser.parse(text)) == text


def test_inner_braces_balance_and_are_part_of_the_text() -> None:
    [span] = _spans(f"{{ a {{b}} c }}{{tm-{A}}}\n").values()
    assert span.text == "a {b} c"


def test_a_brace_group_without_an_id_is_literal_prose() -> None:
    text = f"use {{curly}} braces, then {{shared}}{{tm-{A}}}\n"
    assert [s.text for s in _spans(text).values()] == ["shared"]  # the first group is prose


def test_unbalanced_braces_cannot_make_a_span() -> None:
    assert _spans(f"{{ a }} b }}{{tm-{A}}}\n") == {}


def test_a_checksum_invalid_id_is_literal_text_not_a_span() -> None:
    bad = "aaaaaaab"
    assert _spans(f"{{x}}{{tm-{bad}}}\n") == {}


def test_empty_braces_share_nothing() -> None:
    assert _spans(f"{{ }}{{tm-{A}}}\n") == {}


def test_whole_line_constructs_win_over_spans() -> None:
    """`- [ ] {x}{tm-a} ^tm-b` is an ordinary task whose text happens to contain braces."""
    text = f"- [ ] {{x}}{{tm-{A}}} ^tm-{B}\n"
    bs = parser.parse(text)
    assert list(bs.blocks) == [B] and bs.blocks[B].kind == "task"
    assert bs.blocks[B].text == f"{{x}}{{tm-{A}}}"
    assert render(bs) == text


def test_a_span_never_swallows_a_construct_line() -> None:
    text = f"{{ open\n- [ ] a task ^tm-{B}\nclose }}{{tm-{A}}}\n"
    bs = parser.parse(text)
    assert list(bs.blocks) == [B]  # the span is prose again; the task is untouched


def test_fenced_and_front_matter_braces_are_ignored() -> None:
    text = f"---\ntitle: {{x}}{{tm-{A}}}\n---\n```\n{{y}}{{tm-{B}}}\n```\n"
    assert parser.parse(text).blocks == {}


def test_span_new_requests_a_mint() -> None:
    text = "see {a fresh idea}{tm-new} here\n"
    bs = parser.parse(text)
    [nr] = bs.new_requests
    assert (nr.shape, nr.text, nr.line_no, nr.marker_line) == ("span", "a fresh idea", 1, 1)
    assert text[nr.marker_col :].startswith("{tm-new}")


def test_a_repeated_span_id_is_recorded_as_a_duplicate() -> None:
    text = f"{{one}}{{tm-{A}}}\n{{two}}{{tm-{A}}}\n"
    bs = parser.parse(text)
    assert bs.blocks[A].text == "one"  # the first copy keeps the id
    [dup] = bs.duplicate_spans
    assert (dup.id, dup.marker_line) == (A, 2)


def test_the_tokens_are_constants_every_pattern_is_built_from() -> None:
    assert grammar.SPAN_OPEN == "{" and grammar.SPAN_CLOSE == "}"
    assert grammar.span_source("x", A, lead=" ", trail=" ") == f"{{ x }}{{tm-{A}}}"


def test_scan_is_capped() -> None:
    text = "{" + "\n" * (grammar.SPAN_MAX_LINES + 5) + f"x}}{{tm-{A}}}\n"
    assert _spans(text) == {}


# --- rendering -------------------------------------------------------------------------------


def test_render_substitutes_new_text_keeping_this_files_padding_and_context() -> None:
    text = f"A: {{ old }}{{tm-{A}}} (tail)\n"
    bs = parser.parse(text)
    edited = bs.model_copy(
        update={"blocks": {A: bs.blocks[A].model_copy(update={"text": "new\nsecond line"})}}
    )
    assert render(edited) == f"A: {{ new\nsecond line }}{{tm-{A}}} (tail)\n"


def test_render_handles_a_span_that_changes_line_count_before_another() -> None:
    text = f"{{one}}{{tm-{A}}}\nmiddle\n{{ two\nlines }}{{tm-{B}}}\n"
    bs = parser.parse(text)
    blocks = {
        A: bs.blocks[A].model_copy(update={"text": "1\n1b\n1c"}),
        B: bs.blocks[B].model_copy(update={"text": "2"}),
    }
    out = render(bs.model_copy(update={"blocks": blocks}))
    assert out == f"{{1\n1b\n1c}}{{tm-{A}}}\nmiddle\n{{ 2 }}{{tm-{B}}}\n"


# --- linting ---------------------------------------------------------------------------------


def test_a_duplicate_span_id_gets_a_new_node_for_the_later_copy() -> None:
    text = f"{{one}}{{tm-{A}}}\nmiddle {{two}}{{tm-{A}}} tail\n"
    result = linter.lint(parser.parse(""), parser.parse(text), text)
    [repair] = result.repairs
    assert (repair.code, repair.action, repair.line_no) == ("E_DUP_ID", "propose_tm_new", 2)
    assert repair.after == "middle {two}{tm-new} tail"
    assert result.review_items == []


def test_a_span_whose_id_wrapper_was_deleted_gets_it_back() -> None:
    base = f"see {{ shared text }}{{tm-{A}}} ok\n"
    vault = "see { shared text } ok\n"  # the {tm-id} vanished, the braces and text are intact
    result = linter.lint(parser.parse(base), parser.parse(vault), vault)
    [repair] = result.repairs
    assert (repair.code, repair.action, repair.id) == ("E_LOST_ANCHOR", "reinsert_anchor", A)
    assert repair.after == f"see {{ shared text }}{{tm-{A}}} ok"


def test_a_multi_line_span_whose_id_wrapper_was_deleted_gets_it_back() -> None:
    base = f"{{ first\nsecond }}{{tm-{A}}}\n"
    vault = "{ first\nsecond }\n"
    result = linter.lint(parser.parse(base), parser.parse(vault), vault)
    [repair] = result.repairs
    assert repair.line_no == 2 and repair.after == f"second }}{{tm-{A}}}"


def test_a_reworded_span_is_not_guessed_the_node_follows_the_delete_rules() -> None:
    base = f"{{shared text}}{{tm-{A}}}\n"
    vault = "{shared texts}\n"
    result = linter.lint(parser.parse(base), parser.parse(vault), vault, maturity={A: "S1"})
    assert result.repairs == []  # no fuzzy re-attachment (PRD F13)
    assert [r.code for r in result.review_items] == ["E_DELETED_S1"]


def test_a_brace_heavy_note_parses_in_bounded_time() -> None:
    """LaTeX/JSON-style notes full of unclosed braces must not make parsing quadratic."""
    import time

    for text in (
        "\n".join("\\frac{a" for _ in range(20000)) + "\n",
        "\n".join("{{{{ x" for _ in range(5000)) + "\n",
    ):
        t0 = time.perf_counter()
        bs = parser.parse(text)
        assert time.perf_counter() - t0 < 1.5
        assert bs.blocks == {}
