"""Contract grammar v1: tokens, regexes, and the contract version constant.

Build-plan task T3.1, spec §4.7 ("Contract grammar v1 (Obsidian sublanguage)").
This module holds only patterns/constants for the line-oriented Obsidian
sublanguage grammar — no parsing logic (that is T3.2's `contract/parser.py`).

The id8 alphabet/length come from ``kernel.ids`` (spec §4.1) and are reused
here, not duplicated, per the task instructions.

EBNF reproduced verbatim from mvp-spec.md §4.7::

    anchor      := "^tm-" id8
    managed_par := text SP anchor EOL                       ; paragraph node
    task_line   := indent "- [" ("x"|" ") "] " text SP anchor EOL
    new_line    := (text | task_form) SP "^tm-new" EOL      ; user requests minting
    embed       := "![[" path "#^tm-" id8 "]]"              ; read-only transclusion
    ref         := "[[" path "#^tm-" id8 "]]"               ; inline reference
    indent      := (2 spaces)*                              ; nesting depth = indent/2

All line-level patterns operate on a single logical line of managed-file text (no
trailing ``\\n``, per the canonical LF/one-trailing-newline rules in
kernel/canonical.py — this module does not itself split files into lines).

An anchor only counts as a *real* anchor when it sits at end-of-line
(optionally followed by trailing whitespace); the same substring appearing
mid-line is plain text (spec §4.7: "Text matching the anchor pattern *not*
at end-of-line is plain text.").
"""

from __future__ import annotations

import re

from akasha.kernel.ids import ID_LEN, A

# --- Contract version -------------------------------------------------------

# Front-matter key `tm: <version>` marks a managed file (spec §4.7 file-level
# rule); version 1 is the v1 grammar frozen in this module.
CONTRACT_VERSION = 1

# --- id8 / anchor ------------------------------------------------------------

# id8: exactly ID_LEN chars drawn from the base32 alphabet A (spec §4.1).
# Reuses kernel.ids's alphabet/length rather than re-declaring them.
_ID8_ALPHABET_CLASS = "[" + re.escape(A) + "]"
ID8_PATTERN = f"{_ID8_ALPHABET_CLASS}{{{ID_LEN}}}"
ID8_RE = re.compile(f"^{ID8_PATTERN}$")

# anchor := "^tm-" id8 — matches anywhere (mid-line or at EOL); callers that
# need the "real anchor" (EOL-only) semantics use ANCHOR_EOL_RE below.
ANCHOR_PATTERN = rf"\^tm-(?P<id>{ID8_PATTERN})"
ANCHOR_RE = re.compile(ANCHOR_PATTERN)

# A "real" anchor per spec §4.7: preceded by whitespace and sitting at
# end-of-line (trailing whitespace tolerated, but nothing else after it).
# A match of ANCHOR_RE that does *not* also satisfy this is plain text.
ANCHOR_EOL_RE = re.compile(rf"\s+{ANCHOR_PATTERN}\s*$")

# `^tm-new` marker (new_line token): also only meaningful at end-of-line.
NEW_MARKER_EOL_RE = re.compile(r"\s+\^tm-new\s*$")

# --- indent --------------------------------------------------------------

# indent := (2 spaces)*  — nesting depth = indent / 2.
INDENT_RE = re.compile(r"^(?P<indent> *)")
INDENT_UNIT = "  "  # 2 spaces per spec §4.7


def indent_depth(indent: str) -> int:
    """Nesting depth for an ``indent`` token: ``len(indent) // 2`` (spec §4.7).

    ``indent`` must be a run of spaces only (typically the ``indent`` capture
    group of ``TASK_LINE_RE`` or ``INDENT_RE``); an odd number of spaces has
    no defined depth under the grammar and is treated as a floor division
    (narrowest reading — no half-depths).
    """
    return len(indent) // 2


# --- managed_par -----------------------------------------------------------

# managed_par := text SP anchor EOL
MANAGED_PAR_RE = re.compile(rf"^(?P<text>\S.*?)\s+{ANCHOR_PATTERN}\s*$")

# --- task_line ---------------------------------------------------------------

# task_line := indent "- [" ("x"|" ") "] " text SP anchor EOL
TASK_LINE_RE = re.compile(
    rf"^(?P<indent>(?: {{2}})*)- \[(?P<state>[x ])\] (?P<text>\S.*?)\s+{ANCHOR_PATTERN}\s*$"
)

# --- new_line ------------------------------------------------------------

# new_line := (text | task_form) SP "^tm-new" EOL
#
# SPEC-QUESTION (narrowest reading, see docs/spec-questions.md T3.1 entry):
# `task_form` is used but not itself defined by the §4.7 EBNF block. The
# narrowest reading is that it mirrors task_line's prefix (indent + the
# "- [x|space] " checkbox marker) applied to un-anchored text, i.e. a task
# line that has no anchor yet and is requesting one via `^tm-new`.
NEW_LINE_RE = re.compile(
    r"^(?:"
    r"(?P<indent>(?: {2})*)- \[(?P<state>[x ])\] (?P<task_text>\S.*?)"
    r"|"
    r"(?P<text>\S.*?)"
    r")\s+\^tm-new\s*$"
)

# --- embed / ref -----------------------------------------------------------

# path: anything up to the "#^tm-" delimiter, excluding "]" and "#".
PATH_PATTERN = r"(?P<path>[^\]#]+)"

# embed := "![[" path "#^tm-" id8 "]]"
EMBED_RE = re.compile(rf"!\[\[{PATH_PATTERN}#{ANCHOR_PATTERN}\]\]")

# ref := "[[" path "#^tm-" id8 "]]" — not preceded by "!" (that's an embed).
REF_RE = re.compile(rf"(?<!!)\[\[{PATH_PATTERN}#{ANCHOR_PATTERN}\]\]")

# --- fenced code blocks ------------------------------------------------------

# Fence delimiter line (``` or longer run of backticks), spec §4.7: "Anything
# inside fenced code blocks is ignored entirely." This module exposes only
# the fence-line token; fence-state tracking across lines is parser logic
# (T3.2), not this module's job.
FENCE_RE = re.compile(r"^ {0,3}`{3,}")

# --- front matter ------------------------------------------------------------

# Front-matter key `tm: <version>` marking a managed file (spec §4.7
# file-level rule). Only the key/value line token, not YAML parsing.
FRONT_MATTER_TM_RE = re.compile(r"^tm:\s*(?P<version>\d+)\s*$")
