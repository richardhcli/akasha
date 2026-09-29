"""`python -m kbio.window`: the M11 context-window sweep report (tier S, gpt-oss:120b).

Labels: the unwindowed tier-S run (baseline), `@ctx128k` (the unbound control: the same code path,
a fresh sample), and the windows `@ctx16k` / `@ctx8k`. For each condition: headline metrics per
label, how often the window bound, and the largest prompt the gateway counted (must be <= window
- 2000). Paired tests (task-clustered) compare each window with the control, or with the
baseline where the control is not run yet (A replays the baseline from cache until the first
eviction; for B/C the baseline comparison is marked interim). Writes results/context-window-S.md."""

from __future__ import annotations

from typing import Any

from kbio.agent import parse_model
from kbio.analyze import ORDER, clustered_pair, correct, fmt, load, mean, questions, summarize
from kbio.paths import RESULTS

BASE = "gpt-oss:120b"
LABELS = [BASE, f"{BASE}@ctx128k", f"{BASE}@ctx16k", f"{BASE}@ctx8k"]
TIER = "S"
METRICS = [
    ("records", "tasks run (of 90; CB not run)"),
    ("all_correct", "correct, questions answered under every label"),
    ("perturbed", "READ perturbed correct (headline)"),
    ("write_followup", "WRITE follow-up correct"),
    ("update_all_copies", "UPDATE all copies updated"),
    ("update_stale", "UPDATE stale-read rate"),
    ("tokens_net", "tokens per run, net of schema"),
    ("evicted_runs", "runs with an eviction"),
    ("overflow", "runs ended by context_overflow"),
    ("max_prompt", "max gateway prompt tokens"),
]


def short(label: str) -> str:
    return "baseline" if label == BASE else label.split("@", 1)[1]


def qmap(label: str) -> dict[str, dict[str, dict]]:
    data = load(label, TIER)
    return {c: {q["id"]: q for r in recs for q in questions(r)} for c, recs in data.items()}


def row(s: dict[str, Any], qs: dict[str, dict], cond: str, common: set[str]) -> dict[str, Any]:
    c = s["conditions"][cond]
    ag = c["all_agents"]
    return {
        "records": c["tasks"],
        "all_correct": mean([float(correct(qs[q])) for q in common]),
        "perturbed": c.get("headline_perturbed_correct"),
        "write_followup": (c.get("write") or {}).get("followup_correct"),
        "update_all_copies": (c.get("update") or {}).get("all_copies_updated"),
        "update_stale": (c.get("update") or {}).get("stale_read_rate"),
        "tokens_net": ag.get("tokens_net_mean"),
        "evicted_runs": ag.get("evicted_run_rate"),
        "overflow": ag.get("context_overflow_rate"),
        "max_prompt": ag.get("max_api_prompt_tokens"),
    }


def main() -> None:
    have = [lb for lb in LABELS if load(lb, TIER)]
    sums = {lb: summarize(lb, TIER) for lb in have}
    qs = {lb: qmap(lb) for lb in have}
    lines = ["# M11: context-window sweep, tier S, gpt-oss:120b", "",
             "Same model, same tasks; a client-side window evicts the oldest tool results "
             "(`kbio/agent.py`). `ctx128k` is the unbound control (a fresh sample of B/C, whose "
             "basic-memory results carry fresh UUIDs and never replay from cache).",
             ""]  # fmt: skip
    bad = []
    for cond in [c for c in ORDER if c != "CB"]:
        labs = [lb for lb in have if cond in sums[lb]["conditions"]]
        if not labs:
            continue
        common = set.intersection(*(set(qs[lb][cond]) for lb in labs))
        rows = {lb: row(sums[lb], qs[lb][cond], cond, common) for lb in labs}
        lines += [f"## {cond}", "", "| metric | " + " | ".join(short(lb) for lb in labs) + " |",
                  "|---|" + "---:|" * len(labs)]  # fmt: skip
        for key, name in METRICS:
            lines.append(f"| {name} | " + " | ".join(fmt(rows[lb][key]) for lb in labs) + " |")
        for lb in labs:
            w = parse_model(lb)[1]
            mp = rows[lb]["max_prompt"]
            if w and w < 131072 and mp is not None and mp > w - 2000:
                bad.append(f"{cond} {short(lb)}: max gateway prompt {mp} > {w - 2000}")
        lines.append("")
        ref = f"{BASE}@ctx128k" if f"{BASE}@ctx128k" in labs else BASE
        tests = [lb for lb in labs if lb not in (BASE, ref)]
        if tests:
            note = ""
            if ref == BASE and cond in ("B", "C"):
                note = (" **Interim: rerun noise is not separated from the window effect until "
                        "`ctx128k` runs for this condition.**")  # fmt: skip
            lines += [f"Paired vs `{short(ref)}` (task-clustered, common questions).{note}", "",
                      "| window | units | mean diff [95% CI] | better / worse | sign p |",
                      "|---|---:|---:|---:|---:|"]  # fmt: skip
            for lb in tests:
                p = clustered_pair(qs[lb][cond], qs[ref][cond])
                ci = p["diff_ci95"] or [None, None]
                lines.append(f"| {short(lb)} | {p['units']} | {fmt(p['mean_diff'])} "
                             f"[{fmt(ci[0])}, {fmt(ci[1])}] | {p['a_better']} / {p['b_better']} "
                             f"| {fmt(p['sign_p'])} |")  # fmt: skip
            lines.append("")
    lines += ["## A minus other conditions, all questions correct", "",
              "| label | " + " | ".join(f"A - {c}" for c in ("B", "C", "Cp")) + " |",
              "|---|---:|---:|---:|"]  # fmt: skip
    for lb in have:
        cells = []
        for c in ("B", "C", "Cp"):
            if "A" in qs[lb] and c in qs[lb]:
                common = set(qs[lb]["A"]) & set(qs[lb][c])
                qa, qc = qs[lb]["A"], qs[lb][c]
                d = mean([float(correct(qa[q])) - float(correct(qc[q])) for q in common])
                cells.append(f"{fmt(d)} (n={len(common)})")
            else:
                cells.append("–")
        lines.append(f"| {short(lb)} | " + " | ".join(cells) + " |")
    lines += ["", "## Window check", "", "\n".join(f"- VIOLATION {b}" for b in bad) or
              "- every windowed request's gateway prompt count <= window - 2000"]  # fmt: skip
    out = RESULTS / f"context-window-{TIER}.md"
    out.write_text("\n".join(lines) + "\n")
    print(out.read_text())


if __name__ == "__main__":
    main()
