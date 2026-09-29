"""Condition C′: a plain grep/read/write file agent, in the style of Claude Code."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from kbio.agent import ToolSpec

MAX_MATCHES = 50
LINE_WINDOW = 400
NUM_PREFIX = re.compile(r"^\(\d+\)\s+")


def window(line: str, start: int, end: int) -> str:
    """A long line (a whole paragraph) is shown as a window around the match, like a grep -o
    with context; short lines are shown whole."""
    if len(line) <= LINE_WINDOW:
        return line
    pad = max((LINE_WINDOW - (end - start)) // 2, 40)
    a, b = max(start - pad, 0), min(end + pad, len(line))
    return ("…" if a else "") + line[a:b] + ("…" if b < len(line) else "")


def _obj(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required}


class FilesHarness:
    name = "files"

    def setup(self, kb_dir: Path, scratch: Path) -> list[ToolSpec]:
        self.root = kb_dir.resolve()
        s = {"type": "string"}
        i = {"type": "integer"}
        return [
            ToolSpec(
                "list_dir",
                "List the files and folders under a knowledge-base folder ('' = the root).",
                _obj({"path": s}, []),
            ),
            ToolSpec(
                "grep",
                "Case-insensitive regex search over every .md file; returns path:line: text "
                f"(at most {MAX_MATCHES} matches). Optional `path` limits it to a folder or file.",
                _obj({"pattern": s, "path": s}, ["pattern"]),
            ),
            ToolSpec(
                "read_file",
                "Read a file's lines (1-based `offset`, default 1; `limit` lines, default 200).",
                _obj({"path": s, "offset": i, "limit": i}, ["path"]),
            ),
            ToolSpec(
                "write_file",
                "Create or overwrite a file with `content`.",
                _obj({"path": s, "content": s}, ["path", "content"]),
            ),
            ToolSpec(
                "edit_file",
                "Replace the exact text `old` (must occur exactly once) with `new` in a file.",
                _obj({"path": s, "old": s, "new": s}, ["path", "old", "new"]),
            ),
        ]

    def _path(self, rel: str) -> Path:
        p = (self.root / rel.strip().lstrip("/")).resolve()
        if p != self.root and self.root not in p.parents:
            raise ValueError("path escapes the knowledge base")
        return self._tolerant(p)

    def _tolerant(self, p: Path) -> Path:
        """M12: models drop a leading "(1) " from folder names ("(1) Universal/Truth.md" is
        typed as "Universal/Truth.md"). A missing path component resolves to the one sibling
        whose name matches once that prefix is stripped; anything else is left as typed."""
        if p.exists():
            return p
        cur = self.root
        for part in p.relative_to(self.root).parts:
            nxt = cur / part
            if not nxt.exists() and cur.is_dir():
                alts = [x for x in cur.iterdir() if NUM_PREFIX.sub("", x.name) == part]
                if len(alts) == 1:
                    nxt = alts[0]
            cur = nxt
        return cur

    def _rel(self, p: Path) -> str:
        return str(p.relative_to(self.root))

    def call(self, name: str, args: dict[str, Any]) -> str:
        if name == "list_dir":
            p = self._path(args.get("path", "") or "")
            if not p.is_dir():
                raise ValueError(f"not a folder: {args.get('path')}")
            items = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
            return "\n".join(
                self._rel(x) + ("/" if x.is_dir() else "")
                for x in items
                if not x.name.startswith(".")
            )
        if name == "grep":
            pat = re.compile(str(args["pattern"]), re.I)
            base = self._path(args.get("path", "") or "")
            files = [base] if base.is_file() else sorted(base.rglob("*.md"))
            out: list[str] = []
            total = 0
            for f in files:
                if any(part.startswith(".") for part in f.relative_to(self.root).parts):
                    continue
                for n, line in enumerate(f.read_text(errors="replace").split("\n"), start=1):
                    m = pat.search(line)
                    if m:
                        total += 1
                        if len(out) < MAX_MATCHES:
                            out.append(f"{self._rel(f)}:{n}: {window(line, m.start(), m.end())}")
            if total > len(out):
                out.append(f"[{total - len(out)} more matches not shown]")
            return "\n".join(out) or "no matches"
        if name == "read_file":
            p = self._path(args["path"])
            lines = p.read_text(errors="replace").split("\n")
            off = max(int(args.get("offset") or 1), 1)
            lim = max(int(args.get("limit") or 200), 1)
            chunk = lines[off - 1 : off - 1 + lim]
            body = "\n".join(f"{off + k}\t{ln}" for k, ln in enumerate(chunk))
            more = len(lines) - (off - 1 + len(chunk))
            return body + (f"\n[{more} more lines]" if more > 0 else "")
        if name == "write_file":
            p = self._path(args["path"])
            if p.suffix != ".md":
                raise ValueError("only .md files can be written")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(str(args["content"]))
            return f"wrote {self._rel(p)} ({len(str(args['content']))} chars)"
        if name == "edit_file":
            p = self._path(args["path"])
            text = p.read_text()
            old, new = str(args["old"]), str(args["new"])
            k = text.count(old)
            if k != 1:
                raise ValueError(f"`old` occurs {k} times; it must occur exactly once")
            p.write_text(text.replace(old, new))
            return f"edited {self._rel(p)}"
        raise ValueError(f"unknown tool {name}")

    def teardown(self) -> None:
        pass
