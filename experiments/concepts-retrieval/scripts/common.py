"""Shared helpers: vault paths, note cleaning, and markdown block parsing."""

from __future__ import annotations

import re
from pathlib import Path

# EXP is tracked in git (scripts, README, aggregate results with no note text). DATA sits under the
# git-ignored data/ because everything in it quotes the personal vault: the datasets, the gold
# questions, retrieval contexts, raw model responses, and the per-question evidence report.
EXP = Path(__file__).resolve().parents[1]
REPO = EXP.parents[1]
DATA = REPO / "data" / "experiments" / "concepts-retrieval"
SOURCE_VAULT = REPO / "data" / "(10) Concepts"
REGULAR = DATA / "datasets" / "regular"
CLEAN = DATA / "datasets" / "clean"
AKASHA = DATA / "datasets" / "akasha"

FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.S)
FENCE_RE = re.compile(r"^\s*```")
WIKILINK_RE = re.compile(r"(?<!!)\[\[([^\]|#]*)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")

# Template scaffolding lines that carry no note-specific content.
BOILERPLATE_LINE_RES = [
    re.compile(r"^\s*\\?\*.*Quick Notes - each idea should have its own note!\s*\*\s*$"),
    re.compile(r"^\s*\*All source-notes .*\*\s*$"),
    re.compile(r"^\s*-\s*`\[\[ \]\]` Link to the sources here!\s*$"),
    re.compile(r"^\s*`INPUT\[.*\]`\s*$"),
    re.compile(r"^\s*`=this\.file\.link.*`.*$"),
    re.compile(r"^\s*-?\s*(General|Related|Specified) concepts:\s*(\[\[\s*\]\])?\s*$"),
    re.compile(r"^\s*!\[\[[^\]]+\.(png|jpg|jpeg|gif|webp|svg|pdf)(\|[^\]]*)?\]\]\s*$", re.I),
    re.compile(r"^\s*#{1,6}\s*Summary:\s*$"),
]
EMPTY_LINK_RE = re.compile(r"\s*\[\[\s*\]\]\s*")


def note_title(rel: str) -> str:
    return Path(rel).stem


def split_frontmatter(text: str) -> tuple[str, str]:
    m = FRONTMATTER_RE.match(text)
    return (m.group(1), text[m.end() :]) if m else ("", text)


def frontmatter_aliases(fm: str) -> list[str]:
    out: list[str] = []
    in_aliases = False
    for line in fm.splitlines():
        if re.match(r"^aliases:\s*$", line):
            in_aliases = True
            continue
        if in_aliases and re.match(r"^\s+-\s+", line):
            val = re.sub(r"^\s+-\s+", "", line).strip().strip("\"'")
            if val and not val.startswith("#"):
                out.append(val)
            continue
        in_aliases = False
        m = re.match(r"^aliases:\s*(\S.*)$", line)
        if m:
            out += [v.strip().strip("\"'") for v in m.group(1).strip("[]").split(",") if v.strip()]
    return out


def clean_note(text: str) -> str:
    """Deterministic boilerplate strip. Content lines are kept byte-for-byte (minus an
    empty `[[]]` placeholder), so verbatim gold spans survive."""
    fm, body = split_frontmatter(text.replace("\r\n", "\n"))
    lines: list[str] = []
    aliases = frontmatter_aliases(fm)
    if aliases:
        lines.append("Aliases: " + ", ".join(aliases))
    in_fence = False
    for line in body.split("\n"):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:  # dataview/dataviewjs/code blocks: dropped
            continue
        if any(r.match(line) for r in BOILERPLATE_LINE_RES):
            continue
        if "[[]]" in line or "[[ ]]" in line:
            line = EMPTY_LINK_RE.sub(" ", line).rstrip()
            if re.match(r"^\s*-?\s*(General|Related|Specified) concepts:\s*(\|\|?)?\s*$", line):
                continue
        lines.append(line.rstrip())
    # drop headings whose section has no content (repeat: removing one can empty its parent)
    while True:
        out: list[str] = []
        for i, line in enumerate(lines):
            if re.match(r"^#{1,6}\s", line):
                nxt = next((nl for nl in lines[i + 1 :] if nl.strip()), None)
                if nxt is None or (re.match(r"^#{1,6}\s", nxt) and _level(nxt) <= _level(line)):
                    continue
            out.append(line)
        if len(out) == len(lines):
            break
        lines = out
    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"
    return text


def _level(h: str) -> int:
    return len(h) - len(h.lstrip("#"))


def content_chars(text: str) -> int:
    return len(re.sub(r"\s+", "", clean_note(text)))
