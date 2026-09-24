"""`.tmignore` deny-list matcher (build-plan T18.10a, ruling M18-B). Pure, no I/O.

Every Markdown file under a sync root is tracked by default; a `.tmignore`
file at the root opts paths out. The name is the neutral `tm` prefix (rule 6).
`.gitignore` is deliberately not consulted.

Supported subset (gitignore-style, stdlib only):

- blank lines and lines starting with ``#`` are ignored;
- ``*`` (any run of characters except ``/``), ``?`` (one such character),
  ``**`` (any run including ``/``: ``**/x``, ``x/**``, ``a/**/b``);
- a trailing ``/`` makes the pattern match directories only;
- a pattern containing a ``/`` other than a trailing one, or starting with
  ``/``, is anchored to the root; any other pattern matches at any depth;
- a leading ``!`` re-includes what an earlier pattern excluded;
- the last matching pattern wins; a file below an ignored directory stays
  ignored (as in git, a file cannot be re-included once its parent is out).

Not supported -- skipped with a returned warning, never guessed at: ``[...]``
character classes and backslash escapes.

Built-in defaults apply before the user's patterns and can be overridden by
them with ``!``: ``.obsidian/``, ``.git/``, ``.trash/``, ``node_modules/``,
and every file that is not ``*.md`` (case-insensitive, like the watcher's own
filter). ``!notes.txt`` would lift the last, but the watcher's own Markdown-only
filter still applies on top of this matcher.

Paths are POSIX, root-relative. A Windows-separator input gives the same answer.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache

DEFAULT_DIRECTORIES: tuple[str, ...] = (".obsidian/", ".git/", ".trash/", "node_modules/")


@dataclass(frozen=True)
class _Rule:
    regex: re.Pattern[str]
    negate: bool
    dir_only: bool
    file_only: bool = False


def _translate(body: str) -> str:
    """Translate a gitignore glob body (no leading ``!``/``/``, no trailing ``/``) to a regex."""
    out: list[str] = []
    i = 0
    n = len(body)
    while i < n:
        c = body[i]
        if body.startswith("**/", i) and (i == 0 or body[i - 1] == "/"):
            out.append("(?:.*/)?")
            i += 3
        elif body.startswith("/**", i) and i + 3 == n:
            out.append("/.*")
            i += 3
        elif c == "*":
            j = i
            while j < n and body[j] == "*":
                j += 1
            out.append(".*" if j - i >= 2 else "[^/]*")
            i = j
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return "".join(out)


@cache
def _compile(pattern: str) -> _Rule | None:
    """Compile one already-vetted pattern line, or None if it is not a rule."""
    line = pattern.rstrip()
    if not line or line.startswith("#"):
        return None
    negate = line.startswith("!")
    if negate:
        line = line[1:]
    dir_only = line.endswith("/")
    line = line.rstrip("/")
    anchored = line.startswith("/") or "/" in line
    line = line.lstrip("/")
    if not line:
        return None
    body = _translate(line)
    prefix = "" if anchored else "(?:.*/)?"
    return _Rule(re.compile(f"{prefix}{body}"), negate, dir_only)


def parse_patterns(text: str) -> tuple[list[str], list[str]]:
    """Split `.tmignore` text into (usable pattern lines, warnings).

    Comments and blank lines are dropped. A line using an unsupported
    construct is skipped and reported, never approximated.
    """
    patterns: list[str] = []
    warnings: list[str] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        line = line.lstrip()
        if "[" in line or "\\" in line:
            warnings.append(
                f".tmignore line {lineno}: {line!r} skipped "
                "(character classes and backslash escapes are not supported)"
            )
            continue
        patterns.append(line)
    return patterns, warnings


def _defaults() -> tuple[_Rule, ...]:
    rules: list[_Rule] = []
    for p in DEFAULT_DIRECTORIES:
        rule = _compile(p)
        assert rule is not None
        rules.append(rule)
    rules.append(_Rule(re.compile(".*"), negate=False, dir_only=False, file_only=True))
    markdown = re.compile("(?:.*/)?[^/]*\\.md", re.IGNORECASE)
    rules.append(_Rule(markdown, negate=True, dir_only=False, file_only=True))
    return tuple(rules)


_DEFAULTS = _defaults()


def _verdict(path: str, is_dir: bool, user: tuple[_Rule, ...]) -> bool:
    ignored = False
    for rule in (*_DEFAULTS, *user):
        if rule.dir_only and not is_dir:
            continue
        if rule.file_only and is_dir:
            continue
        if rule.regex.fullmatch(path):
            ignored = not rule.negate
    return ignored


def is_ignored(rel_path: str, patterns: Sequence[str], *, is_dir: bool = False) -> bool:
    """True when `rel_path` (root-relative) is excluded from tracking.

    `patterns` are `.tmignore` lines as returned by `parse_patterns`; anything
    unusable in them is skipped, so this never raises on arbitrary text.
    """
    path = rel_path.replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    path = path.strip("/")
    if not path or path == ".":
        return False
    user = tuple(r for p in patterns if (r := _compile_safe(p)) is not None)
    parts = path.split("/")
    for i in range(1, len(parts)):
        if _verdict("/".join(parts[:i]), True, user):
            return True
    return _verdict(path, is_dir, user)


def _compile_safe(pattern: str) -> _Rule | None:
    try:
        return _compile(pattern)
    except re.error:
        return None
