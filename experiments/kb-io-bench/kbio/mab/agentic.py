"""`python -m kbio.mab.agentic`: the M13 secondary agentic subset (M13-PREREG.md §Secondary,
fixed by Amendment 1 before it runs). Descriptive only; not part of the claim.

Same kbio agent loop (`kbio.agent.run_agent`, 12-step cap, 2,000-token results cap, temperature 0)
and the same user message for both agents: the benchmark's RAG query text (knowledge-pool rule,
counterfactual example, question), followed by TOOL_NOTE. The system message is the benchmark's.

  A-P   plaintext: `list_dir` / `grep` / `read_file` over one markdown file, facts.md, holding the
        benchmark's fact list verbatim (one numbered fact per line), via kbio's FilesHarness.
  A-K2  akasha: the K2 store (superseded facts tombstoned), via `search` (any-term, bm25, top 10
        live claims) and `neighborhood` (a node's linked nodes: its journal and contradictions).

Questions: 20 per sub-dataset, `random.Random(13).sample(range(100), 20)`, 160 in total. Records go
to data/.../results/mab/<A-P|A-K2>/<sub>/<qi>.json in the primary schema, so `mab_score.py` scores
them with the benchmark's metric."""

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path
from typing import Any

from kbio import akasha_ctl as ak
from kbio.agent import ToolSpec, run_agent
from kbio.harnesses.files import FilesHarness
from kbio.mab import akasha_mem
from kbio.mab.run import MODEL, SUBS, load, path
from kbio.paths import DATA

PLAIN = DATA / "mab" / "plain"
STEPS = 12
TOOL_NOTE = (
    "\n\nThe knowledge pool is not in this message: use your tools to look facts up. When you "
    "are done, reply with only the answer."
)


def sample(sub: str) -> list[int]:
    return sorted(random.Random(13).sample(range(len(load(sub)["answers"])), 20))


class AkashaTools:
    name = "akasha-memory"

    def setup(self, kb_dir: Path, scratch: Path) -> list[ToolSpec]:
        s = {"type": "string"}
        return [
            ToolSpec("search", "Search the memory for facts matching any of the words in `query`; "
                     "returns up to 10 current facts, best match first, as `id: text`.",
                     {"type": "object", "properties": {"query": s}, "required": ["query"]}),
            ToolSpec("neighborhood", "The nodes linked to a fact `id`: the journal entry it was "
                     "learned from, and any facts that contradict it.",
                     {"type": "object", "properties": {"id": s}, "required": ["id"]}),
        ]  # fmt: skip

    def call(self, name: str, args: dict[str, Any]) -> str:
        if name == "search":
            params = {"q": str(args["query"]), "mode": "any", "limit": 10, "type": "claim",
                      "status": "live"}  # fmt: skip
            hits = ak.api("GET", "/search", params=params)["results"]
            return "\n".join(f"{h['id']}: {h['body'].strip()}" for h in hits) or "no matches"
        if name == "neighborhood":
            nid = str(args["id"])
            nb = ak.api("GET", f"/nodes/{nid}/neighborhood", params={"hops": 1})
            lines = []
            for e in nb["edges"]:
                other = e["dst"] if e["src"] == nid else e["src"]
                node = ak.api("GET", f"/nodes/{other}")
                lines.append(f"{e['edge_type']} {other} ({node['node_type']}, {node['status']}): "
                             f"{node['body'].strip()[:300]}")  # fmt: skip
            return "\n".join(lines) or "no linked nodes"
        raise ValueError(f"unknown tool {name}")

    def teardown(self) -> None:
        pass


def plain_vault(sub: str) -> Path:
    v = PLAIN / sub.rsplit("_", 1)[1]
    if not (v / "facts.md").exists():
        v.mkdir(parents=True, exist_ok=True)
        (v / "facts.md").write_text(load(sub)["context"].strip() + "\n")
    return v


def run_one(cond: str, sub: str, qi: int) -> dict[str, Any]:
    rec = load(sub)
    t0 = time.monotonic()
    if cond == "A-P":
        h: Any = FilesHarness()
        tools = [t for t in h.setup(plain_vault(sub), DATA / "tmp") if t.name in
                 ("list_dir", "grep", "read_file")]  # fmt: skip
    else:
        h = AkashaTools()
        tools = h.setup(Path("."), DATA / "tmp")
    user = rec["queries"]["rag"][qi] + TOOL_NOTE
    a = run_agent(MODEL, rec["system"], user, h, tools, STEPS)
    return {"cond": cond, "sub": sub, "qi": qi, "answer": rec["answers"][qi],
            "status": "ok" if a["status"] == "ok" else "FAILED", "output": a["final"],
            "agent": {k: a[k] for k in ("steps", "tool_calls", "total_tokens", "hit_step_cap")},
            "trace": [[c["name"], c["args"]] for t in a["turns"] for c in t["tool_calls"]],
            "wall_s": round(time.monotonic() - t0, 2)}  # fmt: skip


def main() -> None:
    ap = argparse.ArgumentParser(prog="kbio.mab.agentic")
    ap.add_argument("--conds", default="A-P,A-K2")
    args = ap.parse_args()
    for cond in args.conds.split(","):
        for sub in SUBS:
            if cond == "A-K2":
                akasha_mem.ensure_store("K2", sub)
            todo = [q for q in sample(sub) if not path(cond, sub, q).exists()]
            print(f"== {cond} {sub}: {len(todo)}/20 to do", flush=True)
            for qi in todo:
                out = run_one(cond, sub, qi)
                p = path(cond, sub, qi)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.with_suffix(".tmp").write_text(json.dumps(out, ensure_ascii=False, indent=1))
                p.with_suffix(".tmp").rename(p)
                got = out["output"][:50]
                print(f"{cond} {sub} q{qi:03d} {out['status']} steps={out['agent']['steps']} "
                      f"out={got!r} gold={out['answer'][0][:30]!r}", flush=True)  # fmt: skip


if __name__ == "__main__":
    main()
