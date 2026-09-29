"""`python -m kbio.mab.analyze SCORES.jsonl`: the M13 analysis, as pre-registered in M13-PREREG.md.

Input: the benchmark-scored records (`mab_score.py`): one row per (cond, sub, qi) with
`substring_exact_match` (their Conflict Resolution metric). A FAILED call scores 0. Writes
results/m13-mab.md and results/m13-mab.json."""

from __future__ import annotations

import json
import random
import sys
from collections import defaultdict
from math import comb
from pathlib import Path
from typing import Any

from kbio.paths import RESULTS

CONDS = ["LC", "R-bm25", "R-facts", "R-rule", "K0", "K1", "K2"]
SIZES = ["6k", "32k", "64k", "262k"]
N_PER_SUB = 100
ALPHA = 0.05
BOOT = 2000
PILOT = {("sh", "6k"), ("mh", "6k")}  # first 20 questions of each were piloted (replayed)
PILOT_N = 20


def sign_one_sided(better: int, worse: int) -> float:
    n = better + worse
    return sum(comb(n, k) for k in range(better, n + 1)) / 2**n if n else 1.0


def sign_two_sided(better: int, worse: int) -> float:
    n = better + worse
    if not n:
        return 1.0
    k = min(better, worse)
    return min(1.0, 2 * sum(comb(n, i) for i in range(0, k + 1)) / 2**n)


def holm(ps: list[float]) -> list[float]:
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    adj, run = [0.0] * len(ps), 0.0
    for rank, i in enumerate(order):
        run = max(run, min(1.0, (len(ps) - rank) * ps[i]))
        adj[i] = run
    return adj


def boot_ci(d: list[float], seed: int = 0) -> list[float] | None:
    if len(d) < 2:
        return None
    rng = random.Random(seed)
    ms = sorted(sum(rng.choice(d) for _ in d) / len(d) for _ in range(BOOT))
    return [round(ms[int(0.025 * BOOT)], 4), round(ms[int(0.975 * BOOT) - 1], 4)]


def load(path: Path) -> dict[str, dict[tuple[str, str, int], float]]:
    """cond -> {(hop, size, qi): score}."""
    out: dict[str, dict[tuple[str, str, int], float]] = defaultdict(dict)
    for line in path.read_text().splitlines():
        r = json.loads(line)
        _f, hop, size = r["sub"].split("_")
        out[r["cond"]][(hop, size, r["qi"])] = float(r["substring_exact_match"])
    return out


def keys(hop: str, exclude_pilot: bool = False) -> list[tuple[str, str, int]]:
    return [(hop, s, q) for s in SIZES for q in range(N_PER_SUB)
            if not (exclude_pilot and (hop, s) in PILOT and q < PILOT_N)]  # fmt: skip


def paired(sa: dict, sb: dict, ks: list, sided: str = "one") -> dict[str, Any]:
    ks = [k for k in ks if k in sa and k in sb]
    d = [sa[k] - sb[k] for k in ks]
    better, worse = sum(x > 0 for x in d), sum(x < 0 for x in d)
    test = sign_one_sided if sided == "one" else sign_two_sided
    return {"n": len(ks), "acc_a": sum(sa[k] for k in ks) / len(ks) if ks else None,
            "acc_b": sum(sb[k] for k in ks) / len(ks) if ks else None,
            "diff": sum(d) / len(d) if d else None, "diff_ci95": boot_ci(d),
            "a_better": better, "b_better": worse, "p": test(better, worse),
            "sided": sided}  # fmt: skip


def summarize(scores: dict) -> dict[str, Any]:
    acc = {c: {f"{h}_{s}": (sum(v for k, v in scores[c].items() if k[:2] == (h, s)) /
                            max(1, sum(1 for k in scores[c] if k[:2] == (h, s))))
               for h in ("sh", "mh") for s in SIZES if any(k[:2] == (h, s) for k in scores[c])}
           for c in CONDS if c in scores}  # fmt: skip
    counts = {c: len(scores.get(c, {})) for c in CONDS}
    out: dict[str, Any] = {"accuracy": acc, "records": counts, "tests": {}}
    for ex in (False, True):
        tag = "_nopilot" if ex else ""
        sh, mh = keys("sh", ex), keys("mh", ex)
        g = {c: scores.get(c, {}) for c in CONDS}
        t = {
            "H1_K2_gt_Rbm25_sh": paired(g["K2"], g["R-bm25"], sh),
            "H2_K1_gt_K0_sh": paired(g["K1"], g["K0"], sh),
            "H3_K2_gt_LC_sh": paired(g["K2"], g["LC"], sh),
            "H1c_K2_vs_Rrule_sh": paired(g["K2"], g["R-rule"], sh, "two"),
            "N1_K0_vs_Rfacts_sh": paired(g["K0"], g["R-facts"], sh, "two"),
            "mh_K2_vs_Rbm25": paired(g["K2"], g["R-bm25"], mh),
            "mh_K2_vs_Rrule": paired(g["K2"], g["R-rule"], mh, "two"),
            "mh_K2_vs_LC": paired(g["K2"], g["LC"], mh),
        }
        fam = ["H1_K2_gt_Rbm25_sh", "H2_K1_gt_K0_sh", "H3_K2_gt_LC_sh"]
        for name, p in zip(fam, holm([t[n]["p"] for n in fam]), strict=True):
            t[name]["p_holm"] = p
        n1 = t["N1_K0_vs_Rfacts_sh"]["diff_ci95"]
        t["N1_K0_vs_Rfacts_sh"]["noninferior_at_-0.05"] = bool(n1 and n1[0] > -0.05)
        out["tests"]["all" + tag if not ex else "sensitivity" + tag] = t
    complete = all(counts[c] == 800 for c in CONDS)
    out["complete"] = complete
    t = out["tests"]["all"]
    if not complete:
        out["verdict"] = "INCOMPLETE: no claim."
    else:
        h1 = t["H1_K2_gt_Rbm25_sh"]["p_holm"] < ALPHA
        hc = t["H1c_K2_vs_Rrule_sh"]
        if not h1:
            out["verdict"] = "H1 not shown: akasha (K2) did not beat the benchmark's BM25 baseline."
        elif hc["p"] < ALPHA and hc["diff"] > 0:
            out["verdict"] = ("H1 shown, and K2 > R-rule: akasha beats plaintext even with the "
                              "same conflict rule.")  # fmt: skip
        elif hc["p"] < ALPHA and hc["diff"] < 0:
            out["verdict"] = ("H1 shown, but K2 < R-rule: the rule helps, and plaintext with the "
                              "same rule does better than akasha.")  # fmt: skip
        else:
            out["verdict"] = ("H1 shown; K2 ~ R-rule: akasha is a correct host for conflict "
                              "handling (with history and provenance), not better than plaintext "
                              "running the same rule.")  # fmt: skip
    return out


def fmt(x: Any) -> str:
    if x is None:
        return "–"
    if isinstance(x, float):
        return f"{x:.3f}" if abs(x) < 1 or x == 0 else f"{x:.2f}"
    return str(x)


def markdown(s: dict[str, Any]) -> str:
    L = ["# M13: akasha on MemoryAgentBench Conflict Resolution (gpt-oss:120b)", "",
         "Pre-registered in `M13-PREREG.md`. Metric: the benchmark's `substring_exact_match`.", "",
         f"**Verdict: {s['verdict']}**", "", "## Accuracy by sub-dataset", "",
         "| cond | " + " | ".join(f"{h}_{z}" for h in ("sh", "mh") for z in SIZES) + " | records |",
         "|---|" + "---:|" * 9]  # fmt: skip
    for c, a in s["accuracy"].items():
        cells = [fmt(a.get(f"{h}_{z}")) for h in ("sh", "mh") for z in SIZES]
        L.append(f"| {c} | " + " | ".join(cells) + f" | {s['records'][c]} |")
    for block, t in s["tests"].items():
        L += ["", f"## Tests ({block})", "",
              "| test | n | a | b | diff [95% CI] | a better / b better | p | Holm |",
              "|---|---:|---:|---:|---:|---:|---:|---:|"]  # fmt: skip
        for name, p in t.items():
            ci = p["diff_ci95"] or [None, None]
            L.append(f"| {name} ({p['sided']}-sided) | {p['n']} | {fmt(p['acc_a'])} | "
                     f"{fmt(p['acc_b'])} | {fmt(p['diff'])} [{fmt(ci[0])}, {fmt(ci[1])}] | "
                     f"{p['a_better']} / {p['b_better']} | {fmt(p['p'])} | "
                     f"{fmt(p.get('p_holm'))} |")  # fmt: skip
    return "\n".join(L) + "\n"


def main() -> None:
    s = summarize(load(Path(sys.argv[1])))
    (RESULTS / "m13-mab.json").write_text(json.dumps(s, indent=1, sort_keys=True) + "\n")
    (RESULTS / "m13-mab.md").write_text(markdown(s))
    print(markdown(s))


if __name__ == "__main__":
    main()
