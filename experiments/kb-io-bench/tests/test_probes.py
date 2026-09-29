"""V4 probe helpers: template round trips, UPDATE rewrites, hop visibility (no KILT data needed)."""

from __future__ import annotations

import random

from kbio import probes
from kbio.perturb import FICTIONAL


def _sentences() -> list[tuple[str, str, str]]:
    vals = {
        "title": "The Hollywood Reporter",
        "sur": "Straughan",
        "first": "Edith",
        "town": "Brantwick",
        "year": "1884",
        "code": "55-Q",
        "count": "113",
        "shelf": "MS K.12.34",
        "person": "Edith Straughan",
    }
    return [(t.format(**vals), k, vals[k]) for t, _q, k in FICTIONAL]


def test_groups_marker_and_rewrite() -> None:
    for s, key, ans in _sentences():
        k, g = probes.groups(s)
        assert k == key
        mk = probes.marker(s)
        assert (mk is None) == (key == "person")
        new = {"person": "Tobias Marlow", "count": "214", "year": "1901"}.get(key, "zz")
        out = probes.with_value(s, new)
        assert new in out and ans not in out.replace(g["title"], "")
        assert probes.groups(out)[1]["title"] == g["title"]


def test_new_value_is_fresh_and_recorded() -> None:
    used: set[str] = {"code:55-Q"}
    rng = random.Random(1)
    seen = set()
    for _ in range(50):
        v = probes.new_value("code", rng, used, "55-Q")
        assert v != "55-Q" and v not in seen
        seen.add(v)
    assert {f"code:{v}" for v in seen} <= used


def test_mentions_is_whole_word_case_insensitive() -> None:
    assert probes.mentions("Jazz", "a jazz singer")
    assert not probes.mentions("Jazz", "jazzy tunes")
    assert probes.mentions("U.S. state", "a U.S. state in the west")
