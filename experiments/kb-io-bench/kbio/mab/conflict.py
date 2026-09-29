"""M13 conflict rule: one decision function, shared by the plaintext control (R-rule) and akasha
(K1/K2), so that only the store differs. Fixed before any answer is seen; it knows nothing about
this dataset's relation templates.

Two facts conflict iff, as normalized token sequences, they share a prefix of at least MIN_PREFIX
tokens and at least half of the longer fact (so the relation, not just the subject, is shared:
"Hun Sen is a citizen of X" vs "Hun Sen is married to Y" share only "hun sen is"), differ only in
one span that runs to the end of both (the object slot), and the two
differing spans share no token (a shared token means a second difference, e.g. another subject:
"The chairperson of Fatah is X" vs "The chairperson of Hamas is Y" differ in "fatah is x" / "hamas
is y", which share "is"). Identical facts do not conflict."""

from __future__ import annotations

import re
from collections import defaultdict

MIN_PREFIX = 3
SERIAL_RE = re.compile(r"^\s*\d+\.\s+")
TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def tokens(fact: str) -> list[str]:
    return TOKEN_RE.findall(SERIAL_RE.sub("", fact).lower())


def conflicts(a: str, b: str) -> bool:
    ta, tb = tokens(a), tokens(b)
    if ta == tb:
        return False
    p = 0
    while p < min(len(ta), len(tb)) and ta[p] == tb[p]:
        p += 1
    ra, rb = ta[p:], tb[p:]
    long_enough = p >= MIN_PREFIX and 2 * p >= max(len(ta), len(tb))
    return long_enough and bool(ra) and bool(rb) and not (set(ra) & set(rb))


def supersede_plain(facts: list[tuple[int, str]]) -> tuple[set[int], list[tuple[int, int]]]:
    """The plaintext implementation of the rule (R-rule): facts in serial order; each new fact
    retires every earlier live fact it conflicts with. Candidates are the earlier facts sharing
    the first MIN_PREFIX tokens (a necessary condition of `conflicts`, so this is exhaustive).
    Returns (retired serials, [(newer, older)] pairs)."""
    buckets: dict[tuple[str, ...], list[int]] = defaultdict(list)
    text = dict(facts)
    retired: set[int] = set()
    pairs: list[tuple[int, int]] = []
    for serial, fact in sorted(facts):
        key = tuple(tokens(fact)[:MIN_PREFIX])
        for old in buckets[key]:
            if old not in retired and conflicts(fact, text[old]):
                retired.add(old)
                pairs.append((serial, old))
        buckets[key].append(serial)
    return retired, pairs
