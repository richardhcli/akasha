"""Single entry point: `kbio <command> [args]` (run, analyze, corpus, tasks, gate, check, wiki)."""

from __future__ import annotations

import importlib
import sys

COMMANDS = {
    "run": "kbio.run",
    "analyze": "kbio.analyze",
    "corpus": "kbio.corpus",
    "tasks": "kbio.tasks",
    "gate": "kbio.gate",
    "check": "kbio.checks",
    "wiki": "kbio.sources.wikipedia",
}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print("usage: kbio {" + ",".join(COMMANDS) + "} [args]")
        raise SystemExit(2)
    cmd = sys.argv.pop(1)
    sys.argv[0] = f"kbio {cmd}"
    importlib.import_module(COMMANDS[cmd]).main()


if __name__ == "__main__":
    main()
