"""One BM25 retriever for every condition, packed to an equal token budget (cl100k tokens).
Writes results/retrieval.json and prints gold-span recall per condition x budget (no LLM)."""

from __future__ import annotations

import json
import math
import re
from collections import Counter

import tiktoken
from common import DATA

ENC = tiktoken.get_encoding("cl100k_base")
CONDITIONS = ["raw", "clean", "clean-atoms", "akasha", "akasha-1hop"]
BUDGETS = [250, 500, 1000, 2000, 4000]
STOP = set(
    """a an the and or of to in on for is are was were be been it its this that these those
with as by at from what which who how do does did not no can should would into about than then
so if when where why their there they them your you i me my we our according notes note""".split()
)


def ntok(s: str) -> int:
    return len(ENC.encode(s))


def terms(s: str) -> list[str]:
    out = []
    for w in re.findall(r"[a-z0-9]+", s.lower()):
        if w in STOP or len(w) < 2:
            continue
        for suf in ("ing", "ed", "es", "s"):
            if len(w) > len(suf) + 3 and w.endswith(suf):
                w = w[: -len(suf)]
                break
        out.append(w)
    return out


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.2, b: float = 0.75) -> None:
        self.docs = [Counter(d) for d in docs]
        self.len = [len(d) for d in docs]
        self.avg = sum(self.len) / len(docs)
        df = Counter(t for d in self.docs for t in d)
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.k1, self.b = k1, b

    def scores(self, q: list[str]) -> list[float]:
        out = []
        for d, dl in zip(self.docs, self.len):
            s = 0.0
            for t in set(q):
                if t in d:
                    tf = d[t]
                    s += (
                        self.idf[t]
                        * tf
                        * (self.k1 + 1)
                        / (tf + self.k1 * (1 - self.b + self.b * dl / self.avg))
                    )
            out.append(s)
        return out


def load_units(cond: str) -> list[dict]:
    base = "akasha" if cond == "akasha-1hop" else cond
    units = json.loads((DATA / "units" / f"{base}.json").read_text())
    for u in units:
        where = " › ".join([u["title"]] + u["heading_path"])
        cite = f"{u['source']}#^tm-{u['id']}" if "id" in u else u["source"]
        u["cite"] = cite
        u["header"] = f"{cite} — {where}"
        u["index_text"] = where + "\n" + u["body"]
    return units


def retrieve(cond: str, units: list[dict], index: BM25, question: str, budget: int) -> list[dict]:
    scores = index.scores(terms(question))
    ranked = sorted((i for i, s in enumerate(scores) if s > 0), key=lambda i: -scores[i])[:60]
    by_id = {u["id"]: u for u in units if "id" in u}
    chosen: list[dict] = []
    used = 0
    seen: set[int] = set()

    def add(i: int, via: str) -> bool:
        nonlocal used
        u = units[i]
        block = f"[S{len(chosen) + 1}] {u['header']}\n{u['body']}\n\n"
        cost = ntok(block)
        if used + cost > budget:
            return False
        chosen.append(
            {
                "unit": i,
                "via": via,
                "score": round(scores[i], 3),
                "tokens": cost,
                "cite": u["cite"],
                "header": u["header"],
                "body": u["body"],
                "source": u["source"],
            }
        )
        used += cost
        seen.add(i)
        return True

    pos = {u["id"]: i for i, u in enumerate(units) if "id" in u}
    for i in ranked:
        if i in seen:
            continue
        if not add(i, "bm25") and not chosen:
            # the single best unit is larger than the budget: truncate it to fit
            u = units[i]
            body = ENC.decode(ENC.encode(u["body"])[: max(budget - ntok(u["header"]) - 12, 16)])
            chosen.append(
                {
                    "unit": i,
                    "via": "bm25-truncated",
                    "score": round(scores[i], 3),
                    "tokens": budget,
                    "cite": u["cite"],
                    "header": u["header"],
                    "body": body,
                    "source": u["source"],
                }
            )
            used = budget
            seen.add(i)
        if cond == "akasha-1hop" and i in seen:
            for dep in units[i].get(
                "links", []
            ):  # the linked note's definition atom (mirrored in this file)
                j = pos[dep]
                if j not in seen and dep in by_id:
                    add(j, f"1hop:{units[i]['id']}")
    return chosen


def render(chunks: list[dict]) -> str:
    return "".join(
        f"[S{k}] {c['header']}\n{c['body']}\n\n" for k, c in enumerate(chunks, 1)
    ).strip()


def main() -> None:
    questions = json.loads((DATA / "questions.json").read_text())
    out: dict = {"budgets": BUDGETS, "conditions": CONDITIONS, "runs": {}}
    table = {}
    for cond in CONDITIONS:
        units = load_units(cond)
        index = BM25([terms(u["index_text"]) for u in units])
        for budget in BUDGETS:
            key = f"{cond}@{budget}"
            recs = []
            for q in questions:
                chunks = retrieve(cond, units, index, q["question"], budget)
                ctx = render(chunks)
                hit = [any(g["span"] in c["body"] for c in chunks) for g in q["gold"]]
                recs.append(
                    {
                        "qid": q["id"],
                        "context_tokens": ntok(ctx),
                        "gold_hits": hit,
                        "recall": (sum(hit) / len(hit)) if hit else None,
                        "chunks": chunks,
                    }
                )
            out["runs"][key] = recs
            answerable = [r for r in recs if r["recall"] is not None]
            table[key] = (
                sum(r["recall"] for r in answerable) / len(answerable),
                sum(all(r["gold_hits"]) for r in answerable) / len(answerable),
                sum(r["context_tokens"] for r in recs) / len(recs),
                sum(len(r["chunks"]) for r in recs) / len(recs),
            )
    (DATA / "results").mkdir(exist_ok=True)
    (DATA / "results" / "retrieval.json").write_text(json.dumps(out, ensure_ascii=False) + "\n")
    print(f"{'condition':<14}" + "".join(f"{b:>22}" for b in BUDGETS))
    print(" " * 14 + "".join(f"{'span-rec/all-hit/tok':>22}" for _ in BUDGETS))
    for cond in CONDITIONS:
        print(
            f"{cond:<14}"
            + "".join(
                f"{table[f'{cond}@{b}'][0]:>9.2f}/{table[f'{cond}@{b}'][1]:.2f}/{table[f'{cond}@{b}'][2]:>5.0f}"
                for b in BUDGETS
            )
        )


if __name__ == "__main__":
    main()
