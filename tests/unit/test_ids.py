"""Tests for kernel/ids.py (build-plan task T2.1, spec §4.1)."""

from __future__ import annotations

import pytest

from akasha.kernel.ids import A, IdError, checksum, contract_anchor, mint, validate

# Known checksum vectors, computed independently from the spec formula
# checksum(core) = A[sum((i+1) * A.index(c) for i, c in enumerate(core)) % 32]
KNOWN_VECTORS = [
    ("abcdefg", "q"),
    ("aaaaaaa", "a"),
    ("zzzzzzz", "4"),
    ("a234567", "t"),
    ("gfedcba", "y"),
]


@pytest.mark.parametrize("core,expected", KNOWN_VECTORS)
def test_checksum_known_vectors(core: str, expected: str) -> None:
    assert checksum(core) == expected


def test_checksum_covers_at_least_three_vectors() -> None:
    assert len(KNOWN_VECTORS) >= 3


def test_mint_produces_valid_id() -> None:
    for _ in range(200):
        id_ = mint()
        assert len(id_) == 8
        assert all(c in A for c in id_)
        # Must not raise.
        validate(id_)


def test_mint_ids_are_from_alphabet_and_unique_enough() -> None:
    ids = {mint() for _ in range(500)}
    # secrets-backed randomness over a large space should not collide here.
    assert len(ids) == 500


def test_validate_known_vector_id() -> None:
    core, check = KNOWN_VECTORS[0]
    validate(core + check)


def test_validate_bad_checksum_raises_e_id_checksum() -> None:
    core, check = KNOWN_VECTORS[0]
    # Flip to a char guaranteed different from the correct checksum.
    bad_check = "a" if check != "a" else "b"
    with pytest.raises(IdError) as exc_info:
        validate(core + bad_check)
    assert exc_info.value.code == "E_ID_CHECKSUM"


def test_validate_wrong_length_raises() -> None:
    with pytest.raises(IdError) as exc_info:
        validate("short")
    assert exc_info.value.code == "E_ID_CHECKSUM"

    with pytest.raises(IdError):
        validate("toolongtoolong")


def test_validate_bad_alphabet_char_raises() -> None:
    core, check = KNOWN_VECTORS[0]
    with pytest.raises(IdError) as exc_info:
        validate("1" + core[1:] + check)  # "1", "0", "8", "9" are outside A
    assert exc_info.value.code == "E_ID_CHECKSUM"


def test_contract_anchor_form() -> None:
    core, check = KNOWN_VECTORS[0]
    id_ = core + check
    assert contract_anchor(id_) == f"^tm-{id_}"


def test_contract_anchor_of_minted_id() -> None:
    id_ = mint()
    anchor = contract_anchor(id_)
    assert anchor.startswith("^tm-")
    assert anchor[len("^tm-") :] == id_
