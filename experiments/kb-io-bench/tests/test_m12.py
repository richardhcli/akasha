"""M12 pre-registered statistics helpers."""

from __future__ import annotations

from kbio.m12 import boot_diff, holm, sign_one_sided


def test_sign_test_one_sided() -> None:
    assert abs(sign_one_sided(5, 0) - 1 / 32) < 1e-12
    assert abs(sign_one_sided(3, 1) - 5 / 16) < 1e-12
    assert sign_one_sided(0, 0) == 1.0
    assert sign_one_sided(0, 4) == 1.0


def test_holm() -> None:
    assert holm([0.01, 0.04]) == [0.02, 0.04]
    assert holm([0.04, 0.01]) == [0.04, 0.02]
    assert holm([0.6, 0.7]) == [1.0, 1.0]


def test_boot_diff_is_deterministic() -> None:
    a, b = [1.0, 1.0, 0.0, 1.0], [0.0, 1.0, 0.0, 0.0]
    ci = boot_diff(a, b)
    assert ci == boot_diff(a, b) and ci is not None and ci[0] <= 0.5 <= ci[1]
