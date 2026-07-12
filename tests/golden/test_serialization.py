"""Golden byte-exact serialization corpus (spec §4.3, tasks T2.4 / T3.7).

Each case directory under tests/golden/serialization/<case>/ contains an
input.md and an expected.md. This test asserts that
canonicalize_text(input.md) reproduces expected.md byte-for-byte.

Golden files are never edited to make an implementation pass (see root
CLAUDE.md rule 0.3); if canonical.py's behavior changes intentionally, the
fixtures must be regenerated via an explicit build-plan task, not hand-edited
here.

T3.7 adds ``contract_*`` cases that also exercise the §4.7 grammar / linter,
plus a committed fuzz corpus directory under ``serialization/fuzz/``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from akasha.contract import linter, parser
from akasha.kernel.canonical import canonicalize_text

GOLDEN_ROOT = Path(__file__).parent / "serialization"
FUZZ_ROOT = GOLDEN_ROOT / "fuzz"


def _discover_cases() -> list[Path]:
    if not GOLDEN_ROOT.is_dir():
        return []
    return sorted(
        p
        for p in GOLDEN_ROOT.iterdir()
        if p.is_dir() and (p / "input.md").is_file() and (p / "expected.md").is_file()
    )


def _discover_contract_cases() -> list[Path]:
    """Contract-grammar cases added in T3.7 (``contract_*`` prefix)."""
    return [p for p in _discover_cases() if p.name.startswith("contract_")]


CASES = _discover_cases()
CONTRACT_CASES = {p.name: p for p in _discover_contract_cases()}


def test_at_least_fifteen_cases_present():
    assert len(CASES) >= 15, (
        f"expected >=15 golden serialization cases, found {len(CASES)} under {GOLDEN_ROOT}"
    )


@pytest.mark.parametrize("case_dir", CASES, ids=[c.name for c in CASES])
def test_canonicalize_text_matches_golden_expected(case_dir: Path):
    input_bytes = (case_dir / "input.md").read_bytes()
    expected_bytes = (case_dir / "expected.md").read_bytes()

    actual_text = canonicalize_text(input_bytes.decode("utf-8"))
    actual_bytes = actual_text.encode("utf-8")

    assert actual_bytes == expected_bytes, (
        f"canonicalize_text output for case {case_dir.name!r} does not match "
        f"golden expected.md byte-for-byte"
    )


# --- T3.7: contract-focused corpus + fuzz stub --------------------------------

_REQUIRED_CONTRACT_CASES = frozenset(
    {
        "contract_tasks",
        "contract_nesting",
        "contract_embeds",
        "contract_refs",
        "contract_tm_new",
        "contract_e_id_checksum",
        "contract_e_dup_id",
        "contract_e_lost_anchor",
        "contract_e_deleted_s1",
        "contract_w_unmanaged_anchor",
        "contract_pause_and_diff",
    }
)


def test_at_least_twenty_five_cases_and_required_contract_set():
    """M3 / T3.7 DoD: ≥25 golden cases including the contract-focused set."""
    assert len(CASES) >= 25, (
        f"expected >=25 golden serialization cases, found {len(CASES)} under {GOLDEN_ROOT}"
    )
    missing = _REQUIRED_CONTRACT_CASES - CONTRACT_CASES.keys()
    assert not missing, f"missing required contract_* golden cases: {sorted(missing)}"


def _read(case: Path, name: str) -> str:
    return (case / name).read_text(encoding="utf-8")


def test_contract_focused_cases_parse_and_lint_behavior():
    """Parse/lint each ``contract_*`` fixture and assert the behavior it demos."""
    missing = _REQUIRED_CONTRACT_CASES - CONTRACT_CASES.keys()
    assert not missing, f"missing required contract_* golden cases: {sorted(missing)}"

    # --- tasks: open + done task lines ---
    case = CONTRACT_CASES["contract_tasks"]
    bs = parser.parse(_read(case, "input.md"))
    assert bs.managed is True
    assert len(bs.blocks) == 2
    assert {b.kind for b in bs.blocks.values()} == {"task"}
    assert {b.task_state for b in bs.blocks.values()} == {"open", "done"}

    # --- nesting: parent depth-0 with two depth-1 children ---
    case = CONTRACT_CASES["contract_nesting"]
    bs = parser.parse(_read(case, "input.md"))
    parent = next(b for b in bs.blocks.values() if b.depth == 0)
    children = [b for b in bs.blocks.values() if b.depth == 1]
    assert parent.kind == "task"
    assert len(children) == 2
    assert all(c.parent_id == parent.id for c in children)

    # --- embeds ---
    case = CONTRACT_CASES["contract_embeds"]
    bs = parser.parse(_read(case, "input.md"))
    assert len(bs.embeds) == 1
    assert bs.embeds[0].path == "claims/water.md"
    assert bs.embeds[0].id  # checksum-valid id8 captured by grammar

    # --- refs ---
    case = CONTRACT_CASES["contract_refs"]
    bs = parser.parse(_read(case, "input.md"))
    assert len(bs.refs) == 1
    assert bs.refs[0].path == "notes/boiling.md"
    assert bs.refs[0].id

    # --- ^tm-new markers (paragraph + task forms) ---
    case = CONTRACT_CASES["contract_tm_new"]
    bs = parser.parse(_read(case, "input.md"))
    assert len(bs.new_requests) == 2
    assert {n.shape for n in bs.new_requests} == {"paragraph", "task"}

    # --- E_ID_CHECKSUM: deliberate invalid checksum → review, never repair ---
    case = CONTRACT_CASES["contract_e_id_checksum"]
    vault_text = _read(case, "input.md")
    vault = parser.parse(vault_text)
    result = linter.lint(parser.parse("---\ntm: 1\n---\n"), vault, vault_text)
    assert any(v.code == "E_ID_CHECKSUM" for v in result.violations)
    assert any(r.code == "E_ID_CHECKSUM" for r in result.review_items)
    assert result.repairs == []

    # --- E_DUP_ID: copy-paste → certain propose_tm_new repair ---
    case = CONTRACT_CASES["contract_e_dup_id"]
    base = parser.parse(_read(case, "base.md"))
    vault_text = _read(case, "input.md")
    vault = parser.parse(vault_text)
    result = linter.lint(base, vault, vault_text)
    assert any(v.code == "E_DUP_ID" for v in result.violations)
    assert any(r.action == "propose_tm_new" for r in result.repairs)
    assert result.repairs[0].after.endswith("^tm-new")

    # --- E_LOST_ANCHOR: byte-identical body → reinsert_anchor repair ---
    case = CONTRACT_CASES["contract_e_lost_anchor"]
    base = parser.parse(_read(case, "base.md"))
    vault_text = _read(case, "input.md")
    vault = parser.parse(vault_text)
    result = linter.lint(base, vault, vault_text)
    assert any(v.code == "E_LOST_ANCHOR" for v in result.violations)
    assert any(r.action == "reinsert_anchor" for r in result.repairs)

    # --- E_DELETED_S1: gone block at S1+ → review, never repair ---
    case = CONTRACT_CASES["contract_e_deleted_s1"]
    base = parser.parse(_read(case, "base.md"))
    vault_text = _read(case, "input.md")
    vault = parser.parse(vault_text)
    deleted_id = next(iter(base.blocks))
    result = linter.lint(base, vault, vault_text, maturity={deleted_id: "S1"})
    assert any(v.code == "E_DELETED_S1" and v.id == deleted_id for v in result.violations)
    assert any(r.code == "E_DELETED_S1" for r in result.review_items)
    assert result.repairs == []

    # --- W_UNMANAGED_ANCHOR: advisory in unmanaged file ---
    case = CONTRACT_CASES["contract_w_unmanaged_anchor"]
    vault_text = _read(case, "input.md")
    vault = parser.parse(vault_text)
    assert vault.managed is False
    result = linter.lint(parser.parse(""), vault, vault_text)
    assert any(v.code == "W_UNMANAGED_ANCHOR" for v in result.violations)
    assert any(r.code == "W_UNMANAGED_ANCHOR" for r in result.review_items)
    assert result.repairs == []

    # --- pause-and-diff: >25% affected ⇒ PauseDecision with unified diff ---
    case = CONTRACT_CASES["contract_pause_and_diff"]
    base_text = _read(case, "base.md")
    vault_text = _read(case, "input.md")
    base = parser.parse(base_text)
    vault = parser.parse(vault_text)
    result = linter.lint(base, vault, vault_text)
    assert linter.pause_threshold(result, base) is True
    decision = linter.pause_and_diff(result, base, base_text, vault_text)
    assert decision is not None
    assert decision.snapshot == vault_text
    assert decision.review_item.message != ""
    assert "---" in decision.review_item.message or "+++" in decision.review_item.message


def test_fuzz_corpus_directory_present_and_green():
    """T3.7: fuzz corpus path exists; empty is OK when no falsifying example."""
    assert FUZZ_ROOT.is_dir(), f"expected fuzz corpus directory at {FUZZ_ROOT}"
    readme = FUZZ_ROOT / "README.md"
    assert readme.is_file() and readme.stat().st_size > 0, (
        f"expected non-empty {readme} when no shrunk Hypothesis failures are committed"
    )
    # Any future fixture files under fuzz/ must be non-empty if present.
    for path in sorted(FUZZ_ROOT.rglob("*")):
        if path.is_file() and path.name != "README.md":
            assert path.stat().st_size > 0, f"empty fuzz fixture: {path}"
