"""Golden byte-exact serialization corpus (spec §4.3, task T2.4).

Each case directory under tests/golden/serialization/<case>/ contains an
input.md and an expected.md. This test asserts that
canonicalize_text(input.md) reproduces expected.md byte-for-byte.

Golden files are never edited to make an implementation pass (see root
CLAUDE.md rule 0.3); if canonical.py's behavior changes intentionally, the
fixtures must be regenerated via an explicit build-plan task, not hand-edited
here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from akasha.kernel.canonical import canonicalize_text

GOLDEN_ROOT = Path(__file__).parent / "serialization"


def _discover_cases() -> list[Path]:
    if not GOLDEN_ROOT.is_dir():
        return []
    return sorted(
        p
        for p in GOLDEN_ROOT.iterdir()
        if p.is_dir() and (p / "input.md").is_file() and (p / "expected.md").is_file()
    )


CASES = _discover_cases()


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
