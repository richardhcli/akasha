"""Property test: canonicalize_text is idempotent (spec §4.3, §6.1, task T2.3).

canonicalize(canonicalize(x)) == canonicalize(x) for arbitrary text, and the
output of canonicalize_text is always valid UTF-8, Unicode NFC, with exactly
one trailing newline.
"""

from __future__ import annotations

import unicodedata

from hypothesis import given
from hypothesis import strategies as st

from akasha.kernel.canonical import canonicalize_text

# --- Building blocks that stress the canonicalization rules -----------------

_LINE_ENDINGS = st.sampled_from(["\n", "\r\n", "\r", "\r\r\n"])
_FENCE_MARKERS = st.sampled_from(["```", "~~~", "````", "~~~~", "  ```"])
_TABS_AND_SPACES = st.sampled_from(["\t", "  ", " \t ", "\t\t"])
_EMOJI = st.sampled_from(["😀", "🎉", "👩‍💻", "🇨🇦", "🧑🏽‍🚀", "❤️"])
_NFD_WORDS = st.sampled_from(
    [
        unicodedata.normalize("NFD", s)
        for s in ["café", "naïve", "Ångström", "é", "ü", "résumé", "안녕"]
    ]
)

# General unicode text, excluding surrogate codepoints (which cannot be
# encoded to UTF-8 on their own and are not valid canonicalize_text input).
_GENERAL_TEXT = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",), max_codepoint=0x2FFFF),
    max_size=30,
)

_TOKEN = st.one_of(
    _GENERAL_TEXT,
    _LINE_ENDINGS,
    _FENCE_MARKERS,
    _TABS_AND_SPACES,
    _EMOJI,
    _NFD_WORDS,
)

# A composed strategy: arbitrary unicode text plus structured pieces designed
# to exercise CRLF handling, NFD normalization, emoji, tabs, and code fences.
text_strategy = st.one_of(
    _GENERAL_TEXT,
    st.lists(_TOKEN, max_size=25).map("".join),
)


@given(text_strategy)
def test_canonicalize_is_idempotent(s: str) -> None:
    once = canonicalize_text(s)
    twice = canonicalize_text(once)
    assert once == twice


@given(text_strategy)
def test_canonicalize_output_is_valid_utf8_nfc_single_trailing_newline(s: str) -> None:
    result = canonicalize_text(s)

    # Valid UTF-8: must round-trip through encode/decode without error.
    encoded = result.encode("utf-8")
    assert encoded.decode("utf-8") == result

    # Unicode NFC.
    assert unicodedata.normalize("NFC", result) == result

    # Exactly one trailing newline: result ends with "\n" and the character
    # immediately before it (if any) is not itself a newline.
    assert result.endswith("\n")
    assert len(result) == 1 or result[-2] != "\n"
