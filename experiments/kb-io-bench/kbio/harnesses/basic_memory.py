"""Conditions B and C: basic-memory (pinned submodule, v0.23.2) as the SOTA harness.

Its MCP server runs over stdio (`basic-memory mcp --project kb`) with every config file and its
SQLite under the scratch HOME (`BASIC_MEMORY_CONFIG_DIR`). Tool schemas come from the server's
`list_tools` and are passed to the model unchanged. Only the subset that matches the akasha
adapter is exposed: search_notes, read_note, build_context (READ), plus write_note and edit_note
(WRITE/UPDATE). Settings: basic-memory's defaults for frontmatter and permalinks (it adds
title/type/permalink frontmatter to every file on first index). Turning that off leaves every
pre-existing note without a permalink, so `build_context` finds nothing: the baseline would be
crippled. The rewrite is measured (M6) and reported as a threat instead. Semantic search is on
(local fastembed bge-small-en-v1.5; `search_notes` defaults to hybrid).
"""

from __future__ import annotations

import asyncio
import json
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from kbio.agent import ToolSpec
from kbio.env import scratch_env
from kbio.paths import DATA, EXP, SCRATCH_HOME, TRASH

BM_BIN = EXP / ".venv" / "bin" / "basic-memory"
BM_DIR = SCRATCH_HOME / ".config" / "basic-memory"
READ_TOOLS = ("search_notes", "read_note", "build_context")
WRITE_TOOLS = ("write_note", "edit_note")
PROJECT = "kb"
SETTINGS = {
    "ensure_frontmatter_on_sync": True,
    "disable_permalinks": False,
    "semantic_search_enabled": True,
    "auto_update": False,
    "logfire_enabled": False,
    "cloud_promo_opt_out": True,
    "cloud_promo_first_run_shown": True,
    "index_changes": True,
}


def write_config(vault: Path) -> None:
    BM_DIR.mkdir(parents=True, exist_ok=True)
    cfg = {
        **SETTINGS,
        "projects": {PROJECT: {"path": str(vault.resolve()), "mode": "local"}},
        "default_project": PROJECT,
    }
    (BM_DIR / "config.json").write_text(json.dumps(cfg, indent=2) + "\n")


def backup_db(dest: Path) -> None:
    """Consistent single-file copy of the live index (WAL folded in), for a template."""
    import sqlite3

    src = sqlite3.connect(f"file:{BM_DIR / 'memory.db'}?mode=ro", uri=True, timeout=30)
    out = sqlite3.connect(dest)
    try:
        src.backup(out)
    finally:
        out.close()
        src.close()


def reset_db(label: str) -> None:
    """Fresh basic-memory index for one snapshot (old DB moved to trash, never deleted)."""
    bin_dir = TRASH / "bm-stores" / (time.strftime("%Y%m%d-%H%M%S-") + label)
    for f in BM_DIR.glob("memory.db*"):
        bin_dir.mkdir(parents=True, exist_ok=True)
        f.rename(bin_dir / f.name)


class BasicMemoryHarness:
    name = "basic-memory"

    def __init__(
        self,
        write_tools: bool = True,
        index_timeout: float = 1800.0,
        restore_db: Path | None = None,
    ) -> None:
        self.write_tools = write_tools
        self.index_timeout = index_timeout
        self.restore_db = restore_db  # a template index (see run.build_bm_template) to start from
        self._loop: asyncio.AbstractEventLoop | None = None

    # ---- a private event loop in a thread keeps the MCP session alive across sync calls ----
    def _run(self, coro: Any, timeout: float = 600.0) -> Any:
        assert self._loop is not None
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    def setup(self, kb_dir: Path, scratch: Path) -> list[ToolSpec]:
        self.root = kb_dir.resolve()
        reset_db(kb_dir.parent.name)
        if self.restore_db is not None:
            BM_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.restore_db, BM_DIR / "memory.db")
        write_config(self.root)
        env = scratch_env(extra={"BASIC_MEMORY_CONFIG_DIR": str(BM_DIR), "NO_COLOR": "1"})
        log = DATA / "logs" / "basic-memory-mcp.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        self._errlog = open(log, "a")  # noqa: SIM115
        params = StdioServerParameters(
            command=str(BM_BIN), args=["mcp", "--project", PROJECT], env=env, cwd=str(self.root)
        )
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._ready = asyncio.run_coroutine_threadsafe(self._open(params), self._loop)
        self._ready.result(120)
        listed = self._run(self.session.list_tools())
        keep = READ_TOOLS + (WRITE_TOOLS if self.write_tools else ())
        self.all_tools = [t.name for t in listed.tools]
        tools = [
            ToolSpec(t.name, t.description or "", t.input_schema)
            for t in listed.tools
            if t.name in keep
        ]
        tools.sort(key=lambda t: keep.index(t.name))
        self.index_seconds = self.wait_indexed()
        return tools

    async def _open(self, params: StdioServerParameters) -> None:
        self._stdio = stdio_client(params, errlog=self._errlog)
        read, write = await self._stdio.__aenter__()
        self._sess_cm = ClientSession(read, write)
        self.session = await self._sess_cm.__aenter__()
        await self.session.initialize()

    async def _close(self) -> None:
        try:
            await self._sess_cm.__aexit__(None, None, None)
        finally:
            await self._stdio.__aexit__(None, None, None)

    def _call(self, name: str, args: dict[str, Any], timeout: float = 600.0) -> str:
        res = self._run(self.session.call_tool(name, args), timeout)
        parts = []
        for c in res.content:
            parts.append(getattr(c, "text", None) or json.dumps(c.model_dump(), default=str))
        text = "\n".join(parts)
        if res.is_error:
            raise RuntimeError(text[:2000])
        return text

    def wait_indexed(self) -> float:
        """Block until the initial sync has indexed every markdown file and the embeddings have
        settled (entity and vector-chunk counts unchanged over two polls)."""
        t0 = time.monotonic()
        n_files = sum(1 for p in self.root.rglob("*.md") if not p.name.startswith("."))
        last, stable = (-1, -1), 0
        while time.monotonic() - t0 < self.index_timeout:
            n = self.indexed_count()
            if n[0] >= n_files and n[1] > 0 and n == last:
                stable += 1
                if stable >= 2:
                    break
            else:
                stable = 0
            last = n
            time.sleep(2.0)
        return round(time.monotonic() - t0, 1)

    def indexed_count(self) -> tuple[int, int]:
        """(entities, vector chunks) in basic-memory's index."""
        import sqlite3

        db = BM_DIR / "memory.db"
        if not db.exists():
            return 0, 0
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
            try:
                e = con.execute("SELECT count(*) FROM entity").fetchone()[0]
                v = con.execute("SELECT count(*) FROM search_vector_chunks").fetchone()[0]
                return e, v
            finally:
                con.close()
        except sqlite3.Error:
            return 0, 0

    def wait_synced(self, timeout: float = 120.0) -> float:
        """Block until every markdown file's sha256 equals its indexed checksum (the watcher has
        re-indexed files changed by the agent or by the akasha daemon)."""
        import hashlib
        import sqlite3

        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            files = {
                str(p.relative_to(self.root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.root.rglob("*.md")
            }
            try:
                con = sqlite3.connect(f"file:{BM_DIR / 'memory.db'}?mode=ro", uri=True, timeout=5)
                try:
                    idx = dict(con.execute("SELECT file_path, checksum FROM entity").fetchall())
                finally:
                    con.close()
            except sqlite3.Error:
                idx = {}
            if all(idx.get(f) == h for f, h in files.items()):
                return round(time.monotonic() - t0, 1)
            time.sleep(1.0)
        return -1.0

    def call(self, name: str, args: dict[str, Any]) -> str:
        allowed = READ_TOOLS + (WRITE_TOOLS if self.write_tools else ())
        if name not in allowed:
            raise ValueError(f"unknown tool {name}")
        return self._call(name, args)

    def teardown(self) -> None:
        if self._loop is None:
            return
        try:
            self._run(self._close(), 30)
        except Exception:  # the server may already be gone
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(5)
        self._loop = None
        self._errlog.close()
