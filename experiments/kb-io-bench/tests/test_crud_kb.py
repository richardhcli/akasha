"""crud-kb (V1): store semantics, query sanitising, snippet window, pagination, caps, ingest, and an
MCP stdio round trip driven by the v1 agent loop with a fake model (no network, no scratch HOME)."""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from kbio.agent import ToolSpec, run_agent
from kbio.crud_kb.server import Server
from kbio.crud_kb.store import READ_LIMIT, NotFound, Store, ingest_sources, snippet

EXP = Path(__file__).resolve().parents[1]


@pytest.fixture
def st(tmp_path: Path) -> Store:
    s = Store(tmp_path / "kb.db")
    yield s  # type: ignore[misc]
    s.close()


# ---------------------------------------------------------------- CRUD + tombstones
def test_create_read_update_delete(st: Store) -> None:
    a = st.create("Habit", "A habit is a routine. The Marlow entry is 41-C.", ["psych"])
    b = st.create("Habit", "Another note.")
    assert (a, b) == ("n1", "n2")  # deterministic, sequential
    r = st.read(a)
    assert r["title"] == "Habit" and r["tags"] == "psych" and r["next_offset"] is None
    st.update(a, old="41-C", new="17-B")
    assert "17-B" in st.read(a)["body"] and "41-C" not in st.read(a)["body"]
    assert [h["id"] for h in st.search("17-B")] == [a]
    assert st.search("41-C") == []
    st.update(a, body="Replaced entirely.")
    assert st.read(a)["body"] == "Replaced entirely."
    st.integrity_check()
    st.delete(a)
    st.integrity_check()
    with pytest.raises(NotFound) as e1:
        st.read(a)
    with pytest.raises(NotFound) as e2:
        st.read("nope")
    assert str(e1.value).replace(a, "X") == str(e2.value).replace("nope", "X")  # no deletion leak
    assert st.search("Replaced") == []
    assert [i["id"] for i in st.list()["items"]] == [b]
    assert [d["id"] for d in st.export()] == [b]
    with pytest.raises(NotFound):
        st.update(a, body="zombie")
    with pytest.raises(NotFound):
        st.delete(a)
    assert st.create("x", "y") == "n3"  # ids are never reused


def test_update_argument_rules(st: Store) -> None:
    a = st.create("T", "one two one")
    with pytest.raises(ValueError, match="either"):
        st.update(a)
    with pytest.raises(ValueError, match="either"):
        st.update(a, body="x", old="one", new="1")
    with pytest.raises(ValueError, match="occurs 2 times"):
        st.update(a, old="one", new="1")
    with pytest.raises(ValueError, match="occurs 0 times"):
        st.update(a, old="three", new="3")
    with pytest.raises(ValueError, match="both"):
        st.update(a, old="two")
    st.update(a, old="two", new="2")
    assert st.read(a)["body"] == "one 2 one"
    st.integrity_check()


def test_minted_id_skips_ingested_collision(tmp_path: Path, st: Store) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "n1.md").write_text("# Taken\n\nbody\n")
    st.ingest(ingest_sources(src))
    assert st.create("t", "b") == "n2"


# ---------------------------------------------------------------- search sanitising
@pytest.mark.parametrize(
    "query",
    [
        "55-Q",
        "97\u2011W",
        "AND",
        "NEAR(",
        "*",
        "-",
        "Marlow's",
        '"',
        "a OR",
        "!!! ???",
        "",
        "NOT x",
    ],
)
def test_search_never_raises(st: Store, query: str) -> None:
    st.create("Codes", "Entry 55-Q and code 97-W. Marlow's index. NOT a NEAR miss AND more.")
    hits = st.search(query)
    assert isinstance(hits, list)
    if query in ("", "!!! ???", "-", "*", '"'):
        assert hits == []


def test_hyphenated_codes_are_phrases(st: Store) -> None:
    hit = st.create("Hit", "It is catalogued as entry 55-Q in the index.")
    st.create("Miss", "Entry 55 of volume Q is unrelated.")
    assert [h["id"] for h in st.search("55-Q")] == [hit]
    assert [h["id"] for h in st.search("55\u2011Q")] == [hit]  # gpt-oss writes U+2011


def test_and_first_then_or_fill(st: Store) -> None:
    both = st.create("Both", "the lexicon society met in pellamfield")
    one = st.create("One", "pellamfield is a town")
    st.create("None", "unrelated text")
    ids = [h["id"] for h in st.search("Where did the Lexicon Society meet in Pellamfield?", k=5)]
    assert ids == [both, one]  # AND hit first, OR fills the rest, no duplicates
    assert [h["id"] for h in st.search("Where did the Lexicon Society meet?")] == [both]
    assert [h["id"] for h in st.search("lexicon society", k=1)] == [both]
    assert all(h["score"] == round(h["score"], 3) for h in st.search("pellamfield"))


def test_porter_stemming(st: Store) -> None:
    a = st.create("Habits", "Forming a habit takes weeks.")
    hits = st.search("habits forming")
    assert hits and hits[0]["id"] == a
    assert "habit" in hits[0]["snippet"]


def test_k_is_capped(st: Store) -> None:
    for i in range(60):
        st.create(f"d{i}", "common word")
    assert len(st.search("common", k=500)) == 50
    assert len(st.search("common")) == 10


# ---------------------------------------------------------------- snippet window
def test_snippet_window_long_paragraph_match_at_end() -> None:
    body = ("filler text " * 900) + "the planted value is 97\u2011W here." + (" tail" * 100)
    assert len(body) > 10_000 and "\n" not in body
    words = [["planted"], ["97", "w"]]
    snip = snippet(body, words)
    assert "97\u2011W" in snip and snip.startswith("…") and snip.endswith("…")
    assert len(snip) <= 150 * 2 + len("97\u2011W") + 2 + 20


def test_snippet_prefers_window_with_most_terms_and_earliest_tie() -> None:
    body = (
        "alpha "
        + "x " * 400
        + "beta "
        + "y " * 400
        + "alpha beta gamma "
        + "z " * 400
        + "alpha beta"
    )
    snip = snippet(body, [["alpha"], ["beta"], ["gamma"]])
    assert "alpha beta gamma" in snip
    tie = "aaa one " + "q " * 300 + "aaa one"
    s2 = snippet(tie, [["aaa"], ["one"]])
    assert s2.startswith("aaa one")  # earliest of two equal windows, no leading ellipsis


def test_snippet_no_body_match_gives_head() -> None:
    assert snippet("short body", [["zzz"]]) == "short body"


def test_snippet_skips_stopwords() -> None:
    body = "the " * 200 + "Straughan founded it."
    assert "Straughan" in snippet(body, [["the"], ["straughan"]])


# ---------------------------------------------------------------- pagination + caps
def test_read_pages_concatenate(st: Store) -> None:
    body = "".join(f"line {i}\n" for i in range(3000))
    a = st.create("Long", body)
    got, off, pages = "", 0, 0
    while off is not None:
        r = st.read(a, off, 5000)
        got += r["body"]
        off = r["next_offset"]
        pages += 1
    assert got == body and pages == -(-len(body) // 5000)
    assert st.read(a)["end"] == READ_LIMIT
    with pytest.raises(ValueError):
        st.read(a, len(body) + 1)


def test_list_pages_no_gaps_or_dupes(st: Store) -> None:
    ids = [st.create(f"T{i % 7}", "b") for i in range(123)]  # duplicate titles
    st.delete(ids[5])
    seen, cur = [], None
    while True:
        page = st.list(cursor=cur)
        seen += [i["id"] for i in page["items"]]
        cur = page["next_cursor"]
        if cur is None:
            break
    assert sorted(seen) == sorted(set(ids) - {ids[5]}) and len(seen) == len(set(seen))
    assert {i["title"] for i in st.list(prefix="T3")["items"]} == {"T3"}
    assert st.list(prefix="%")["items"] == []  # LIKE wildcards are literal


def test_server_caps_results(st: Store) -> None:
    a = st.create("Big", "word " * 5000)
    srv = Server(st, max_chars=1000)
    r = srv.call_tool("read", {"id": a, "limit": 50_000})
    assert len(r) <= 1000 and "[... truncated" not in r and "of 25000 (next_offset: " in r
    for i in range(20):
        st.create(f"Hit {i}", "needle " + "pad " * 200)
    out = srv.call_tool("search", {"query": "needle", "k": 20})
    assert len(out) <= 1000 and isinstance(json.loads(out), list)  # whole hits dropped, valid JSON
    reply = srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "list", "arguments": {}},
        }
    )
    assert reply is not None and len(reply["result"]["content"][0]["text"]) < 1100


def test_jsonrpc_protocol(st: Store) -> None:
    srv = Server(st)
    init = srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        }
    )
    assert init is not None and init["result"]["protocolVersion"] == "2025-06-18"
    assert srv.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert srv.handle({"jsonrpc": "2.0", "method": "notifications/cancelled"}) is None
    err = srv.handle({"jsonrpc": "2.0", "id": 2, "method": "resources/list"})
    assert err is not None and err["error"]["code"] == -32601
    names = [
        t["name"]
        for t in srv.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})["result"]["tools"]
    ]  # type: ignore[index]
    assert "export" not in names and set(names) == {
        "search",
        "read",
        "list",
        "create",
        "update",
        "delete",
    }
    assert "export" in [
        t["name"]
        for t in Server(st, white_box=True).handle(
            {"jsonrpc": "2.0", "id": 4, "method": "tools/list"}
        )["result"]["tools"]
    ]  # type: ignore[index]
    bad = srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "export", "arguments": {}},
        }
    )
    assert bad is not None and bad["result"]["isError"] is True
    miss = srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "read", "arguments": {}},
        }
    )
    assert (
        miss is not None
        and "missing required argument `id`" in miss["result"]["content"][0]["text"]
    )


# ---------------------------------------------------------------- ingest CLI
def test_ingest_md_and_kilt_jsonl(tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / "Wikipedia").mkdir(parents=True)
    (src / ".hidden").mkdir()
    (src / "Wikipedia" / "Habit.md").write_text("# Habit\n\nA habit is a routine.\n")
    (src / "loose.md").write_text("no heading here\n")
    (src / ".hidden" / "x.md").write_text("# hidden\n")
    kilt = [
        {
            "wikipedia_id": "290",
            "wikipedia_title": "A",
            "text": ["A\n", "A is the first letter.\n"],
        },
        {"id": "p2", "title": "Two", "body": "second", "tags": ["t"]},
        {"no_id": True},
    ]
    (src / "kilt.jsonl").write_text("\n".join(json.dumps(r) for r in kilt) + "\nnot json\n")
    db = tmp_path / "kb.db"
    out = subprocess.run(
        [sys.executable, "-m", "kbio.crud_kb", "ingest", str(src), "--db", str(db)],
        cwd=EXP,
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": os.environ["PATH"]},
    )
    stats = json.loads(out.stdout)
    assert stats["records"] == 4 and stats["bad"] == 2 and stats["live_docs"] == 4
    assert stats["db_bytes"] > 0 and stats["seconds"] >= 0
    st = Store(db)
    try:
        st.integrity_check()
        assert st.read("Wikipedia/Habit")["title"] == "Habit"
        assert st.read("loose")["title"] == "loose"
        assert st.read("290")["body"] == "A\nA is the first letter."
        assert st.search("first letter")[0]["id"] == "290"
        st.delete("p2")
        st.ingest(ingest_sources(src / "Wikipedia"))  # re-ingest: upsert, tombstones stay out
        st.integrity_check()
        assert st.search("second") == []
        assert st.read("Habit")["body"].startswith("# Habit")  # ids relative to the ingest root
    finally:
        st.close()


# ---------------------------------------------------------------- MCP round trip via run_agent
class McpStdioHarness:
    """Minimal MCP-stdio harness for the test (the V2 adapter will generalise this)."""

    name = "crud-kb"

    def __init__(self, db: Path, errlog: Path) -> None:
        self.db, self.errlog_path = db, errlog

    def _run(self, coro: Any, timeout: float = 60.0) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    def setup(self, kb_dir: Path, scratch: Path) -> list[ToolSpec]:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "kbio.crud_kb", "serve", "--db", str(self.db)],
            env={"PATH": os.environ["PATH"], "PYTHONPATH": str(EXP)},
            cwd=str(EXP),
        )
        self._err = open(self.errlog_path, "a")  # noqa: SIM115
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._stop = asyncio.Event()
        self._ready: asyncio.Future[None] = self._loop.create_future()
        self._main = asyncio.run_coroutine_threadsafe(self._session_task(params), self._loop)
        self._run(self._wait_ready())
        listed = self._run(self.session.list_tools())
        return [ToolSpec(t.name, t.description or "", t.input_schema) for t in listed.tools]

    async def _session_task(self, params: StdioServerParameters) -> None:
        # one task owns the whole session: anyio cancel scopes must exit in the task that
        # entered them
        async with stdio_client(params, errlog=self._err) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                self.session = session
                self._ready.set_result(None)
                await self._stop.wait()

    async def _wait_ready(self) -> None:
        await asyncio.wait(
            {self._ready, asyncio.wrap_future(self._main)}, return_when=asyncio.FIRST_COMPLETED
        )
        if not self._ready.done():
            self._main.result()  # re-raise the startup error

    def call(self, name: str, args: dict[str, Any]) -> str:
        res = self._run(self.session.call_tool(name, args))
        text = "\n".join(getattr(c, "text", "") for c in res.content)
        if res.is_error:
            raise RuntimeError(text)
        return text

    def teardown(self) -> None:
        self._loop.call_soon_threadsafe(self._stop.set)
        self._main.result(30)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(5)
        self._err.close()


def _tc(name: str, args: dict[str, Any], i: int) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": f"c{i}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
        ],
    }


def test_mcp_round_trip_through_agent_loop(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "Habit.md").write_text("# Habit\n\nA habit is a routine.\n")
    Store(tmp_path / "kb.db").ingest(ingest_sources(src))
    h = McpStdioHarness(tmp_path / "kb.db", tmp_path / "err.log")
    tools = h.setup(src, tmp_path)
    try:
        assert {t.name for t in tools} == {"search", "read", "list", "create", "update", "delete"}
        script = [
            _tc(
                "create",
                {"title": "Marlow Index", "body": "Habit is catalogued as entry 41\u2011C."},
                0,
            ),
            _tc("search", {"query": "41-C"}, 1),
            _tc("read", {"id": "n1"}, 2),
            _tc("update", {"id": "n1", "old": "41\u2011C", "new": "17-B"}, 3),
            _tc("search", {"query": "17-B"}, 4),
            _tc("search", {"query": "41-C"}, 5),
            _tc("update", {"id": "n1", "old": "absent", "new": "x"}, 6),
            _tc("delete", {"id": "n1"}, 7),
            _tc("read", {"id": "n1"}, 8),
            _tc("search", {"query": "Marlow"}, 9),
            {"role": "assistant", "content": "DONE"},
        ]
        calls: list[Any] = []

        def chat(model, messages, tools=None, max_tokens=0):  # noqa: ANN001
            calls.append(tools)
            return {
                "message": script[len(calls) - 1],
                "usage": {"prompt_tokens": 10, "completion_tokens": 1},
                "latency_ms": 1.0,
            }

        out = run_agent("fake", "sys", "go", h, tools, max_steps=20, chat=chat)
    finally:
        h.teardown()
    assert out["status"] == "ok" and out["final"] == "DONE" and out["tool_calls"] == 10
    res = [t["tool_calls"][0]["result"] for t in out["turns"][:10]]
    assert json.loads(res[0]) == {"id": "n1"}
    assert json.loads(res[1])[0]["id"] == "n1" and "41\u2011C" in json.loads(res[1])[0]["snippet"]
    assert "title: Marlow Index" in res[2] and "(end)" in res[2]
    assert json.loads(res[3])["updated"] is True
    assert [x["id"] for x in json.loads(res[4])] == ["n1"]
    assert json.loads(res[5]) == []
    assert res[6].startswith("ERROR: RuntimeError:") and "occurs 0 times" in res[6]
    assert json.loads(res[7])["deleted"] is True
    assert res[8].startswith("ERROR:") and "no document with id 'n1'" in res[8]
    assert json.loads(res[9]) == []
    assert calls[0] is not None and len(calls[0]) == 6  # OpenAI tool schemas from MCP list_tools


PROSE = (
    "Albedo is the measure of the diffuse reflection of solar radiation out of the total solar "
    "radiation received by an astronomical body, such as the characteristically photosensitive "
    "hydrometeorological instrumentation of an observatory. "
)


@pytest.mark.parametrize("max_chars", [8000, 1000])
@pytest.mark.parametrize(
    "kind",
    ["dense", "prose", "cjk"],
)
def test_server_read_pages_concatenate_and_fit_the_token_cap(
    st: Store, max_chars: int, kind: str
) -> None:
    """Regression: the result cap used to cut the tail of every full page while next_offset
    skipped past it, so that text was never shown."""
    from kbio.agent import MAX_RESULT_TOKENS
    from kbio.llm import ntok

    dense = {
        "dense": "".join(f"{i:05d} 97-W/{i % 7}; " for i in range(4000)),  # ~2 chars/token
        "prose": PROSE * 120,
        "cjk": "反照率是衡量天体表面反射太阳辐射能力的物理量。" * 800,
    }[kind]
    a = st.create("T" * 200, dense, ["tag"])
    srv = Server(st, max_chars=max_chars)
    got, off = "", 0
    while True:
        r = srv.call_tool("read", {"id": a, "offset": off})
        assert len(r) <= max_chars and "[... truncated" not in r
        assert ntok(r) <= MAX_RESULT_TOKENS
        head, body = r.split("\n---\n", 1)
        got += body
        m = re.search(r"\(next_offset: (\d+)\)$", head)
        if m is None:
            assert head.endswith("(end)")
            break
        off = int(m.group(1))
    assert got == dense
