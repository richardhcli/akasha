"""`python -m kbio.m12`: the M12 analysis, exactly as pre-registered in M12-PREREG.md (with
Amendment 1). Writes results/m12-S.md and results/m12-S.json.

Strata (harness fixed, knowledge base varied): basic-memory B (akasha) vs C (plain), and file tools
Ap (akasha) vs Cp (plain). A is descriptive only."""

from __future__ import annotations

import json
import random
from math import comb
from typing import Any

from kbio.analyze import clustered_pair, fmt, load, mean, questions
from kbio.paths import DATA, RESULTS

MODEL = "gpt-oss:120b"
UPD = "S-kb"
STRATA = [("basic-memory", "B", "C"), ("files", "Ap", "Cp")]
RW = {"B": "S", "C": "S", "Ap": "S-kbrw", "Cp": "S-kbrw"}  # READ/WRITE records (Amendment 1)
ALPHA = 0.05
BOOT = 2000


def sign_one_sided(better: int, worse: int) -> float:
    """P(X >= better) for X ~ Bin(better + worse, 1/2): exact McNemar, one-sided."""
    n = better + worse
    return sum(comb(n, k) for k in range(better, n + 1)) / 2**n if n else 1.0


def holm(ps: list[float]) -> list[float]:
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    adj, run = [0.0] * len(ps), 0.0
    for rank, i in enumerate(order):
        run = max(run, min(1.0, (len(ps) - rank) * ps[i]))
        adj[i] = run
    return adj


def boot_diff(a: list[float], b: list[float], seed: int = 0) -> list[float] | None:
    """95% bootstrap CI of mean(a - b) over paired tasks."""
    d = [x - y for x, y in zip(a, b, strict=True)]
    if len(d) < 2:
        return None
    rng = random.Random(seed)
    ms = sorted(sum(rng.choice(d) for _ in d) / len(d) for _ in range(BOOT))
    return [round(ms[int(0.025 * BOOT)], 4), round(ms[int(0.975 * BOOT) - 1], 4)]


def consistent(rec: dict) -> bool:
    """Primary endpoint: every scored copy has the new sentence and none the old; a FAILED
    writer counts as not consistent."""
    if (rec.get("writer") or {}).get("status") == "FAILED":
        return False
    return (rec.get("update_score") or {}).get("copies_updated_rate") == 1.0


def stale(rec: dict) -> bool:
    return any((f.get("score") or {}).get("update_stale") for f in rec.get("followups", []))


def fu_correct(rec: dict) -> bool:
    fs = rec.get("followups", [])
    return bool(fs) and all((f.get("score") or {}).get("update_correct") for f in fs)


def paired(ra: dict[str, dict], rb: dict[str, dict], ids: list[str], fn: Any) -> dict[str, Any]:
    """akasha (a) vs plain (b) on a binary per-task outcome where True is good."""
    ids = [i for i in ids if i in ra and i in rb]
    xa = [float(fn(ra[i])) for i in ids]
    xb = [float(fn(rb[i])) for i in ids]
    better = sum(x > y for x, y in zip(xa, xb, strict=True))
    worse = sum(x < y for x, y in zip(xa, xb, strict=True))
    return {
        "n": len(ids),
        "rate_akasha": mean(xa),
        "rate_plain": mean(xb),
        "diff": mean([x - y for x, y in zip(xa, xb, strict=True)]),
        "diff_ci95": boot_diff(xa, xb),
        "akasha_better": better,
        "plain_better": worse,
        "p_one_sided": sign_one_sided(better, worse),
    }


def by_id(label: str, cond: str) -> dict[str, dict]:
    return {r["task"]["id"]: r for r in load(MODEL, label).get(cond, [])}


def qdict(label: str, cond: str, family: str) -> dict[str, dict]:
    recs = [r for r in load(MODEL, label).get(cond, []) if r["family"] == family]
    return {q["id"]: q for r in recs for q in questions(r)}


def summarize() -> dict[str, Any]:
    tasks = json.loads((DATA / "tasks" / "S-kb.json").read_text())["tasks"]
    mc = [t["id"] for t in tasks if t["kind"] == "multi-copy"]
    unseen = [t["id"] for t in tasks if t["kind"] == "multi-copy" and not t.get("seen_in_v1")]
    noprefix = [
        t["id"]
        for t in tasks
        if t["kind"] == "multi-copy" and not any(c.startswith("(") for c in t["copies"])
    ]
    sc = [t["id"] for t in tasks if t["kind"] == "single-copy"]
    out: dict[str, Any] = {
        "n_multi": len(mc),
        "n_unseen": len(unseen),
        "n_noprefix": len(noprefix),
        "n_single": len(sc),
        "strata": {},
    }
    for name, ak, pl in STRATA:
        ra, rb = by_id(UPD, ak), by_id(UPD, pl)
        s: dict[str, Any] = {"akasha": ak, "plain": pl, "records": {ak: len(ra), pl: len(rb)}}
        s["H1"] = paired(ra, rb, mc, consistent)
        s["H1_unseen"] = paired(ra, rb, unseen, consistent)
        s["H1_noprefix"] = paired(ra, rb, noprefix, consistent)
        s["H2"] = paired(ra, rb, mc, lambda r: not stale(r))
        s["H2"]["stale_rate_akasha"] = 1 - s["H2"]["rate_akasha"] if s["H2"]["n"] else None
        s["H2"]["stale_rate_plain"] = 1 - s["H2"]["rate_plain"] if s["H2"]["n"] else None
        s["S"] = paired(ra, rb, sc, lambda r: consistent(r) and fu_correct(r))
        s["N_read"] = clustered_pair(qdict(RW[ak], ak, "read"), qdict(RW[pl], pl, "read"))
        s["N_write"] = clustered_pair(qdict(RW[ak], ak, "write"), qdict(RW[pl], pl, "write"))
        s["cost"] = {
            c: {
                "tokens_per_task": mean(
                    [
                        r["writer"].get("total_tokens", 0)
                        + sum(f["agent"].get("total_tokens", 0) or 0 for f in r["followups"])
                        for r in recs.values()
                    ]
                ),
                "writer_tool_calls": mean(
                    [r["writer"].get("tool_calls", 0) for r in recs.values()]
                ),
            }
            for c, recs in ((ak, ra), (pl, rb))
        }
        out["strata"][name] = s
    h1 = [out["strata"][n]["H1"]["p_one_sided"] for n, _, _ in STRATA]
    h2 = [out["strata"][n]["H2"]["p_one_sided"] for n, _, _ in STRATA]
    for (n, _, _), a1, a2 in zip(STRATA, holm(h1), holm(h2), strict=True):
        out["strata"][n]["H1"]["p_holm"] = a1
        out["strata"][n]["H2"]["p_holm"] = a2
    ra = by_id(UPD, "A")
    out["A_descriptive"] = {
        "records": len(ra),
        "consistent_rate_multi": mean([float(consistent(ra[i])) for i in mc if i in ra]),
        "stale_rate_multi": mean([float(stale(ra[i])) for i in mc if i in ra]),
    }
    rej = {
        n: out["strata"][n]["H1"]["p_holm"] < ALPHA
        and out["strata"][n]["H1_unseen"]["p_one_sided"] < ALPHA
        and out["strata"][n]["H1_noprefix"]["p_one_sided"] < ALPHA
        for n, _, _ in STRATA
    }
    rw = {
        c: sum(r["family"] in ("read", "write") for r in load(MODEL, RW[c]).get(c, [])) for c in RW
    }
    complete = all(
        v == len(mc) + len(sc) for n, _, _ in STRATA for v in out["strata"][n]["records"].values()
    ) and all(v == 75 for v in rw.values())  # 60 READ + 15 WRITE per condition
    out["rw_records"] = rw
    out["complete"] = complete
    if not complete:
        out["verdict"] = "INCOMPLETE: interim validity look only; no claim."
    elif all(rej.values()):
        out["verdict"] = "PROVEN (pre-registered wording): replicated with both harnesses."
    elif any(rej.values()):
        yes = [n for n, v in rej.items() if v]
        no = [n for n, v in rej.items() if not v]
        out["verdict"] = f"Shown with {', '.join(yes)}, not replicated with {', '.join(no)}."
    else:
        out["verdict"] = "No KB effect shown."
    return out


def markdown(s: dict[str, Any]) -> str:
    L = [
        "# M12: akasha KB vs plain vault (tier S, gpt-oss:120b)",
        "",
        "Pre-registered in `M12-PREREG.md` (with Amendment 1). Harness fixed within each "
        "stratum; only the knowledge base differs.",
        "",
        f"**Verdict: {s['verdict']}**",
        "",
        f"Tasks: {s['n_multi']} multi-copy ({s['n_unseen']} unseen in v1, {s['n_noprefix']} "
        f"without a '(n) ' folder copy), {s['n_single']} single-copy.",
        "",
    ]

    def row(label: str, p: dict[str, Any], extra: str = "") -> str:
        ci = p.get("diff_ci95") or [None, None]
        return (
            f"| {label} | {p['n']} | {fmt(p['rate_akasha'])} | {fmt(p['rate_plain'])} | "
            f"{fmt(p['diff'])} [{fmt(ci[0])}, {fmt(ci[1])}] | {p['akasha_better']} / "
            f"{p['plain_better']} | {fmt(p['p_one_sided'])}{extra} |"
        )

    for name, s2 in s["strata"].items():
        ak, pl = s2["akasha"], s2["plain"]
        L += [
            f"## Stratum: {name} tools ({ak} = akasha KB, {pl} = plain vault)",
            "",
            f"Records: {s2['records']}",
            "",
            f"| test | tasks | {ak} | {pl} | diff [95% CI] | akasha / plain better "
            "| p (one-sided) |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
        L.append(
            row("H1 all copies consistent (primary)", s2["H1"], f"; Holm {fmt(s2['H1']['p_holm'])}")
        )
        L.append(row("H1-unseen (20 new tasks)", s2["H1_unseen"]))
        L.append(row("H1-noprefix", s2["H1_noprefix"]))
        L.append(row("H2 no stale follow-up read", s2["H2"], f"; Holm {fmt(s2['H2']['p_holm'])}"))
        L.append(row("S single-copy control (correct)", s2["S"], " (prediction: CI low > -0.15)"))
        for k, lab in (("N_read", "N-read"), ("N_write", "N-write")):
            p = s2[k]
            ci = p.get("diff_ci95") or [None, None]
            L.append(
                f"| {lab} (task-clustered, margin -0.10) | {p['units']} | – | – | "
                f"{fmt(p['mean_diff'])} [{fmt(ci[0])}, {fmt(ci[1])}] | {p['a_better']} / "
                f"{p['b_better']} | – |"
            )
        L += [
            "",
            "Cost per UPDATE task: "
            + "; ".join(
                f"{c} {fmt(v['tokens_per_task'])} tokens, "
                f"{fmt(v['writer_tool_calls'])} writer calls"
                for c, v in s2["cost"].items()
            ),
            "",
        ]
    a = s["A_descriptive"]
    L += [
        "## A (akasha harness + akasha KB), descriptive only",
        "",
        f"records {a['records']}, all copies consistent (multi-copy) "
        f"{fmt(a['consistent_rate_multi'])}, "
        f"stale follow-up rate {fmt(a['stale_rate_multi'])}",
        "",
    ]
    return "\n".join(L) + "\n"


def main() -> None:
    s = summarize()
    (RESULTS / "m12-S.json").write_text(json.dumps(s, indent=1, sort_keys=True) + "\n")
    (RESULTS / "m12-S.md").write_text(markdown(s))
    print(markdown(s))


if __name__ == "__main__":
    main()
