"""T18.10a -- the `.tmignore` matcher (ruling M18-B)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from akasha.sync.ignore import is_ignored, parse_patterns


def test_defaults_deny_exactly_the_listed_paths() -> None:
    for denied in (
        ".obsidian/workspace.json",
        ".obsidian/plugins/tm-hub/notes.md",
        ".git/HEAD",
        ".trash/old.md",
        "node_modules/pkg/README.md",
        "sub/node_modules/pkg/README.md",
        "image.png",
        "sub/data.txt",
        "noext",
    ):
        assert is_ignored(denied, []), denied
    for tracked in ("a.md", "sub/dir/b.md", "obsidian/x.md", "Deep/.hidden-name.md"):
        assert not is_ignored(tracked, []), tracked


def test_windows_separators_give_the_same_answer() -> None:
    assert is_ignored(".obsidian\\workspace.json", [])
    assert not is_ignored("sub\\dir\\b.md", [])
    assert is_ignored("drafts\\x.md", ["drafts/"]) == is_ignored("drafts/x.md", ["drafts/"])


def test_comments_and_blank_lines() -> None:
    pats, warns = parse_patterns("# comment\n\n  \nprivate.md\n")
    assert pats == ["private.md"] and warns == []
    assert is_ignored("private.md", pats)


def test_star_question_and_depth() -> None:
    assert is_ignored("a/b/scratch-1.md", ["scratch-?.md"])
    assert not is_ignored("a/scratch-12.md", ["scratch-?.md"])
    assert is_ignored("x/draft-anything.md", ["draft-*.md"])
    # `*` never crosses a slash in an anchored pattern
    assert not is_ignored("a/b/c.md", ["a/*.md"])
    assert is_ignored("a/c.md", ["a/*.md"])


def test_double_star() -> None:
    assert is_ignored("a/b/c/secret.md", ["**/secret.md"])
    assert is_ignored("secret.md", ["**/secret.md"])
    assert is_ignored("private/x/y/z.md", ["private/**"])
    assert is_ignored("a/x.md", ["a/**/x.md"])
    assert is_ignored("a/b/c/x.md", ["a/**/x.md"])
    assert not is_ignored("b/x.md", ["a/**/x.md"])


def test_trailing_slash_is_directory_only() -> None:
    assert is_ignored("build/out.md", ["build/"])
    assert not is_ignored("build.md", ["build/"])
    # the same name as a *file* is not matched by a directory-only pattern
    assert not is_ignored("notes.md", ["notes.md/"])
    assert is_ignored("notes.md", ["notes.md/"], is_dir=True)
    assert is_ignored("build", ["build/"], is_dir=True)


def test_leading_slash_anchors_to_root() -> None:
    assert is_ignored("todo.md", ["/todo.md"])
    assert not is_ignored("sub/todo.md", ["/todo.md"])
    assert is_ignored("sub/todo.md", ["todo.md"])
    assert not is_ignored("x/sub/todo.md", ["sub/todo.md"])  # a slash anchors it
    assert is_ignored("sub/todo.md", ["sub/todo.md"])


def test_negation_and_last_match_wins() -> None:
    pats = ["*.md", "!keep.md"]
    assert is_ignored("a.md", pats)
    assert not is_ignored("keep.md", pats)
    assert not is_ignored("sub/keep.md", pats)
    # order matters: a later broad pattern beats an earlier negation
    assert is_ignored("keep.md", ["!keep.md", "*.md"])


def test_negation_lifts_a_default() -> None:
    assert not is_ignored(".obsidian/notes/x.md", ["!.obsidian/"])
    assert not is_ignored("notes.txt", ["!notes.txt"])


def test_file_below_an_ignored_directory_cannot_be_reincluded() -> None:
    assert is_ignored("logs/keep.md", ["logs/", "!logs/keep.md"])


def test_unsupported_constructs_are_skipped_with_a_warning() -> None:
    pats, warns = parse_patterns("ok.md\n[ab].md\nesc\\ aped.md\nalso-ok/\n")
    assert pats == ["ok.md", "also-ok/"]
    assert len(warns) == 2 and "line 2" in warns[0] and "line 3" in warns[1]


@pytest.mark.parametrize("path", ["", "/", "./", "."])
def test_root_itself_is_never_ignored(path: str) -> None:
    assert not is_ignored(path, ["*"])


@given(st.text(max_size=200), st.text(max_size=80))
def test_arbitrary_pattern_text_never_raises(text: str, path: str) -> None:
    pats, _ = parse_patterns(text)
    is_ignored(path, pats)
    is_ignored(path, text.splitlines(), is_dir=True)
