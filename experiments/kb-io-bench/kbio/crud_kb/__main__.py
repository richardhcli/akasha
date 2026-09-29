"""crud-kb CLI: `python -m kbio.crud_kb serve|ingest|export --db PATH`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from kbio.crud_kb import server
from kbio.crud_kb.store import Store, ingest_sources


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="crud-kb", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="MCP server over stdio")
    s.add_argument("--db", required=True)
    s.add_argument(
        "--white-box", action="store_true", help="also expose `export` (never to agents)"
    )
    s.add_argument("--max-result-chars", type=int, default=server.MAX_RESULT_CHARS)
    i = sub.add_parser("ingest", help="bulk-load *.md / *.jsonl (no LLM); prints timings as JSON")
    i.add_argument("source", type=Path)
    i.add_argument("--db", required=True)
    e = sub.add_parser("export", help="every live document as JSON lines (white-box scoring)")
    e.add_argument("--db", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "serve":
        server.main(a.db, a.white_box, a.max_result_chars)
        return
    store = Store(a.db)
    try:
        if a.cmd == "ingest":
            if not a.source.exists():
                sys.exit(f"crud-kb: no such file or directory: {a.source}")
            stats = store.ingest(ingest_sources(a.source))
            print(json.dumps({"source": str(a.source), "db": a.db, **stats}))
        else:
            for d in store.export():
                sys.stdout.write(json.dumps(d, ensure_ascii=False) + "\n")
    finally:
        store.close()


if __name__ == "__main__":
    main()
