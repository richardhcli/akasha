"""`python -m kbio.mab.run`: M13 conditions on MemoryAgentBench FactConsolidation (M13-PLAN.md §4).

Inputs are the benchmark's own chunks, memorize-wrapped chunks, queries and answers
(`mab_export.py`). Every call goes through `kbio.llm.chat` (throttle, cache, call log) with the
benchmark's settings: temperature 0.7, retrieve 10, its system message and templates, and its
long-context truncation (keep the last tokens). gpt-oss needs room to reason, so max_tokens is
MAX_TOKENS rather than the benchmark's 10 (which it applies only to gpt-4 models).

Conditions (single-shot, 1 call per question):
  LC      the whole memorized context in the prompt (truncated like the benchmark)
  R-bm25  BM25 over the memorized 4k chunks, top 10 (their `Simple_rag_bm25`)
  R-facts BM25 over single facts, top 10
  R-rule  R-facts after the plaintext conflict rule retires superseded facts
  K0/K1/K2 akasha memory (kbio.mab.akasha_mem): claims + journals; K1 flags conflicts, K2
          supersedes

One record per question: data/.../results/mab/<cond>/<sub_dataset>/<qi>.json (resumable)."""

from __future__ import annotations

import argparse
import json
import re
import time
from functools import cache
from typing import Any

import tiktoken
from rank_bm25 import BM25Okapi

from kbio import llm
from kbio.mab import conflict
from kbio.paths import DATA

EXPORT = DATA / "mab" / "export"
OUT = DATA / "results" / "mab"
MODEL = "gpt-oss:120b"
TEMP = 0.7
MAX_TOKENS = 2000
K = 10
# Purdue serves gpt-oss:120b with a 65,536-token limit (HTTP 400 "Input length (120252) exceeds
# model's maximum context length (65536)", pilot 2026-09-29); 62,000 leaves room for the system
# prompt, the query and MAX_TOKENS. The benchmark's 128k gpt-4o-mini config uses 128,000.
LC_LIMIT = 62_000
LC_BUFFER = 50_000  # the benchmark's truncation trigger buffer (agent._query_long_context_agent)
SUBS = [f"factconsolidation_{h}_{s}" for h in ("sh", "mh") for s in ("6k", "32k", "64k", "262k")]
ENC = tiktoken.get_encoding("o200k_base")  # tiktoken.encoding_for_model("gpt-4o-mini")
FACT_RE = re.compile(r"^(\d+)\. (.*)$", re.M)
QUERY_RE = [re.compile(r"Now Answer the Question:\s*(.*)", re.S),
            re.compile(r"Here is the conversation:\s*(.*)", re.S)]  # fmt: skip


@cache
def load(sub: str) -> dict[str, Any]:
    return json.loads((EXPORT / f"{sub}.json").read_text())


@cache
def facts(sub: str) -> tuple[tuple[int, str], ...]:
    return tuple((int(s), f"{s}. {t}") for s, t in FACT_RE.findall(load(sub)["context"]))


def retrieval_query(message: str) -> str:
    """The benchmark's `_extract_retrieval_query`."""
    for rx in QUERY_RE:
        m = rx.search(message)
        if m:
            return "".join(m.groups())
    return message


class BM25:
    """LangChain's BM25Retriever as the benchmark uses it: BM25Okapi over `text.split()`,
    `get_top_n`."""

    def __init__(self, docs: list[str]) -> None:
        self.docs = docs
        self.bm25 = BM25Okapi([d.split() for d in docs])

    def top(self, query: str, k: int = K) -> list[str]:
        return self.bm25.get_top_n(query.split(), self.docs, n=k)


@cache
def index(cond: str, sub: str) -> BM25:
    if cond == "R-bm25":
        return BM25(list(load(sub)["memorized"]))
    fs = facts(sub)
    if cond == "R-rule":
        retired, _ = conflict.supersede_plain(list(fs))
        fs = tuple(f for f in fs if f[0] not in retired)
    return BM25([t for _s, t in fs])


def rag_message(retrieved: list[str], query: str) -> str:
    """The benchmark's RAG prompt: 'Memory i:' blocks, then the query."""
    ctx = [f"{t}\n" for t in retrieved]
    return "\n".join(f"Memory {i + 1}:\n{t}" for i, t in enumerate(ctx)) + "\n" + query


@cache
def lc_context(sub: str) -> tuple[str, int]:
    """The benchmark's long-context memory: memorize-wrapped chunks joined by newlines, truncated
    from the front (the newest facts are kept) when the limit is within the buffer."""
    rec = load(sub)
    ctx = "\n".join(rec["memorized"]).strip()
    toks = ENC.encode(ctx, disallowed_special=())
    if LC_LIMIT <= rec["data_config"]["context_max_length"] + LC_BUFFER:
        toks = toks[-rec["data_config"]["context_max_length"] :]
        toks = toks[-LC_LIMIT:]
        ctx = ENC.decode(toks)
    return ctx, len(toks)


def build(cond: str, sub: str, qi: int) -> tuple[list[dict], dict[str, Any]]:
    rec = load(sub)
    system = rec["system"]
    if cond == "LC":
        ctx, n = lc_context(sub)
        msg = ctx + "\n" + rec["queries"]["lc"][qi]
        return [{"role": "system", "content": system}, {"role": "user", "content": msg}], {
            "context_tokens": n
        }
    query = rec["queries"]["rag"][qi]
    rq = retrieval_query(query)
    if cond.startswith("K"):
        from kbio.mab import akasha_mem

        retrieved = akasha_mem.retrieve(cond, sub, rq, K)
    else:
        retrieved = index(cond, sub).top(rq, K)
    msg = rag_message(retrieved, query)
    return [{"role": "system", "content": system}, {"role": "user", "content": msg}], {
        "retrieval_query": rq,
        "retrieved": retrieved,
    }


def path(cond: str, sub: str, qi: int):
    return OUT / cond / sub / f"{qi:03d}.json"


def run_one(cond: str, sub: str, qi: int) -> dict[str, Any]:
    t0 = time.monotonic()
    messages, info = build(cond, sub, qi)
    rec = load(sub)
    out: dict[str, Any] = {"cond": cond, "sub": sub, "qi": qi, "answer": rec["answers"][qi], **info}
    try:
        r = llm.chat(MODEL, messages, max_tokens=MAX_TOKENS, temperature=TEMP,
                     timeout=600 if cond == "LC" else 150)  # fmt: skip
        out.update(status="ok", output=r["message"]["content"], usage=r["usage"],
                   finish_reason=r.get("finish_reason"), cached=r.get("cached", False),
                   latency_ms=r.get("latency_ms"))  # fmt: skip
    except llm.LLMError as e:
        out.update(status="FAILED", output="", error=str(e)[:500])
    out["wall_s"] = round(time.monotonic() - t0, 2)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(prog="kbio.mab.run")
    ap.add_argument("--conds", default="LC,R-bm25,R-facts,R-rule")
    ap.add_argument("--subs", default=",".join(s.removeprefix("factconsolidation_") for s in SUBS))
    ap.add_argument("--limit", type=int, default=0, help="first N questions per sub-dataset")
    args = ap.parse_args()
    t_start = time.time()
    for cond in args.conds.split(","):
        for short in args.subs.split(","):
            sub = f"factconsolidation_{short}"
            n = len(load(sub)["answers"])
            qis = range(min(n, args.limit) if args.limit else n)
            todo = [qi for qi in qis if not path(cond, sub, qi).exists()]
            print(f"== {cond} {short}: {len(todo)}/{len(qis)} to do", flush=True)
            if cond.startswith("K") and todo:
                from kbio.mab import akasha_mem

                akasha_mem.ensure_store(cond, sub)
            for qi in todo:
                out = run_one(cond, sub, qi)
                p = path(cond, sub, qi)
                p.parent.mkdir(parents=True, exist_ok=True)
                tmp = p.with_suffix(".tmp")
                tmp.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
                tmp.rename(p)
                got, gold = out["output"][:60], out["answer"][0][:30]
                status = out["status"]
                print(f"{cond} {short} q{qi:03d} {status} out={got!r} gold={gold!r}", flush=True)
    print(f"done in {round(time.time() - t_start)} s; llm {llm.STATS}", flush=True)


if __name__ == "__main__":
    main()
