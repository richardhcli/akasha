"""crud-kb store: documents in SQLite, full-text search with FTS5 + BM25. Stdlib only.

The FTS index is external-content over the view `live` (non-deleted rows), so bodies are stored
once (KILT is 35 GiB) and a `rebuild` never resurrects tombstones. Everything an agent can see is
deterministic: ids come from the source (ingest) or a checked counter (create), scores are
rounded, and there are no timestamps.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

SNIPPET_PAD = 150
READ_LIMIT = 6000  # characters: a page (+ header) stays under the shared 2,000-token cap
LIST_PAGE = 50
MAX_K = 50
WEIGHTS = (2.0, 1.0, 1.0)  # bm25 column weights: title, body, tags
# one-to-one, so folded positions are original positions; soft hyphen -> space
DASHES = str.maketrans({"\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2212": "-", "\u00ad": " "})
WORD = re.compile(r"[^\W_]+")
STOPWORDS = frozenset(
    "a an and are as at be by did do does for from had has have how in is it its of on or that "
    "the their this to was were what when where which who whom whose why will with".split()
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs(
  rid INTEGER PRIMARY KEY,
  id TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  tags TEXT NOT NULL DEFAULT '',
  deleted INTEGER NOT NULL DEFAULT 0
);
CREATE VIEW IF NOT EXISTS live(rid, title, body, tags) AS
  SELECT rid, title, body, tags FROM docs WHERE deleted = 0;
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
  title, body, tags, content='live', content_rowid='rid',
  tokenize='porter unicode61 remove_diacritics 2'
);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class NotFound(ValueError):
    pass


def fold(text: str) -> str:
    """Lower-case with Unicode dashes folded to '-', keeping string length (so positions in the
    folded text are positions in the original)."""
    t = text.translate(DASHES)
    low = t.lower()
    if len(low) == len(t):
        return low
    return "".join(c.lower() if len(c.lower()) == 1 else c for c in t)


def query_words(query: str) -> list[list[str]]:
    """Whitespace-separated words, each as its alphanumeric tokens: '41-C' -> ['41', 'c']."""
    words = []
    for w in fold(query).split():
        toks = WORD.findall(w)
        if toks:
            words.append(toks)
    return words


def _content(words: list[list[str]]) -> list[list[str]]:
    kept = [w for w in words if not (len(w) == 1 and w[0] in STOPWORDS)]
    return kept or words


def fts_query(words: list[list[str]], op: str) -> str:
    """Quoted terms only (never raw text into MATCH); a multi-token word is an adjacent phrase."""
    return f" {op} ".join('"' + " ".join(w) + '"' for w in words)


def _term_regex(w: list[str], stem: bool) -> re.Pattern[str]:
    toks = list(w)
    if stem and len(toks[-1]) > 4:
        toks[-1] = toks[-1][: max(4, len(toks[-1]) - 3)]
    return re.compile(r"(?<![^\W_])" + r"[\W_]{1,3}".join(re.escape(t) for t in toks))


def snippet(body: str, words: list[list[str]], pad: int = SNIPPET_PAD, cap: int = 200) -> str:
    """A +-pad character window around the best match: the position whose window holds the most
    distinct query words (ties: the longer match, then the earliest). No match -> the head."""
    low = fold(body)
    hits: list[tuple[int, int, int]] = []
    for i, w in enumerate(_content(words)):
        found = []
        for stem in (False, True):
            found = [
                (m.start(), m.end(), i)
                for _, m in zip(range(cap), _term_regex(w, stem).finditer(low))
            ]
            if found:
                break
        hits.extend(found)
    if not hits:
        a, b = 0, min(len(body), 2 * pad)
    else:
        # sliding window over hits sorted by start: distinct words within +-pad of each hit
        hits.sort()
        counts: dict[int, int] = {}
        lo = hi = 0
        best_key: tuple[int, int, int] = (-1, 0, 0)
        best = hits[0]
        for s, e, _ in hits:
            while hi < len(hits) and hits[hi][0] <= s + pad:
                counts[hits[hi][2]] = counts.get(hits[hi][2], 0) + 1
                hi += 1
            while hits[lo][0] < s - pad:
                j = hits[lo][2]
                counts[j] -= 1
                if not counts[j]:
                    del counts[j]
                lo += 1
            key = (len(counts), e - s, -s)
            if key > best_key:
                best, best_key = (s, e, 0), key
        a, b = max(0, best[0] - pad), min(len(body), best[1] + pad)
    text = " ".join(body[a:b].split())
    return ("…" if a > 0 else "") + text + ("…" if b < len(body) else "")


class Store:
    def __init__(self, db: Path | str) -> None:
        self.path = Path(db)
        self.con = sqlite3.connect(str(db), isolation_level=None)
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.executescript(SCHEMA)

    def close(self) -> None:
        self.con.close()

    # ---- helpers ----
    def _row(self, doc_id: str) -> tuple[int, str, str, str]:
        r = self.con.execute(
            "SELECT rid, title, body, tags FROM docs WHERE id = ? AND deleted = 0", (str(doc_id),)
        ).fetchone()
        if r is None:
            raise NotFound(f"no document with id {doc_id!r}")
        return r

    def _fts_delete(self, rid: int, title: str, body: str, tags: str) -> None:
        self.con.execute(
            "INSERT INTO fts(fts, rowid, title, body, tags) VALUES('delete', ?, ?, ?, ?)",
            (rid, title, body, tags),
        )

    def _fts_insert(self, rid: int, title: str, body: str, tags: str) -> None:
        self.con.execute(
            "INSERT INTO fts(rowid, title, body, tags) VALUES(?, ?, ?, ?)", (rid, title, body, tags)
        )

    def _mint(self) -> str:
        r = self.con.execute("SELECT value FROM meta WHERE key = 'next_created'").fetchone()
        n = int(r[0]) if r else 1
        while self.con.execute("SELECT 1 FROM docs WHERE id = ?", (f"n{n}",)).fetchone():
            n += 1
        self.con.execute(
            "INSERT INTO meta(key, value) VALUES('next_created', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(n + 1),),
        )
        return f"n{n}"

    # ---- CRUD ----
    def create(self, title: str, body: str, tags: Iterable[str] | None = None) -> str:
        title, body = str(title).strip(), str(body)
        if not title:
            raise ValueError("title must not be empty")
        tag_s = ", ".join(str(t).strip() for t in (tags or []) if str(t).strip())
        with self.con:
            self.con.execute("BEGIN")
            doc_id = self._mint()
            cur = self.con.execute(
                "INSERT INTO docs(id, title, body, tags) VALUES(?, ?, ?, ?)",
                (doc_id, title, body, tag_s),
            )
            assert cur.lastrowid is not None
            self._fts_insert(cur.lastrowid, title, body, tag_s)
        return doc_id

    def read(self, doc_id: str, offset: int = 0, limit: int = READ_LIMIT) -> dict[str, Any]:
        _rid, title, body, tags = self._row(doc_id)
        offset, limit = int(offset), int(limit)
        if offset < 0 or offset > len(body):
            raise ValueError(f"offset {offset} is outside the body (0-{len(body)})")
        if limit < 1:
            raise ValueError("limit must be at least 1")
        end = min(len(body), offset + limit)
        return {
            "id": str(doc_id),
            "title": title,
            "tags": tags,
            "offset": offset,
            "end": end,
            "total": len(body),
            "next_offset": end if end < len(body) else None,
            "body": body[offset:end],
        }

    def update(
        self,
        doc_id: str,
        body: str | None = None,
        old: str | None = None,
        new: str | None = None,
    ) -> None:
        full = body is not None
        patch = old is not None or new is not None
        if full == patch:
            raise ValueError("give either `body` (full replace) or `old` and `new` (patch)")
        with self.con:
            self.con.execute("BEGIN")
            rid, title, cur_body, tags = self._row(doc_id)
            if full:
                new_body = str(body)
            else:
                if old is None or new is None:
                    raise ValueError("a patch needs both `old` and `new`")
                if not old:
                    raise ValueError("`old` must not be empty")
                k = cur_body.count(old)
                if k != 1:
                    raise ValueError(
                        f"`old` occurs {k} times in {doc_id!r}; it must occur exactly once"
                    )
                new_body = cur_body.replace(old, str(new))
            self._fts_delete(rid, title, cur_body, tags)
            self.con.execute("UPDATE docs SET body = ? WHERE rid = ?", (new_body, rid))
            self._fts_insert(rid, title, new_body, tags)

    def delete(self, doc_id: str) -> None:
        with self.con:
            self.con.execute("BEGIN")
            rid, title, body, tags = self._row(doc_id)
            self._fts_delete(rid, title, body, tags)
            self.con.execute("UPDATE docs SET deleted = 1 WHERE rid = ?", (rid,))

    def search(self, query: str, k: int = 10) -> list[dict[str, Any]]:
        """AND over the content words first; if that finds fewer than k, fill up from OR."""
        k = max(1, min(int(k), MAX_K))
        words = query_words(str(query))
        if not words:
            return []
        content = _content(words)
        out: list[dict[str, Any]] = []
        seen: set[int] = set()
        ops = ["AND", "OR"] if len(content) > 1 else ["AND"]
        for op in ops:
            if len(out) >= k:
                break
            rows = self.con.execute(
                "SELECT docs.rid, docs.id, docs.title, docs.body, bm25(fts, ?, ?, ?) AS s "
                "FROM fts JOIN docs ON docs.rid = fts.rowid "
                "WHERE fts MATCH ? AND docs.deleted = 0 ORDER BY s, docs.rid LIMIT ?",
                (*WEIGHTS, fts_query(content, op), k + len(seen)),
            ).fetchall()
            for rid, doc_id, title, body, s in rows:
                if rid in seen or len(out) >= k:
                    continue
                seen.add(rid)
                out.append(
                    {
                        "id": doc_id,
                        "title": title,
                        "score": round(-s, 3),
                        "snippet": snippet(body, words),
                    }
                )
        return out

    def list(
        self, prefix: str = "", cursor: str | None = None, page: int = LIST_PAGE
    ) -> dict[str, Any]:
        """Live documents by (title, id); `cursor` is the opaque value from the previous page."""
        after_t, after_i = "", ""
        if cursor:
            after_t, _, after_i = str(cursor).partition("\x1f")
        rows = self.con.execute(
            "SELECT id, title FROM docs WHERE deleted = 0 AND title LIKE ? ESCAPE '\\' "
            "AND (title > ? OR (title = ? AND id > ?)) ORDER BY title, id LIMIT ?",
            (_like_prefix(prefix), after_t, after_t, after_i, page + 1),
        ).fetchall()
        items = [{"id": i, "title": t} for i, t in rows[:page]]
        nxt = None
        if len(rows) > page:
            nxt = items[-1]["title"] + "\x1f" + items[-1]["id"]
        return {"items": items, "next_cursor": nxt}

    def export(self) -> Iterator[dict[str, Any]]:
        """White-box only: every live document."""
        for doc_id, title, body, tags in self.con.execute(
            "SELECT id, title, body, tags FROM docs WHERE deleted = 0 ORDER BY rid"
        ):
            yield {"id": doc_id, "title": title, "body": body, "tags": tags}

    def integrity_check(self) -> None:
        self.con.execute("INSERT INTO fts(fts, rank) VALUES('integrity-check', 1)")

    # ---- bulk ingest (no LLM) ----
    def ingest(self, sources: Iterable[tuple[Path, Path]]) -> dict[str, Any]:
        """Upsert every record (a re-ingested id is replaced), then rebuild the FTS index once."""
        t0 = time.perf_counter()
        self.con.execute("PRAGMA synchronous=OFF")
        n = bad = 0
        self.con.execute("BEGIN")
        try:
            for root, f in sources:
                for rec in _records(root, f):
                    if rec is None:
                        bad += 1
                        continue
                    self.con.execute(
                        "INSERT INTO docs(id, title, body, tags) VALUES(?, ?, ?, ?) "
                        "ON CONFLICT(id) DO UPDATE SET title = excluded.title, "
                        "body = excluded.body, tags = excluded.tags, deleted = 0",
                        rec,
                    )
                    n += 1
            self.con.execute("INSERT INTO fts(fts) VALUES('rebuild')")
            self.con.execute("INSERT INTO fts(fts) VALUES('optimize')")
            self.con.execute("COMMIT")
        except BaseException:
            self.con.execute("ROLLBACK")
            raise
        finally:
            self.con.execute("PRAGMA synchronous=NORMAL")
        self.con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        live = self.con.execute("SELECT count(*) FROM docs WHERE deleted = 0").fetchone()[0]
        return {
            "records": n,
            "bad": bad,
            "live_docs": live,
            "seconds": round(time.perf_counter() - t0, 2),
            "db_bytes": sum(p.stat().st_size for p in self.path.parent.glob(self.path.name + "*")),
        }


def _like_prefix(prefix: str) -> str:
    return re.sub(r"([%_\\])", r"\\\1", str(prefix or "")) + "%"


def _md_title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip() or fallback
    return fallback


def _records(root: Path, f: Path) -> Iterator[tuple[str, str, str, str] | None]:
    """(id, title, body, tags) per document. `.md`: id = path relative to the ingest root without
    `.md`. `.jsonl`: `id`/`wikipedia_id`/`_id`, `title`/`wikipedia_title`, `body`/`text` (a list of
    paragraphs is joined with newlines, as in KILT), optional `tags`."""
    if f.suffix == ".md":
        text = f.read_text(encoding="utf-8", errors="replace")
        rel = f.relative_to(root).with_suffix("").as_posix()
        yield rel, _md_title(text, f.stem), text, ""
        return
    with f.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                yield None
                continue
            doc_id = r.get("id", r.get("wikipedia_id", r.get("_id")))
            body = r.get("body", r.get("text"))
            if doc_id is None or body is None:
                yield None
                continue
            if isinstance(body, list):
                body = "\n".join(str(p).rstrip("\n") for p in body)
            title = str(r.get("title", r.get("wikipedia_title", "")) or doc_id)
            tags = r.get("tags") or []
            tag_s = ", ".join(tags) if isinstance(tags, list) else str(tags)
            yield str(doc_id), title, str(body), tag_s


def ingest_sources(target: Path) -> list[tuple[Path, Path]]:
    """Every non-hidden `*.md` / `*.jsonl` under a directory (sorted), or one file."""
    target = Path(target)
    if target.is_file():
        return [(target.parent, target)]
    files = sorted(
        p
        for p in target.rglob("*")
        if p.suffix in (".md", ".jsonl")
        and p.is_file()
        and not any(part.startswith(".") for part in p.relative_to(target).parts)
    )
    return [(target, p) for p in files]
