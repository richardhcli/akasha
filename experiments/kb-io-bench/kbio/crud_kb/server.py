"""crud-kb MCP server over stdio: newline-delimited JSON-RPC 2.0, hand-rolled on the stdlib.

Implements the handshake (`initialize` echoes the client's protocol version), `ping`,
`tools/list` and `tools/call`. Tool failures are results with `isError: true`, so the agent sees
the message; protocol errors are JSON-RPC errors. stdout carries protocol frames only (UTF-8
bytes, flushed per line); diagnostics go to stderr.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any, BinaryIO

from kbio.crud_kb.store import LIST_PAGE, MAX_K, READ_LIMIT, NotFound, Store

MAX_RESULT_CHARS = 8000
S = {"type": "string"}
INT = {"type": "integer"}


def _obj(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required}


TOOLS: dict[str, dict[str, Any]] = {
    "search": {
        "description": "Full-text search (BM25) over every document. Returns up to `k` hits "
        f"(default 10, max {MAX_K}) as [{{id, title, score, snippet}}]; the snippet is a "
        "window of about 300 characters around the best match.",
        "inputSchema": _obj({"query": S, "k": INT}, ["query"]),
    },
    "read": {
        "description": f"Read a document's body in pages of `limit` characters (default "
        f"{READ_LIMIT}) from `offset` (default 0). The header gives `next_offset` if more "
        "remains.",
        "inputSchema": _obj({"id": S, "offset": INT, "limit": INT}, ["id"]),
    },
    "list": {
        "description": f"List documents (id and title) ordered by title, {LIST_PAGE} per page. "
        "Optional title `prefix`; pass the returned `next_cursor` as `cursor` for the next page.",
        "inputSchema": _obj({"prefix": S, "cursor": S}, []),
    },
    "create": {
        "description": "Create a document. Returns its `id`.",
        "inputSchema": _obj(
            {"title": S, "body": S, "tags": {"type": "array", "items": S}}, ["title", "body"]
        ),
    },
    "update": {
        "description": "Change a document's body: either `body` (replaces the whole body), or "
        "`old` and `new` (replaces the exact text `old`, which must occur exactly once).",
        "inputSchema": _obj({"id": S, "body": S, "old": S, "new": S}, ["id"]),
    },
    "delete": {
        "description": "Delete a document. It disappears from search, read and list.",
        "inputSchema": _obj({"id": S}, ["id"]),
    },
}
EXPORT_TOOL = {
    "description": "White-box only: every live document as JSON lines.",
    "inputSchema": _obj({}, []),
}


PIECE = re.compile(r"[A-Za-z]{1,8}|\d{1,3}|[^\sA-Za-z\d]")
PIECE_RATIO = 1.35  # measured cl100k tokens per piece: KILT pages mean 1.09, worst 1.32
TOKEN_BUDGET = 1900  # under the agent loop's 2,000-token result cap


def approx_tokens(text: str) -> int:
    """Stdlib, conservative estimate of cl100k tokens: pieces (letter runs of <= 8, 3-digit
    groups, single symbols or CJK characters) times PIECE_RATIO, or chars/3.2 if larger."""
    return int(max(len(PIECE.findall(text)) * PIECE_RATIO, len(text) / 3.2)) + 1


def cap(text: str, limit: int = MAX_RESULT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[... truncated: {len(text) - limit} more characters]"


class Server:
    def __init__(
        self,
        store: Store,
        white_box: bool = False,
        max_chars: int = MAX_RESULT_CHARS,
        token_budget: int = TOKEN_BUDGET,
    ):
        self.store = store
        self.max_chars = max_chars
        self.token_budget = token_budget
        self.tools = dict(TOOLS)
        if white_box:
            self.tools["export"] = EXPORT_TOOL

    # ---- tools ----
    def call_tool(self, name: str, args: dict[str, Any]) -> str:
        st = self.store
        if name not in self.tools:
            raise ValueError(f"unknown tool {name!r}")
        if name == "search":
            hits = st.search(_req(args, "query"), int(args.get("k") or 10))
            while hits and len(json.dumps(hits, ensure_ascii=False)) > self.max_chars:
                hits = hits[:-1]  # drop whole hits rather than emit broken JSON
            return json.dumps(hits, ensure_ascii=False)
        if name == "read":
            doc_id, off = _req(args, "id"), int(args.get("offset") or 0)
            limit = min(int(args.get("limit") or READ_LIMIT), self.max_chars)
            while True:
                r = st.read(doc_id, off, limit)
                nxt = f"next_offset: {r['next_offset']}" if r["next_offset"] is not None else "end"
                head = [f"id: {r['id']}", f"title: {r['title']}"]
                if r["tags"]:
                    head.append(f"tags: {r['tags']}")
                head.append(f"characters {r['offset']}-{r['end']} of {r['total']} ({nxt})")
                out = "\n".join(head) + "\n---\n" + r["body"]
                est = approx_tokens(out)
                if (len(out) <= self.max_chars and est <= self.token_budget) or limit <= 1:
                    return out
                # shrink the page until it fits: a cap must never cut a page, because the cut
                # text would never be shown (next_offset skips past it)
                by_chars = limit - (len(out) - self.max_chars)
                by_tokens = int(limit * self.token_budget / est) - 8
                limit = max(1, min(limit - 1, by_chars, by_tokens))
        if name == "list":
            return cap(
                json.dumps(
                    st.list(args.get("prefix") or "", args.get("cursor")), ensure_ascii=False
                ),
                self.max_chars,
            )
        if name == "create":
            tags = args.get("tags") or []
            if isinstance(tags, str):
                tags = tags.split(",")
            return json.dumps({"id": st.create(_req(args, "title"), _req(args, "body"), tags)})
        if name == "update":
            doc_id = _req(args, "id")
            st.update(doc_id, args.get("body"), args.get("old"), args.get("new"))
            return json.dumps({"id": doc_id, "updated": True})
        if name == "delete":
            doc_id = _req(args, "id")
            st.delete(doc_id)
            return json.dumps({"id": doc_id, "deleted": True})
        if name == "export":
            return "\n".join(json.dumps(d, ensure_ascii=False) for d in st.export())
        raise ValueError(f"unknown tool {name!r}")

    # ---- JSON-RPC ----
    def handle(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        method, mid = msg.get("method"), msg.get("id")
        if mid is None:
            return None  # a notification (initialized, cancelled, ...): never answered
        params = msg.get("params") or {}
        if method == "initialize":
            result: dict[str, Any] = {
                "protocolVersion": params.get("protocolVersion") or "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "crud-kb", "version": "0.1.0"},
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": [{"name": n, **spec} for n, spec in self.tools.items()]}
        elif method == "tools/call":
            name = params.get("name", "")
            try:
                text, err = (
                    cap(self.call_tool(name, params.get("arguments") or {}), self.max_chars),
                    False,
                )
            except (ValueError, NotFound, TypeError, KeyError) as e:
                text, err = f"{type(e).__name__}: {e}", True
            result = {"content": [{"type": "text", "text": text}], "isError": err}
        else:
            return {
                "jsonrpc": "2.0",
                "id": mid,
                "error": {"code": -32601, "message": f"method not found: {method}"},
            }
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def serve(self, inp: BinaryIO, out: BinaryIO) -> None:
        for raw in inp:
            if not raw.strip():
                continue
            try:
                msg = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                reply: dict[str, Any] | None = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"parse error: {e}"},
                }
            else:
                if not isinstance(msg, dict):
                    continue  # batches are not used by MCP clients
                reply = self.handle(msg)
            if reply is not None:
                out.write(json.dumps(reply, ensure_ascii=False).encode("utf-8") + b"\n")
                out.flush()


def _req(args: dict[str, Any], key: str) -> str:
    v = args.get(key)
    if v is None or (isinstance(v, str) and not v.strip()):
        raise ValueError(f"missing required argument `{key}`")
    return str(v)


def main(db: str, white_box: bool = False, max_chars: int = MAX_RESULT_CHARS) -> None:
    store = Store(db)
    print(f"crud-kb: serving {db} (white_box={white_box})", file=sys.stderr, flush=True)
    try:
        Server(store, white_box, max_chars).serve(sys.stdin.buffer, sys.stdout.buffer)
    finally:
        store.close()
