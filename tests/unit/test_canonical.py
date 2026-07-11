"""Unit tests for kernel/canonical.py (spec §4.3, task T2.2)."""

import hashlib
import json
import unicodedata

from akasha.kernel.canonical import canonical_json, canonicalize_text, object_hash


def test_crlf_converted_to_lf():
    assert canonicalize_text("a\r\nb\r\nc") == "a\nb\nc\n"


def test_cr_only_converted_to_lf():
    assert canonicalize_text("a\rb\rc") == "a\nb\nc\n"


def test_mixed_line_endings():
    assert canonicalize_text("a\r\nb\nc\r") == "a\nb\nc\n"


def test_nfd_normalized_to_nfc():
    nfd = unicodedata.normalize("NFD", "café")
    nfc = unicodedata.normalize("NFC", "café")
    assert nfd != nfc  # sanity: the two forms really do differ byte-wise
    assert canonicalize_text(nfd) == nfc + "\n"


def test_trailing_whitespace_stripped_per_line():
    assert canonicalize_text("a   \nb\t\nc  ") == "a\nb\nc\n"


def test_zero_trailing_newlines_gets_exactly_one():
    assert canonicalize_text("foo") == "foo\n"


def test_multiple_trailing_newlines_collapsed_to_one():
    assert canonicalize_text("foo\n\n\n\n") == "foo\n"


def test_empty_string_yields_single_newline():
    assert canonicalize_text("") == "\n"


def test_tabs_expanded_outside_code_fence():
    result = canonicalize_text("a\tb")
    assert "\t" not in result
    assert result == "a\tb".expandtabs() + "\n"


def test_tabs_preserved_inside_code_fence():
    src = "before\n```\ncode\twith\ttabs\n```\nafter"
    result = canonicalize_text(src)
    lines = result.split("\n")
    assert lines[2] == "code\twith\ttabs"


def test_tabs_expanded_before_and_after_fence_but_not_inside():
    src = "a\tb\n```\nc\td\n```\ne\tf"
    result = canonicalize_text(src)
    lines = result.split("\n")
    assert lines[0] == "a\tb".expandtabs()
    assert lines[2] == "c\td"  # preserved
    assert lines[4] == "e\tf".expandtabs()


def test_trailing_whitespace_stripped_even_inside_fence():
    src = "```\ncode   \n```"
    result = canonicalize_text(src)
    assert result == "```\ncode\n```\n"


def test_tilde_fence_also_recognized():
    src = "~~~\ncode\twith\ttab\n~~~"
    result = canonicalize_text(src)
    lines = result.split("\n")
    assert lines[1] == "code\twith\ttab"


def test_canonical_json_matches_spec_formula():
    obj = {"b": 1, "a": [1, 2, 3], "c": "héllo"}
    expected = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    assert canonical_json(obj) == expected


def test_canonical_json_sorts_keys_and_uses_compact_separators():
    obj = {"z": 1, "a": 2}
    assert canonical_json(obj) == b'{"a":2,"z":1}'


def test_canonical_json_preserves_non_ascii():
    obj = {"name": "héllo"}
    result = canonical_json(obj)
    assert result == '{"name":"héllo"}'.encode()
    assert b"\\u" not in result


def test_object_hash_is_sha256_hex():
    data = b"hello world"
    expected = hashlib.sha256(data).hexdigest()
    assert object_hash(data) == expected
    assert len(object_hash(data)) == 64


def test_object_hash_of_canonical_json_is_deterministic():
    obj1 = {"b": 2, "a": 1}
    obj2 = {"a": 1, "b": 2}
    assert object_hash(canonical_json(obj1)) == object_hash(canonical_json(obj2))
