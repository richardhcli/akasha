"""v2: any MCP stdio server as a harness, described by `config/harnesses/<name>.toml`.

Manifest keys (V2-PLAN §6):
  [mcp]     command = [...]          the server, spoken to over stdio
  env       = {...}                  extra environment (on top of kbio.env.scratch_env)
  [ingest]  command = [...]          optional bulk load of the background corpus (no LLM)
  [reset]   command = [...]          optional fresh-store step, run before ingest
  [tools]   read = [...], write = [...]   allowlists; READ tasks get `read` only

Command placeholders: {python} (this interpreter), {exp} (experiments/kb-io-bench), {kb} (the
snapshot directory), {store} (a per-snapshot store directory under the scratch dir).
The server's schemas are passed to the model unchanged; only allowlisted tools are exposed or
callable. Streamable-HTTP servers (`mcp.url`) are not supported yet: every planned harness is
stdio.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import threading
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from kbio.agent import ToolSpec
from kbio.env import scratch_env
from kbio.paths import CONFIG, DATA, EXP


@dataclass
class Manifest:
    name: str
    command: list[str]
    read: list[str]
    write: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    ingest: list[str] | None = None
    reset: list[str] | None = None


def load_manifest(name_or_path: str | Path) -> Manifest:
    p = Path(name_or_path)
    if not p.suffix:
        p = CONFIG / "harnesses" / f"{name_or_path}.toml"
    raw = tomllib.loads(p.read_text(encoding="utf-8"))
    mcp = raw.get("mcp", {})
    if "command" not in mcp:
        raise ValueError(f"{p}: only stdio servers (mcp.command) are supported")
    tools = raw.get("tools", {})
    return Manifest(
        name=raw.get("name", p.stem),
        command=list(mcp["command"]),
        read=list(tools.get("read", [])),
        write=list(tools.get("write", [])),
        env={k: str(v) for k, v in raw.get("env", {}).items()},
        ingest=list(raw["ingest"]["command"]) if "ingest" in raw else None,
        reset=list(raw["reset"]["command"]) if "reset" in raw else None,
    )


class McpHarness:
    def __init__(self, manifest: Manifest, write_tools: bool = True, ingest: bool = True) -> None:
        self.m = manifest
        self.name = manifest.name
        self.write_tools = write_tools
        self.do_ingest = ingest
        self.ingest_stats: dict[str, Any] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def allowed(self) -> list[str]:
        return self.m.read + (self.m.write if self.write_tools else [])

    def _fill(self, cmd: list[str]) -> list[str]:
        subs = {
            "python": sys.executable,
            "exp": str(EXP),
            "kb": str(self.kb),
            "store": str(self.store),
        }
        return [c.format(**subs) for c in cmd]

    def _env(self) -> dict[str, str]:
        return scratch_env(extra={"PYTHONPATH": str(EXP), **self.m.env})

    def _step(self, cmd: list[str], label: str) -> dict[str, Any]:
        t0 = time.perf_counter()
        res = subprocess.run(
            self._fill(cmd), cwd=EXP, env=self._env(), capture_output=True, text=True
        )
        if res.returncode != 0:
            raise RuntimeError(
                f"{self.name} {label} failed ({res.returncode}): {res.stderr[-2000:]}"
            )
        out: dict[str, Any] = {"wall_s": round(time.perf_counter() - t0, 2)}
        try:
            parsed = json.loads(res.stdout.strip().splitlines()[-1])
            if isinstance(parsed, dict):
                out.update(parsed)
        except (json.JSONDecodeError, IndexError):
            pass
        return out

    # ---- a private event loop in a thread; one task owns the whole MCP session ----
    def _run(self, coro: Any, timeout: float = 600.0) -> Any:
        assert self._loop is not None
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    async def _session_task(self, params: StdioServerParameters) -> None:
        # anyio cancel scopes must be exited by the task that entered them
        async with stdio_client(params, errlog=self._errlog) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                self.session = session
                self._ready.set_result(None)
                await self._stop.wait()

    async def _wait_ready(self) -> None:
        main = asyncio.wrap_future(self._main)
        await asyncio.wait({self._ready, main}, return_when=asyncio.FIRST_COMPLETED)
        if not self._ready.done():
            self._main.result()  # re-raise the startup error
            raise RuntimeError(f"{self.name}: MCP server exited during startup")

    def setup(self, kb_dir: Path, scratch: Path) -> list[ToolSpec]:
        self.kb = kb_dir.resolve()
        self.store = (scratch / "store").resolve()
        self.store.mkdir(parents=True, exist_ok=True)
        if self.m.reset:
            self._step(self.m.reset, "reset")
        if self.m.ingest and self.do_ingest:
            self.ingest_stats = self._step(self.m.ingest, "ingest")
        log = DATA / "logs" / f"mcp-{self.name}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        self._errlog = open(log, "a")  # noqa: SIM115
        cmd = self._fill(self.m.command)
        params = StdioServerParameters(command=cmd[0], args=cmd[1:], env=self._env(), cwd=str(EXP))
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._stop = asyncio.Event()
        self._ready: asyncio.Future[None] = self._loop.create_future()
        self._main = asyncio.run_coroutine_threadsafe(self._session_task(params), self._loop)
        self._run(self._wait_ready(), 120)
        listed = self._run(self.session.list_tools())
        self.all_tools = [t.name for t in listed.tools]
        missing = [t for t in self.allowed if t not in self.all_tools]
        if missing:
            raise ValueError(f"{self.name}: allowlisted tools not offered by the server: {missing}")
        by_name = {t.name: t for t in listed.tools}
        return [
            ToolSpec(n, by_name[n].description or "", by_name[n].input_schema) for n in self.allowed
        ]

    def call(self, name: str, args: dict[str, Any]) -> str:
        if name not in self.allowed:
            raise ValueError(f"unknown tool {name}")
        res = self._run(self.session.call_tool(name, args))
        text = "\n".join(
            getattr(c, "text", None) or json.dumps(c.model_dump(), default=str) for c in res.content
        )
        if res.is_error:
            raise RuntimeError(text[:2000])
        return text

    def teardown(self) -> None:
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(self._stop.set)
        try:
            self._main.result(30)
        except Exception:  # the server may already be gone
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(5)
        self._loop = None
        self._errlog.close()
