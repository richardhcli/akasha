"""Condition A: the akasha harness, a client of the scratch daemon (HOME=scratch, port 7534).

Reads go through the daemon's HTTP API (`/v1/search`, `/v1/nodes/{id}`, `/neighborhood`); node
locations (which files and headings hold a node) come from the harness's own scan of the vault.
Writes are **file-based**: the harness edits the markdown files and the daemon's watcher ingests
them, which is the real user path (agent-class tokens would turn API mutations into review
proposals, spec §4.11). `edit_node` changes one copy and waits for the daemon to propagate it to
every mirror; `write_atom` mints a checksummed id; `transclude` copies a node byte-identically."""

from __future__ import annotations

import random
import re
import time
from pathlib import Path
from typing import Any

from kbio import akasha_ctl as ak
from kbio.agent import ToolSpec

A = "abcdefghijklmnopqrstuvwxyz234567"
LINE_ATOM_RE = re.compile(r" \^tm-([a-z2-7]{8})[ \t]*$", re.M)
SPAN_END_RE = re.compile(r"\}\{tm-([a-z2-7]{8})\}")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
WIKILINK_RE = re.compile(r"(?<!!)\[\[([^\]|#]*)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
MAX_RESULTS = 10


def mint_id(rng: random.Random) -> str:
    core = "".join(rng.choice(A) for _ in range(7))
    return core + A[sum((i + 1) * A.index(c) for i, c in enumerate(core)) % 32]


def snippet(body: str, terms: list[str], width: int = 200) -> str:
    """200 characters of a node: its start, or a window around the first query term when the
    term would otherwise be cut off (long paragraph nodes)."""
    if len(body) <= width:
        return body
    low = body.lower()
    hits = [i for t in terms if (i := low.find(t)) >= 0]
    start = 0
    if hits and min(hits) > width - 40:
        start = max(min(hits) - width // 3, 0)
    end = min(start + width, len(body))
    return ("…" if start else "") + body[start:end] + ("…" if end < len(body) else "")


def _obj(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required}


def find_atom(text: str, nid: str) -> tuple[int, int, str, str] | None:
    """(start, end, inner text, kind) of a node's first occurrence in a file's text."""
    m = re.search(rf"^(.*?) \^tm-{nid}[ \t]*$", text, re.M)
    if m:
        return m.start(1), m.end(1), m.group(1), "line"
    idx = text.find("}{tm-" + nid + "}")
    if idx < 0:
        return None
    depth, k = 1, idx - 1
    while k >= 0:
        depth += (text[k] == "}") - (text[k] == "{")
        if depth == 0:
            return k + 1, idx, text[k + 1 : idx], "span"
        k -= 1
    return None


def balanced(s: str) -> bool:
    depth = 0
    for ch in s:
        depth += (ch == "{") - (ch == "}")
        if depth < 0:
            return False
    return depth == 0


class AkashaHarness:
    name = "akasha"

    def __init__(
        self, write_tools: bool = True, propagate_timeout: float = 15.0, id_seed: str = ""
    ) -> None:
        self.write_tools = write_tools
        self.propagate_timeout = propagate_timeout
        # ids minted from (seed, counter): a rerun of the same task mints the same ids, so its
        # cached model turns replay
        self._rng = random.Random(f"kbio-mint:{id_seed}")

    # ---- setup / index ----
    def setup(self, kb_dir: Path, scratch: Path) -> list[ToolSpec]:
        self.root = kb_dir.resolve()
        self._index: dict[str, list[tuple[str, int, str]]] = {}
        self._mtimes: dict[str, float] = {}
        self._file_ids: dict[str, list[str]] = {}
        self.refresh()
        s, i = {"type": "string"}, {"type": "integer"}
        tools = [
            ToolSpec(
                "search",
                "Full-text search over knowledge-base nodes (atomic blocks of text). Every "
                "word must match, so use 1-4 distinctive keywords. Returns node ids, the first "
                "200 characters, and the notes/headings where each node appears.",
                _obj({"query": s}, ["query"]),
            ),
            ToolSpec(
                "get_node",
                "Full text of a node by id, plus every note it appears in (a node copied into "
                "several notes is one node: the copies are kept identical).",
                _obj({"id": s}, ["id"]),
            ),
            ToolSpec(
                "neighborhood",
                "Context around a node: its heading and neighbouring nodes in each note it "
                "appears in, the notes it links to, and graph edges.",
                _obj({"id": s, "hops": i}, ["id"]),
            ),
            ToolSpec(
                "read_note",
                "Read a whole note by path (e.g. 'Wikipedia/Habit.md'), node anchors included.",
                _obj({"path": s}, ["path"]),
            ),
        ]
        if self.write_tools:
            tools += [
                ToolSpec(
                    "write_atom",
                    "Add a new node (one fact) to a note, optionally under an existing heading "
                    "(created at the end of the note if missing). Creates the note if it does "
                    "not exist. The id is minted for you.",
                    _obj({"note": s, "text": s, "after_heading": s}, ["note", "text"]),
                ),
                ToolSpec(
                    "transclude",
                    "Place an existing node into another note as a live copy with the same id "
                    "(edits to either copy reach both). Optionally under a heading.",
                    _obj({"id": s, "into_note": s, "after_heading": s}, ["id", "into_note"]),
                ),
                ToolSpec(
                    "edit_node",
                    "Replace a node's text. Edit once: every note that holds a copy of the node "
                    "is updated automatically.",
                    _obj({"id": s, "new_text": s}, ["id", "new_text"]),
                ),
            ]
        return tools

    def _path(self, rel: str) -> Path:
        rel = rel.strip().lstrip("/")
        if rel and not rel.endswith(".md"):
            rel += ".md"
        p = (self.root / rel).resolve()
        if self.root not in p.parents:
            raise ValueError("path escapes the knowledge base")
        return p

    def refresh(self) -> None:
        """Rescan changed files into the id -> [(file, line, heading path)] index."""
        seen = set()
        for p in self.root.rglob("*.md"):
            rel = str(p.relative_to(self.root))
            if any(part.startswith(".") for part in Path(rel).parts):
                continue
            seen.add(rel)
            mt = p.stat().st_mtime_ns
            if self._mtimes.get(rel) == mt:
                continue
            self._mtimes[rel] = mt
            self._drop(rel)
            ids: list[str] = []
            path: list[tuple[int, str]] = []
            for n, line in enumerate(p.read_text(errors="replace").split("\n"), start=1):
                h = HEADING_RE.match(line)
                if h:
                    lvl = len(h.group(1))
                    path = [x for x in path if x[0] < lvl] + [(lvl, h.group(2).strip())]
                    continue
                for m in list(LINE_ATOM_RE.finditer(line)) + list(SPAN_END_RE.finditer(line)):
                    nid = m.group(1)
                    ids.append(nid)
                    where = " › ".join(x[1] for x in path)
                    self._index.setdefault(nid, []).append((rel, n, where))
            self._file_ids[rel] = ids
        for rel in list(self._file_ids):
            if rel not in seen:
                self._drop(rel)
                self._mtimes.pop(rel, None)

    def _drop(self, rel: str) -> None:
        for nid in self._file_ids.pop(rel, []):
            self._index[nid] = [x for x in self._index.get(nid, []) if x[0] != rel]

    def locations(self, nid: str) -> list[tuple[str, int, str]]:
        self.refresh()
        return self._index.get(nid, [])

    def _loc_str(self, nid: str) -> str:
        locs = self.locations(nid)
        return "; ".join(f"{f}" + (f" › {h}" if h else "") for f, _n, h in locs) or "(no file)"

    def _node(self, nid: str) -> dict[str, Any]:
        return ak.api("GET", f"/nodes/{nid.removeprefix('tm-').removeprefix('^tm-')}")

    # ---- tools ----
    def call(self, name: str, args: dict[str, Any]) -> str:
        fn = getattr(self, f"t_{name}", None)
        if fn is None:
            raise ValueError(f"unknown tool {name}")
        if name in ("write_atom", "transclude", "edit_node") and not self.write_tools:
            raise ValueError(f"unknown tool {name}")
        return fn(**args)

    def t_search(self, query: str) -> str:
        res = ak.api("GET", "/search", params={"q": query})["results"]
        if not res:  # AND found nothing: rank by how many of the words each node matches
            words = [w for w in re.findall(r"[A-Za-z0-9]+", query) if len(w) > 2][:6]
            hits: dict[str, tuple[int, dict]] = {}
            for w in words:
                for r in ak.api("GET", "/search", params={"q": w})["results"][:50]:
                    k, _ = hits.get(r["id"], (0, r))
                    hits[r["id"]] = (k + 1, r)
            res = [
                r
                for k, r in sorted(hits.values(), key=lambda x: -x[0])
                if k >= 2 or len(words) == 1
            ]
            if res:
                res = res[:MAX_RESULTS]
                header = "no node matches every word; best partial matches:\n"
            else:
                return "no results"
        else:
            header = f"{len(res)} results" + (
                f", top {MAX_RESULTS}" if len(res) > MAX_RESULTS else ""
            )
            header += ":\n"
        lines = []
        terms = [w.lower() for w in re.findall(r"[A-Za-z0-9]+", query) if len(w) > 1]
        for r in res[:MAX_RESULTS]:
            body = " ".join(r["body"].split())
            lines.append(f"- {r['id']}: {snippet(body, terms)}\n  in: {self._loc_str(r['id'])}")
        return header + "\n".join(lines)

    def t_get_node(self, id: str) -> str:  # noqa: A002
        n = self._node(id)
        where = self._loc_str(n["id"])
        return f"node {n['id']} ({n['status']}):\n{n['body'].rstrip()}\n\nappears in: {where}"

    def t_neighborhood(self, id: str, hops: int = 1) -> str:  # noqa: A002
        nid = self._node(id)["id"]
        out = []
        for rel, line_no, heading in self.locations(nid):
            ids = self._file_ids.get(rel, [])
            k = ids.index(nid) if nid in ids else -1
            near = [x for x in ids[max(k - 2, 0) : k + 3] if x != nid] if k >= 0 else []
            out.append(f"in {rel}" + (f" › {heading}" if heading else "") + ":")
            text = (self.root / rel).read_text(errors="replace")
            for x in near:
                a = find_atom(text, x)
                if a:
                    out.append(f"  - {x}: {' '.join(a[2].split())[:160]}")
        body = self._node(nid)["body"]
        links = sorted({m.group(1).strip() for m in WIKILINK_RE.finditer(body)})
        if links:
            out.append("links to notes: " + ", ".join(links))
        nb = ak.api("GET", f"/nodes/{nid}/neighborhood", params={"hops": max(1, min(hops, 2))})
        if nb["edges"]:
            edges = [f"{e['src']} -> {e['dst']}" for e in nb["edges"][:20]]
            out.append("edges: " + "; ".join(edges))
        return "\n".join(out) or "no neighbourhood"

    def t_read_note(self, path: str) -> str:
        p = self._path(path)
        if not p.exists():
            raise ValueError(f"no such note: {path}")
        return p.read_text(errors="replace")

    # ---- writes (file-based; the daemon ingests them) ----
    def _insert(self, note: str, block: str, after_heading: str | None) -> Path:
        p = self._path(note)
        if p.exists():
            text = p.read_text()
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            text = f"# {p.stem}\n"
        lines = text.rstrip("\n").split("\n")
        if after_heading:
            want = after_heading.strip().lstrip("#").strip().lower()
            hi = next(
                (i for i, ln in enumerate(lines)
                 if (m := HEADING_RE.match(ln)) and m.group(2).strip().lower() == want),
                None,
            )  # fmt: skip
            if hi is None:
                lines += ["", f"## {after_heading.strip().lstrip('#').strip()}", "", block]
            else:
                lvl = len(HEADING_RE.match(lines[hi]).group(1))  # type: ignore[union-attr]
                end = next(
                    (j for j in range(hi + 1, len(lines))
                     if (m := HEADING_RE.match(lines[j])) and len(m.group(1)) <= lvl),
                    len(lines),
                )  # fmt: skip
                while end > hi + 1 and not lines[end - 1].strip():
                    end -= 1
                lines[end:end] = ["", block]
        else:
            lines += ["", block]
        p.write_text("\n".join(lines) + "\n")
        return p

    def _wait_node(self, nid: str, timeout: float) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                self._node(nid)
                return True
            except Exception:
                time.sleep(0.3)
        return False

    def t_write_atom(self, note: str, text: str, after_heading: str | None = None) -> str:
        text = text.strip("\n")
        if not text.strip():
            raise ValueError("empty text")
        nid = mint_id(self._rng)
        if "\n" in text or text[0] in " \t":
            if not balanced(text):
                raise ValueError("text with unbalanced braces cannot be a multi-line node")
            block = "{" + text + "}{tm-" + nid + "}"
        else:
            block = f"{text} ^tm-{nid}"
        p = self._insert(note, block, after_heading)
        ok = self._wait_node(nid, self.propagate_timeout)
        rel = p.relative_to(self.root)
        return f"created node {nid} in {rel}" + ("" if ok else " (not yet indexed by the daemon)")

    def t_transclude(self, id: str, into_note: str, after_heading: str | None = None) -> str:  # noqa: A002
        nid = self._node(id)["id"]
        locs = self.locations(nid)
        if not locs:
            raise ValueError(f"node {nid} is not in any note")
        src = (self.root / locs[0][0]).read_text()
        a = find_atom(src, nid)
        assert a is not None
        start, end, inner, kind = a
        dest = self._path(into_note)
        if dest.exists() and nid in (self._file_ids.get(str(dest.relative_to(self.root))) or []):
            return f"node {nid} is already in {dest.relative_to(self.root)}"
        block = f"{inner} ^tm-{nid}" if kind == "line" else "{" + inner + "}{tm-" + nid + "}"
        self._insert(into_note, block, after_heading)
        rel = dest.relative_to(self.root)
        return f"transcluded node {nid} into {rel} (now in {len(locs) + 1} notes)"

    def t_edit_node(self, id: str, new_text: str) -> str:  # noqa: A002
        nid = self._node(id)["id"]
        locs = self.locations(nid)
        if not locs:
            raise ValueError(f"node {nid} is not in any note")
        rel = locs[0][0]
        p = self.root / rel
        text = p.read_text()
        a = find_atom(text, nid)
        assert a is not None
        start, end, inner, kind = a
        new_text = new_text.strip("\n")
        if kind == "line" and "\n" in new_text:
            raise ValueError("this node is a single line; new_text must be one line")
        if kind == "span" and not balanced(new_text):
            raise ValueError("unbalanced braces")
        p.write_text(text[:start] + new_text + text[end:])
        others = [f for f, _n, _h in locs if f != rel]
        deadline = time.monotonic() + self.propagate_timeout
        done: set[str] = set()
        while time.monotonic() < deadline:
            for f in others:
                if f not in done:
                    b = find_atom((self.root / f).read_text(), nid)
                    if b and b[2] == new_text:
                        done.add(f)
            if len(done) == len(others):
                break
            time.sleep(0.3)
        return (
            f"updated node {nid} in {rel}; copies updated in {len(done)}/{len(others)} other "
            f"notes" + (": " + ", ".join(sorted(done)) if done else "")
        )

    def teardown(self) -> None:
        pass
