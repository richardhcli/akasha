"""`kbio analyze2`: offline, deterministic aggregation of v2 results (kbio.run2 records).

Reads data/.../v2/results/<model>/<harness>/<rung>/<op>/*.json, the probe set and its patches
(never a store or the endpoint). Writes results/v2/summary.json and results/v2/<model>.md
(tracked; ids and numbers only) and data/.../v2/per-question-<model>.md (local; every answer).
Every metric is per (harness, rung, op); the unit for CIs and paired tests is the task (the
follow-ups of one CREATE/UPDATE/DELETE task form one unit, as do a CREATE task's control reads).
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
from collections import defaultdict
from typing import Any

from kbio import probes, score2
from kbio.analyze import BOOT, binom_two_sided, mean, net_tokens, unexposed_calls
from kbio.paths import RESULTS as TRACKED
from kbio.run import model_dir
from kbio.run2 import RESULTS

OUT = TRACKED / "v2"
OPS = ["read", "control", "create", "update", "delete"]


def rung_key(name: str) -> int:
    return int(float(name)) if name.startswith("1e") else int(name)


def load(model: str) -> dict[tuple[str, str, str], list[dict]]:
    out: dict[tuple[str, str, str], list[dict]] = {}
    base = RESULTS / model_dir(model)
    if not base.exists():
        return out
    for hdir in sorted(base.iterdir()):
        for rdir in sorted(hdir.iterdir(), key=lambda p: rung_key(p.name)):
            for op in OPS:
                d = rdir / op
                if d.exists():
                    recs = [json.loads(p.read_text()) for p in sorted(d.glob("*.json"))]
                    out[(hdir.name, rdir.name, op)] = recs
    return out


class Ctx:
    """Probe-set context: base texts of the rung-1e2 pages and an id resolver."""

    def __init__(self) -> None:
        self.base = json.loads(probes.PATCHES.read_text())["base"]
        self.drop = probes.dropped(json.loads(probes.PROBES.read_text()))  # CB-leaked probes
        pages = probes.rung_pages()
        self.titles = {p["id"]: p["title"] for p in pages}
        self.md = probes.md_names(pages)

    def ids(self, added: dict[str, Any] | None = None) -> score2.Ids:
        titles = dict(self.titles)
        for i, d in (added or {}).items():
            titles[i] = d["title"]
        return score2.Ids(titles, self.md)


def unit_of(op: str, qid: str) -> str:
    if op in ("create", "update", "delete", "control"):
        return qid.rsplit("-q", 1)[0]
    return qid


def questions(op: str, rec: dict[str, Any], ctx: Ctx) -> list[dict[str, Any]]:
    """Every answered question of a record with its verdict, gold and unit."""
    out = []
    if op in ("read", "control"):
        q = rec["task"]
        gold = q.get("gold") if op == "read" else q.get("control_gold")
        pairs = [(q, rec["agent"], gold, ctx.ids())]
    else:
        diff = rec.get("diff") or {}
        ids = ctx.ids(diff.get("added"))
        t = rec["task"]
        fus = rec["followups"]
        if op == "create":
            eff = score2.create_effects(t, diff)
            golds = [[{"span": f["answer"], "id": i} for i in e["docs"]]
                     for f, e in zip(t["facts"], eff["facts"], strict=True)]  # fmt: skip
        elif op == "update":
            docs = {**diff.get("changed", {}), **diff.get("added", {})}
            have = [
                c["id"]
                for c in t["copies"]
                if t["new_sentence"] in docs.get(c["id"], {}).get("body", "")
            ]
            golds = [[{"span": t["new_sentence"], "id": i} for i in have]]
        else:
            golds = [[]]
        if op == "create":  # follow-up i asks about fact i (run2 may have skipped dropped ones)
            by_id = {q["id"]: g for q, g in zip(t["followups"], golds, strict=True)}
            pairs = [(f["task"], f["agent"], by_id[f["task"]["id"]], ids) for f in fus]
        else:
            pairs = [(f["task"], f["agent"], golds[0], ids) for f in fus]
    for q, agent, gold, ids in pairs:
        if q["id"] in ctx.drop:
            continue
        sc = score2.score_answer(q, agent, ids, gold or [])
        out.append({"id": q["id"], "unit": unit_of(op, q["id"]),
                    "kind": q.get("subkind") or q.get("kind") or op, "q": q, "agent": agent,
                    "sc": sc, "correct": bool(sc and sc["correct"])})  # fmt: skip
    return out


def boot(xs: list[float], seed: int = 0) -> list[float] | None:
    if len(xs) < 2:
        return None
    rng = random.Random(seed)
    ms = sorted(sum(rng.choice(xs) for _ in xs) / len(xs) for _ in range(BOOT))
    return [round(ms[int(0.025 * BOOT)], 4), round(ms[int(0.975 * BOOT) - 1], 4)]


def unit_means(qs: list[dict]) -> dict[str, float]:
    by: dict[str, list[float]] = defaultdict(list)
    for q in qs:
        by[q["unit"]].append(float(q["correct"]))
    return {u: sum(v) / len(v) for u, v in sorted(by.items())}


def agent_cost(runs: list[dict]) -> dict[str, Any]:
    ok = [r for r in runs if r.get("status") == "ok"]
    return {
        "runs": len(runs),
        "failed": len(runs) - len(ok),
        "tokens_mean": mean([r["total_tokens"] for r in ok]),
        "tokens_median": statistics.median([r["total_tokens"] for r in ok]) if ok else None,
        "tokens_net_mean": mean([net_tokens(r) for r in ok]),
        "tool_calls_mean": mean([r["tool_calls"] for r in ok]),
        "step_cap_rate": mean([float(r["hit_step_cap"]) for r in ok]),
        "malformed_calls": sum(r.get("malformed_calls", 0) or 0 for r in ok),
        "unexposed_tool_calls": sum(unexposed_calls(r) for r in ok),
        "tool_schema_tokens": ok[0].get("tool_schema_tokens") if ok else None,
        "latency_s_mean": mean([r["latency_ms"] / 1000 for r in ok]),
    }


def cell(op: str, recs: list[dict], ctx: Ctx) -> dict[str, Any]:
    qs = [q for r in recs for q in questions(op, r, ctx)]
    um = unit_means(qs)
    c: dict[str, Any] = {
        "tasks": len(recs),
        "questions": len(qs),
        "correct": mean([float(q["correct"]) for q in qs]),
        "correct_task_mean": mean(list(um.values())),
        "correct_task_ci95": boot(list(um.values())),
        "by_kind": {},
    }
    kinds: dict[str, list[dict]] = defaultdict(list)
    for q in qs:
        kinds[q["kind"]].append(q)
    for k, items in sorted(kinds.items()):
        c["by_kind"][k] = {"n": len(items), "correct": mean([float(q["correct"]) for q in items])}
    scored = [q for q in qs if q["sc"]]
    c["abstained"] = mean([float(q["sc"]["abstained"]) for q in scored])
    c["stale_rate"] = mean([float(q["sc"]["stale"]) for q in scored if q["q"].get("stale_values")])
    c["zombie_rate"] = mean(
        [float(q["sc"]["zombie"]) for q in scored if q["q"].get("zombie_values")]
    )
    c["partial_aggregate"] = mean([q["sc"]["partial"] for q in scored if "partial" in q["sc"]])
    c["cite_precision"] = mean([q["sc"]["cite"]["precision"] for q in scored])
    c["cite_recall"] = mean([q["sc"]["cite"]["recall"] for q in scored])
    miss = [q for q in scored if not q["correct"] and q["sc"]["shown"] is not None]
    c["misses"] = {"n": len(miss), "shown": sum(bool(q["sc"]["shown"]) for q in miss),
                   "never_shown": sum(not q["sc"]["shown"] for q in miss),
                   "shown_then_abstained": sum(bool(q["sc"]["shown"] and q["sc"]["abstained"])
                                               for q in miss)}  # fmt: skip
    readers = [q["agent"] for q in qs]
    c["reader_cost"] = agent_cost(readers)
    if op in ("create", "update", "delete"):
        c["writer_cost"] = agent_cost([r["writer"] for r in recs])
        c["writer_zero_write_calls"] = sum(
            r["writer"].get("status") == "ok"
            and not any(x["name"] in ("create", "update", "delete", "write_note", "edit_note",
                                      "write_atom", "edit_node", "transclude", "write_file",
                                      "edit_file", "delete_note", "move_note")
                        for t in r["writer"]["turns"] for x in t["tool_calls"])
            for r in recs
        )  # fmt: skip
        effs = []
        for r in recs:
            d = r.get("diff")
            if d is None:
                continue
            fn = {"create": lambda t, d: score2.create_effects(t, d),
                  "update": lambda t, d: score2.update_effects(t, d, ctx.base),
                  "delete": lambda t, d: score2.delete_effects(t, d, ctx.base)}[op]  # fmt: skip
            effs.append(fn(r["task"], d))
        keys = {"create": ["landed_rate", "landed_relaxed_rate", "docs_added"],
                "update": ["copies_updated_rate", "copies_stale", "copies_deleted", "docs_added"],
                "delete": ["copies_removed_rate", "docs_deleted", "chars_lost"]}[op]  # fmt: skip
        c["effects"] = {k: mean([float(e[k]) for e in effs]) for k in keys}
        if op == "update":
            c["effects"]["all_copies_updated"] = mean(
                [float(e["copies_updated_rate"] == 1) for e in effs]
            )
        if op == "delete":
            c["effects"]["tasks_with_collateral"] = sum(bool(e["collateral_docs"]) for e in effs)
        c["wall_s_mean"] = mean([r["wall_s"] for r in recs])
    c["ingest"] = recs[0].get("ingest") if recs else None
    c["tool_mode"] = recs[0].get("tool_mode") if recs else None
    c["_units"] = um
    return c


def paired(ua: dict[str, float], ub: dict[str, float], seed: int = 0) -> dict[str, Any]:
    common = sorted(set(ua) & set(ub))
    diffs = [ua[u] - ub[u] for u in common]
    wins, losses = sum(d > 0 for d in diffs), sum(d < 0 for d in diffs)
    return {"units": len(common), "mean_diff": mean(diffs), "diff_ci95": boot(diffs, seed),
            "a_better": wins, "b_better": losses,
            "sign_p": binom_two_sided(min(wins, losses), wins + losses)}  # fmt: skip


WRITE_TOOLS = {"create", "update", "delete", "write_note", "edit_note", "delete_note",
               "move_note", "write_atom", "edit_node", "transclude", "write_file", "edit_file"}  # fmt: skip
GATE_MAX_FAILING = 0.20


def gate(recs_by_op: dict[str, list[dict]]) -> dict[str, Any]:
    """V6 pilot gate (as v1 M7): a task fails if any of its agent runs is not ok or ends with an
    empty final, or its writer made no write call; pass at <= 20% failing tasks per harness."""
    tasks = failing = zero = 0
    ids = []
    for op, recs in recs_by_op.items():
        for r in recs:
            runs = [r["agent"]] if op in ("read", "control") else (
                [r["writer"]] + [f["agent"] for f in r["followups"]])  # fmt: skip
            bad = any(x.get("status") != "ok" or not (x.get("final") or "").strip() for x in runs)
            if op not in ("read", "control"):
                w = r["writer"]
                if w.get("status") == "ok" and not any(
                    c["name"] in WRITE_TOOLS for t in w["turns"] for c in t["tool_calls"]
                ):
                    zero += 1
                    bad = True
            tasks += 1
            if bad:
                failing += 1
                ids.append(f"{op}/{r['task']['id']}")
    rate = round(failing / max(tasks, 1), 4)
    return {"tasks": tasks, "failing": failing, "failing_rate": rate, "failing_ids": ids,
            "writers_zero_write_calls": zero, "pass": rate <= GATE_MAX_FAILING}  # fmt: skip


def summarize(model: str) -> dict[str, Any]:
    ctx = Ctx()
    data = load(model)
    shas = sorted({r.get("probes_sha") for recs in data.values() for r in recs})
    if len(shas) > 1:
        raise SystemExit(f"records from different probe sets {shas}: analyse them separately")
    cells: dict[str, dict[str, dict[str, Any]]] = defaultdict(lambda: defaultdict(dict))
    for (h, rung, op), recs in data.items():
        cells[h][rung][op] = cell(op, recs, ctx)
    pairs: dict[str, Any] = {}
    harnesses = sorted(cells)
    for i, a in enumerate(harnesses):
        for b in harnesses[i + 1 :]:
            for rung in sorted(set(cells[a]) & set(cells[b]), key=rung_key):
                for op in OPS:
                    if op in cells[a][rung] and op in cells[b][rung]:
                        pairs[f"{a}-vs-{b}/{rung}/{op}"] = paired(
                            cells[a][rung][op]["_units"], cells[b][rung][op]["_units"]
                        )
    for h in cells.values():
        for r in h.values():
            for c in r.values():
                c.pop("_units")
    gates: dict[str, Any] = {}
    for (h, rung, op), recs in data.items():
        gates.setdefault(f"{h}/{rung}", {})[op] = recs
    return {"model": model, "probes_sha": shas[0] if shas else None,
            "cells": {h: dict(r) for h, r in sorted(cells.items())}, "paired": pairs,
            "gate": {k: gate(v) for k, v in sorted(gates.items())}}  # fmt: skip


def fmt(x: Any) -> str:
    if x is None:
        return "–"
    if isinstance(x, float):
        return f"{x:.2f}"
    return str(x)


def markdown(s: dict[str, Any]) -> str:
    lines = [f"# kb-io-bench v2 results: {s['model']}", ""]
    for h, rungs in s["cells"].items():
        rs = sorted(rungs, key=rung_key)
        lines += [f"## {h}", "", "| op / metric | " + " | ".join(rs) + " |",
                  "|---|" + "---:|" * len(rs)]  # fmt: skip
        rows = [("ingest wall_s", lambda c: (c.get("ingest") or {}).get("wall_s")),
                ("store MB", lambda c: round((c.get("ingest") or {}).get("store_bytes", 0) / 1e6, 1)
                 if c.get("ingest") else None)]  # fmt: skip
        for label, fn in rows:
            first = [next(iter(rungs[r].values())) for r in rs]
            lines.append(f"| {label} | " + " | ".join(fmt(fn(c)) for c in first) + " |")
        for op in OPS:
            if not any(op in rungs[r] for r in rs):
                continue
            metrics = ["correct_task_mean", "stale_rate", "zombie_rate", "cite_recall"]
            for m in metrics:
                vals = [rungs[r].get(op, {}).get(m) for r in rs]
                if any(v is not None for v in vals):
                    lines.append(f"| {op} {m} | " + " | ".join(fmt(v) for v in vals) + " |")
            ci = [rungs[r].get(op, {}).get("correct_task_ci95") for r in rs]
            lines.append(f"| {op} ci95 | " + " | ".join(
                f"[{fmt(c[0])}, {fmt(c[1])}]" if c else "–" for c in ci) + " |")  # fmt: skip
            miss = [rungs[r].get(op, {}).get("misses") for r in rs]
            lines.append(f"| {op} misses shown/never | " + " | ".join(
                f"{m['shown']}/{m['never_shown']}" if m else "–" for m in miss) + " |")  # fmt: skip
            eff = [rungs[r].get(op, {}).get("effects") or {} for r in rs]
            for k in sorted({k for e in eff for k in e}):
                lines.append(f"| {op} {k} | " + " | ".join(fmt(e.get(k)) for e in eff) + " |")
            for who in ("reader_cost", "writer_cost"):
                cs = [rungs[r].get(op, {}).get(who) for r in rs]
                if any(cs):
                    for m in ("tokens_mean", "tokens_net_mean", "tool_calls_mean", "failed",
                              "malformed_calls"):  # fmt: skip
                        lines.append(f"| {op} {who.split('_')[0]} {m} | " + " | ".join(
                            fmt(c.get(m)) if c else "–" for c in cs) + " |")  # fmt: skip
        lines.append("")
    lines += ["## Gate (fail > 20% of tasks = stop)", "",
              "| harness / rung | tasks | failing | rate | zero-write writers | pass |",
              "|---|---:|---:|---:|---:|---|"]  # fmt: skip
    for k, g in s["gate"].items():
        lines.append(f"| {k} | {g['tasks']} | {g['failing']} | {fmt(g['failing_rate'])} | "
                     f"{g['writers_zero_write_calls']} | {g['pass']} |")  # fmt: skip
    lines.append("")
    if s["paired"]:
        lines += ["## Paired (task-clustered)", "",
                  "| pair / rung / op | units | mean diff [95% CI] | a / b better | sign p |",
                  "|---|---:|---:|---:|---:|"]  # fmt: skip
        for k, v in s["paired"].items():
            ci = v["diff_ci95"] or ["–", "–"]
            lines.append(f"| {k} | {v['units']} | {fmt(v['mean_diff'])} [{fmt(ci[0])}, "
                         f"{fmt(ci[1])}] | {v['a_better']} / {v['b_better']} | {v['sign_p']} |")  # fmt: skip
    return "\n".join(lines) + "\n"


def per_question(model: str) -> str:
    ctx = Ctx()
    lines = [f"# v2 per-question evidence: {model}", ""]
    for (h, rung, op), recs in load(model).items():
        for r in recs:
            for q in questions(op, r, ctx):
                a = q["agent"]
                lines += [f"## {h} {rung} {op} {q['id']}: {'CORRECT' if q['correct'] else 'wrong'}",
                          "", f"**Q:** {q['q']['question']}", "", f"**Gold:** {q['q'].get('answer')}",
                          "", "> " + (a.get("final") or "(empty)").replace("\n", "\n> "), ""]  # fmt: skip
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="kbio analyze2")
    ap.add_argument("--model", default="gpt-oss:120b")
    a = ap.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    s = summarize(a.model)
    path = OUT / "summary.json"
    allsum = json.loads(path.read_text()) if path.exists() else {"runs": {}}
    allsum["runs"][a.model] = s
    path.write_text(json.dumps(allsum, indent=1, sort_keys=True) + "\n")
    (OUT / f"{model_dir(a.model)}.md").write_text(markdown(s))
    (probes.V2 / f"per-question-{model_dir(a.model)}.md").write_text(per_question(a.model))
    print((OUT / f"{model_dir(a.model)}.md").read_text())


if __name__ == "__main__":
    main()
