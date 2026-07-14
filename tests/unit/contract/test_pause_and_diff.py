"""Unit tests for pause & diff formatter-storm guard (build-plan T3.6, §4.7)."""

from __future__ import annotations

from akasha.contract import linter, parser
from akasha.contract.linter import LintResult, PauseDecision, Violation
from akasha.contract.parser import Block, BlockSet
from akasha.kernel import ids
from akasha.kernel.ids import contract_anchor


def _id() -> str:
    return ids.mint()


def _managed(body: str) -> str:
    return "---\ntm: 1\n---\n" + body


def _base_with_n_blocks(n: int) -> tuple[BlockSet, list[str]]:
    """Build a managed BlockSet with ``n`` paragraph blocks; return (base, ids)."""
    id_list = [_id() for _ in range(n)]
    blocks: dict[str, Block] = {
        i: Block(id=i, kind="paragraph", text=f"claim {k}", line_no=k + 1)
        for k, i in enumerate(id_list)
    }
    return BlockSet(managed=True, contract_version=1, blocks=blocks), id_list


def _result_affecting(id_list: list[str], count: int) -> LintResult:
    """LintResult with ``count`` distinct E_LOST_ANCHOR violations (one each)."""
    violations = [
        Violation(
            code="E_LOST_ANCHOR",
            id=id_list[k],
            line_nos=[k + 1],
            message=f"affected {id_list[k]}",
        )
        for k in range(count)
    ]
    return LintResult(violations=violations)


# --- pause_threshold ----------------------------------------------------------


def test_pause_threshold_greater_than_25_percent() -> None:
    """7/25 = 28% > 25% ⇒ pause."""
    base, id_list = _base_with_n_blocks(25)
    result = _result_affecting(id_list, 7)
    assert linter.pause_threshold(result, base) is True


def test_pause_threshold_exactly_24_percent_does_not_pause() -> None:
    """6/25 = 24% ≯ 25% ⇒ do not pause (DoD boundary)."""
    base, id_list = _base_with_n_blocks(25)
    result = _result_affecting(id_list, 6)
    assert linter.pause_threshold(result, base) is False


def test_pause_threshold_exactly_25_percent_does_not_pause() -> None:
    """1/4 = 25% is exclusive — must not pause."""
    base, id_list = _base_with_n_blocks(4)
    result = _result_affecting(id_list, 1)
    assert linter.pause_threshold(result, base) is False


def test_pause_threshold_empty_base_blocks_never_pauses() -> None:
    base = BlockSet(managed=True, contract_version=1, blocks={})
    result = LintResult(
        violations=[
            Violation(code="E_ID_CHECKSUM", id="deadbeef", line_nos=[1], message="x"),
        ]
    )
    assert linter.pause_threshold(result, base) is False


def test_pause_threshold_dedupes_violation_ids() -> None:
    """Two violations for the same id count once in the numerator."""
    base, id_list = _base_with_n_blocks(4)
    # Two findings on one id ⇒ numerator 1 / 4 = 25% ⇒ no pause.
    result = LintResult(
        violations=[
            Violation(code="E_DUP_ID", id=id_list[0], line_nos=[1, 2], message="dup"),
            Violation(code="E_LOST_ANCHOR", id=id_list[0], line_nos=[1], message="lost"),
        ]
    )
    assert linter.pause_threshold(result, base) is False
    # Second distinct id ⇒ 2/4 = 50% ⇒ pause.
    result.violations.append(
        Violation(code="E_LOST_ANCHOR", id=id_list[1], line_nos=[2], message="lost2")
    )
    assert linter.pause_threshold(result, base) is True


def test_pause_threshold_ignores_none_ids() -> None:
    base, id_list = _base_with_n_blocks(4)
    result = LintResult(
        violations=[
            Violation(code="W_UNMANAGED_ANCHOR", id=None, line_nos=[1], message="adv"),
            Violation(code="E_LOST_ANCHOR", id=id_list[0], line_nos=[1], message="lost"),
        ]
    )
    # Only one distinct non-None id ⇒ 1/4 = 25% ⇒ no pause.
    assert linter.pause_threshold(result, base) is False


# --- pause_and_diff -----------------------------------------------------------


def test_pause_and_diff_triggers_with_one_review_item_and_nonempty_diff() -> None:
    """>25% affected ⇒ PauseDecision with snapshot + exactly one review item + diff."""
    id_list = [_id() for _ in range(25)]
    base_lines = [f"claim {k} {contract_anchor(i)}" for k, i in enumerate(id_list)]
    base_text = _managed("\n".join(base_lines) + "\n")
    # Strip anchors from first 7 blocks (28% E_LOST_ANCHOR).
    vault_lines = list(base_lines)
    for k in range(7):
        vault_lines[k] = f"claim {k}"
    vault_text = _managed("\n".join(vault_lines) + "\n")

    base = parser.parse(base_text)
    vault = parser.parse(vault_text)
    result = linter.lint(base, vault, vault_text)

    assert linter.pause_threshold(result, base) is True
    decision = linter.pause_and_diff(result, base, base_text, vault_text)

    assert isinstance(decision, PauseDecision)
    assert decision is not None
    assert decision.snapshot == vault_text
    assert decision.review_item.message != ""
    assert "---" in decision.review_item.message or "+++" in decision.review_item.message
    # Exactly one review item on the decision (singular field).
    assert decision.review_item is not None


def test_pause_and_diff_24_percent_returns_none() -> None:
    base, id_list = _base_with_n_blocks(25)
    result = _result_affecting(id_list, 6)
    base_text = "base\n"
    vault_text = "vault\n"
    assert linter.pause_and_diff(result, base, base_text, vault_text) is None


def test_pause_and_diff_empty_base_returns_none() -> None:
    base = BlockSet(managed=True, contract_version=1, blocks={})
    result = _result_affecting([_id(), _id()], 2)
    assert linter.pause_and_diff(result, base, "a\n", "b\n") is None


def test_pause_and_diff_is_deterministic() -> None:
    base, id_list = _base_with_n_blocks(5)
    # 2/5 = 40% > 25%.
    result = _result_affecting(id_list, 2)
    base_text = _managed(
        "\n".join(f"claim {k} {contract_anchor(i)}" for k, i in enumerate(id_list)) + "\n"
    )
    vault_text = _managed(
        "\n".join(
            (f"claim {k}" if k < 2 else f"claim {k} {contract_anchor(i)}")
            for k, i in enumerate(id_list)
        )
        + "\n"
    )

    a = linter.pause_and_diff(result, base, base_text, vault_text)
    b = linter.pause_and_diff(result, base, base_text, vault_text)

    assert a is not None and b is not None
    assert a.model_dump() == b.model_dump()
    assert a.snapshot == b.snapshot
    assert a.review_item.message == b.review_item.message
    assert a.review_item.message != ""
