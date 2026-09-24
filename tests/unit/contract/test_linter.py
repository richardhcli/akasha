"""Unit tests for the contract linter (build-plan task T3.5, spec §4.7)."""

from __future__ import annotations

from akasha.contract import linter, parser
from akasha.kernel import ids
from akasha.kernel.ids import contract_anchor


def _id() -> str:
    return ids.mint()


def _bad_checksum_id() -> str:
    good = ids.mint()
    core = good[: ids.CORE_LEN]
    real_check = good[ids.CORE_LEN]
    bad_check = next(c for c in ids.A if c != real_check)
    return core + bad_check


def _managed(body: str) -> str:
    return "---\ntm: 1\n---\n" + body


def _codes(result: linter.LintResult) -> set[str]:
    return {v.code for v in result.violations}


def _review_codes(result: linter.LintResult) -> set[str]:
    return {r.code for r in result.review_items}


def _repair_actions(result: linter.LintResult) -> list[str]:
    return [r.action for r in result.repairs]


# --- E_ID_CHECKSUM ------------------------------------------------------------


def test_e_id_checksum_bad_eol_anchor_gets_a_new_node() -> None:
    """M20-G: a malformed id cannot name any node, so the line is given a new one."""
    bad = _bad_checksum_id()
    vault_text = _managed(f"Water boils at 100C ^tm-{bad}\n")
    vault = parser.parse(vault_text)
    # Parser still captures the structurally valid (alphabet+length) id.
    assert bad in vault.blocks

    result = linter.lint(parser.parse(_managed("")), vault, vault_text)

    assert "E_ID_CHECKSUM" in _codes(result)
    assert any(v.id == bad for v in result.violations if v.code == "E_ID_CHECKSUM")
    assert result.review_items == []  # never a review: resolved on the spot
    [repair] = result.repairs
    assert (repair.code, repair.action, repair.id) == ("E_ID_CHECKSUM", "propose_tm_new", bad)
    assert repair.before == f"Water boils at 100C ^tm-{bad}"
    assert repair.after == "Water boils at 100C ^tm-new"


def test_e_id_checksum_valid_anchor_is_clean() -> None:
    good = _id()
    vault_text = _managed(f"A valid claim ^tm-{good}\n")
    vault = parser.parse(vault_text)
    result = linter.lint(parser.parse(_managed("")), vault, vault_text)
    assert "E_ID_CHECKSUM" not in _codes(result)


# --- E_DUP_ID -----------------------------------------------------------------


def test_e_dup_id_certain_repair_when_one_copy_matches_base() -> None:
    """Certain path: original unchanged, edited duplicate → propose ^tm-new on edit."""
    id_ = _id()
    base_line = f"Original claim text ^tm-{id_}"
    base_text = _managed(f"{base_line}\n")
    # Copy-paste then edit the second copy's text — first stays byte-identical.
    vault_text = _managed(f"{base_line}\nEdited claim text ^tm-{id_}\n")
    base = parser.parse(base_text)
    vault = parser.parse(vault_text)

    result = linter.lint(base, vault, vault_text)

    assert "E_DUP_ID" in _codes(result)
    assert _repair_actions(result) == ["propose_tm_new"]
    repair = result.repairs[0]
    assert repair.code == "E_DUP_ID"
    assert repair.id == id_
    assert repair.before == f"Edited claim text ^tm-{id_}"
    assert repair.after == "Edited claim text ^tm-new"
    assert not any(r.code == "E_DUP_ID" for r in result.review_items)


def test_e_dup_id_certain_repair_pure_copy_paste() -> None:
    """Both copies identical to base (E05): first keeps id, second → ^tm-new."""
    id_ = _id()
    line = f"Stable claim ^tm-{id_}"
    base_text = _managed(f"{line}\n")
    vault_text = _managed(f"{line}\n{line}\n")
    base = parser.parse(base_text)
    vault = parser.parse(vault_text)

    result = linter.lint(base, vault, vault_text)

    assert "E_DUP_ID" in _codes(result)
    assert len(result.repairs) == 1
    assert result.repairs[0].action == "propose_tm_new"
    assert result.repairs[0].after.endswith("^tm-new")
    assert not any(r.code == "E_DUP_ID" for r in result.review_items)


def test_e_dup_id_no_copy_matches_base_first_copy_keeps_the_id() -> None:
    """M20-G: both copies differ from base -> still no review; the first copy keeps the id."""
    id_ = _id()
    base_text = _managed(f"Original claim ^tm-{id_}\n")
    vault_text = _managed(f"First edit ^tm-{id_}\nSecond edit ^tm-{id_}\n")
    base = parser.parse(base_text)
    vault = parser.parse(vault_text)

    result = linter.lint(base, vault, vault_text)

    assert "E_DUP_ID" in _codes(result)
    assert result.review_items == []
    [repair] = result.repairs
    assert repair.before == f"Second edit ^tm-{id_}" and repair.after == "Second edit ^tm-new"


def test_e_dup_id_with_no_base_block_first_copy_keeps_the_id() -> None:
    id_ = _id()
    vault_text = _managed(f"One ^tm-{id_}\nTwo ^tm-{id_}\nThree ^tm-{id_}\n")
    result = linter.lint(parser.parse(""), parser.parse(vault_text), vault_text)
    assert result.review_items == []
    assert [r.before for r in result.repairs] == [f"Two ^tm-{id_}", f"Three ^tm-{id_}"]


# --- E_LOST_ANCHOR ------------------------------------------------------------


def test_e_lost_anchor_certain_repair_when_text_byte_identical() -> None:
    id_ = _id()
    base_text = _managed(f"Water boils at 100C ^tm-{id_}\n")
    # Same text, anchor deleted.
    vault_text = _managed("Water boils at 100C\n")
    base = parser.parse(base_text)
    vault = parser.parse(vault_text)
    assert id_ not in vault.blocks

    result = linter.lint(base, vault, vault_text)

    assert "E_LOST_ANCHOR" in _codes(result)
    assert _repair_actions(result) == ["reinsert_anchor"]
    repair = result.repairs[0]
    assert repair.id == id_
    assert repair.before == "Water boils at 100C"
    assert repair.after == f"Water boils at 100C {contract_anchor(id_)}"
    assert not any(r.code == "E_LOST_ANCHOR" for r in result.review_items)


def test_e_lost_anchor_fuzzy_not_exact_changes_the_id() -> None:
    """M20-G: >= 0.9 similar but not identical is ambiguous (PRD F13: no re-anchoring by
    similarity), so the line becomes a NEW node and the old id follows the delete rules."""
    id_ = _id()
    base_text = _managed(f"Water boils at 100C at sea level ^tm-{id_}\n")
    # Small edit keeps similarity high but breaks byte-identity.
    vault_text = _managed("Water boils at 100C at sea leve\n")
    base = parser.parse(base_text)
    vault = parser.parse(vault_text)

    result = linter.lint(base, vault, vault_text, maturity={id_: "S0"})

    assert "E_LOST_ANCHOR" in _codes(result)
    assert result.review_items == []
    [repair] = result.repairs
    assert (repair.code, repair.action, repair.id) == ("E_LOST_ANCHOR", "propose_tm_new", id_)
    assert repair.after == "Water boils at 100C at sea leve ^tm-new"
    # Sanity: similarity really cleared the threshold.
    from difflib import SequenceMatcher

    ratio = SequenceMatcher(
        None,
        "Water boils at 100C at sea leve",
        "Water boils at 100C at sea level",
    ).ratio()
    assert ratio >= linter.LOST_ANCHOR_SIMILARITY


def test_e_lost_anchor_fuzzy_on_an_s1_node_also_reviews_the_deletion() -> None:
    id_ = _id()
    base_text = _managed(f"Water boils at 100C at sea level ^tm-{id_}\n")
    vault_text = _managed("Water boils at 100C at sea leve\n")
    result = linter.lint(
        parser.parse(base_text), parser.parse(vault_text), vault_text, maturity={id_: "S1"}
    )
    assert [r.code for r in result.review_items] == ["E_DELETED_S1"]  # the only thing for a human
    assert [r.action for r in result.repairs] == ["propose_tm_new"]


def test_a_corrupted_anchor_on_a_byte_identical_line_restores_the_base_id() -> None:
    """The line is exactly the base block with its id damaged: same certainty as a deleted anchor,
    so the base id comes back (a mirror stays linked) instead of a new node being minted."""
    id_ = _id()
    bad = _bad_checksum_id()
    base_text = _managed(f"Water boils at 100C ^tm-{id_}\n")
    vault_text = _managed(f"Water boils at 100C ^tm-{bad}\n")
    result = linter.lint(parser.parse(base_text), parser.parse(vault_text), vault_text)
    [repair] = result.repairs  # the checksum repair yields to the restore
    assert (repair.code, repair.action, repair.id) == ("E_LOST_ANCHOR", "reinsert_anchor", id_)
    assert repair.before == f"Water boils at 100C ^tm-{bad}"
    assert repair.after == f"Water boils at 100C ^tm-{id_}"
    assert result.review_items == []


def test_a_corrupted_anchor_on_a_changed_line_still_gets_a_new_node() -> None:
    id_ = _id()
    bad = _bad_checksum_id()
    base_text = _managed(f"Water boils at 100C ^tm-{id_}\n")
    vault_text = _managed(f"Water boils at 100C, honestly ^tm-{bad}\n")  # not byte-identical
    result = linter.lint(parser.parse(base_text), parser.parse(vault_text), vault_text)
    assert [(r.code, r.action) for r in result.repairs] == [("E_ID_CHECKSUM", "propose_tm_new")]
    assert result.repairs[0].after == "Water boils at 100C, honestly ^tm-new"


def test_e_lost_anchor_task_line_certain_repair() -> None:
    id_ = _id()
    base_text = _managed(f"- [ ] Buy milk ^tm-{id_}\n")
    vault_text = _managed("- [ ] Buy milk\n")
    base = parser.parse(base_text)
    vault = parser.parse(vault_text)

    result = linter.lint(base, vault, vault_text)

    assert _repair_actions(result) == ["reinsert_anchor"]
    assert result.repairs[0].after == f"- [ ] Buy milk {contract_anchor(id_)}"


# --- E_DELETED_S1 -------------------------------------------------------------


def test_e_deleted_s1_when_block_gone_and_maturity_s1() -> None:
    id_ = _id()
    base_text = _managed(f"Important claim ^tm-{id_}\n")
    vault_text = _managed("Unrelated other text\n")
    base = parser.parse(base_text)
    vault = parser.parse(vault_text)

    result = linter.lint(base, vault, vault_text, maturity={id_: "S1"})

    assert "E_DELETED_S1" in _codes(result)
    assert any(r.code == "E_DELETED_S1" and r.id == id_ for r in result.review_items)
    assert result.repairs == []


def test_e_deleted_s1_not_raised_for_s0() -> None:
    id_ = _id()
    base_text = _managed(f"Scratch note ^tm-{id_}\n")
    vault_text = _managed("Unrelated other text\n")
    base = parser.parse(base_text)
    vault = parser.parse(vault_text)

    result = linter.lint(base, vault, vault_text, maturity={id_: "S0"})

    assert "E_DELETED_S1" not in _codes(result)


def test_e_deleted_s1_callable_maturity_lookup() -> None:
    id_ = _id()
    base_text = _managed(f"Proven claim ^tm-{id_}\n")
    vault_text = _managed("")
    base = parser.parse(base_text)
    vault = parser.parse(vault_text)

    result = linter.lint(base, vault, vault_text, maturity=lambda i: "S3" if i == id_ else None)

    assert "E_DELETED_S1" in _codes(result)


# --- clean files ---------------------------------------------------------------


def test_file_with_no_issues_is_clean() -> None:
    id_ = _id()
    text = _managed(f"All good ^tm-{id_}\n")
    bs = parser.parse(text)
    result = linter.lint(bs, bs, text, maturity={id_: "S1"})
    assert result.violations == []
    assert result.repairs == []
    assert result.review_items == []


def test_fenced_anchors_are_never_findings() -> None:
    id_ = _id()
    text = f"```\nhidden ^tm-{id_}\n```\n"
    bs = parser.parse(text)
    result = linter.lint(bs, bs, text)
    assert result.violations == []
