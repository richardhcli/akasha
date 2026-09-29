"""Serve a v1 kbio harness (a Python class) as an MCP stdio server, so v2 drives it like any
other MCP harness: `python -m kbio.mcp_wrap files|akasha --vault DIR`.

The JSON-RPC loop is crud-kb's (`crud_kb.server.Server.handle/serve`); only the tool table and
`call_tool` differ. The harness's own schemas and results pass through unchanged, with no extra
result cap (the agent loop's 2,000-token cap applies to every harness alike). Every tool the
class offers is listed; the manifest's allowlists decide what an agent may call."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from kbio.crud_kb import server


def make(name: str) -> Any:
    if name == "files":
        from kbio.harnesses.files import FilesHarness

        return FilesHarness()
    if name == "akasha":
        from kbio.harnesses.akasha import AkashaHarness

        return AkashaHarness(write_tools=True)
    raise SystemExit(f"mcp_wrap: unknown harness {name}")


class Wrapped(server.Server):
    def __init__(self, name: str, vault: Path) -> None:  # noqa: D107 - no store, no super()
        self.name = name
        self.h = make(name)
        specs = self.h.setup(vault, vault.parent)
        self.tools = {t.name: {"description": t.description, "inputSchema": t.parameters}
                      for t in specs}  # fmt: skip
        self.max_chars = 10**9

    def call_tool(self, name: str, args: dict[str, Any]) -> str:
        if name not in self.tools:
            raise ValueError(f"unknown tool {name!r}")
        try:
            return self.h.call(name, args)
        except (ValueError, TypeError, KeyError):
            raise
        except Exception as e:  # reported to the model as a tool error, never a dead server
            raise ValueError(f"{type(e).__name__}: {e}") from e

    def handle(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        reply = super().handle(msg)
        if reply and msg.get("method") == "initialize":
            reply["result"]["serverInfo"] = {"name": f"kbio-{self.name}", "version": "0.1.0"}
        return reply


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="kbio.mcp_wrap")
    ap.add_argument("harness")
    ap.add_argument("--vault", type=Path, required=True)
    a = ap.parse_args(argv)
    w = Wrapped(a.harness, a.vault)
    print(f"mcp_wrap: {a.harness} on {a.vault}", file=sys.stderr, flush=True)
    try:
        w.serve(sys.stdin.buffer, sys.stdout.buffer)
    finally:
        w.h.teardown()


if __name__ == "__main__":
    main()
