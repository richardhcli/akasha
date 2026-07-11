"""Byte-level canonicalization for text and JSON (spec §4.3).

This is the *only* module that normalizes text or serializes objects for
hashing; no other module should re-implement these rules (build-plan rule 0.5
note in docs/mvp-spec.md §4.3).

Text rule: UTF-8, Unicode NFC, line endings LF, no trailing whitespace on any
line, exactly one trailing newline, tabs preserved inside code fences only,
otherwise expanded to spaces on managed lines.

JSON rule (for hashing objects):
    json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

Object hash: sha256 hex digest of canonical bytes.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any

# SPEC-QUESTION: §4.3 says tabs outside code fences are "expanded to spaces
# on managed lines" but does not specify a tab width. We use Python's
# str.expandtabs() default (tabsize=8), the conventional interpretation,
# pending confirmation. See docs/spec-questions.md.
_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")


def canonicalize_text(s: str) -> str:
    """Normalize text per spec §4.3.

    - Unicode NFC normalization.
    - CRLF / CR line endings converted to LF.
    - Trailing whitespace stripped from every line.
    - Tabs preserved verbatim inside fenced code blocks (``` or ~~~ fences,
      minimal line-oriented detection, no grammar knowledge); tabs elsewhere
      are expanded to spaces.
    - Exactly one trailing newline in the result.
    """
    s = unicodedata.normalize("NFC", s)
    s = s.replace("\r\n", "\n").replace("\r", "\n")

    lines = s.split("\n")
    out_lines: list[str] = []
    in_fence = False
    for line in lines:
        is_fence_marker = bool(_FENCE_RE.match(line))
        if in_fence:
            processed = line.rstrip()
        else:
            processed = line.expandtabs().rstrip()
        out_lines.append(processed)
        if is_fence_marker:
            in_fence = not in_fence

    text = "\n".join(out_lines)
    text = text.rstrip("\n")
    return text + "\n"


def canonical_json(obj: Any) -> bytes:
    """Serialize `obj` to canonical JSON bytes per spec §4.3."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def object_hash(data: bytes) -> str:
    """Return the sha256 hex digest of `data` (canonical object hash)."""
    return hashlib.sha256(data).hexdigest()
