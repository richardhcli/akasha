"""`kbio analyze`: offline, deterministic aggregation of the result JSONs.

Reads data/.../results/<model>/<tier>/<cond>/*.json only (never a vault or the endpoint) and
writes results/summary.json + results/<model>-<tier>.md (tracked; ids and numbers only, no note
text) and data/.../per-question-<model>-<tier>.md (local; every answer next to its gold)."""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from collections import defaultdict
from typing import Any

from kbio import score
from kbio.paths import DATA, RESULTS
from kbio.run import RESULTS as RAW
from kbio.run import model_dir

ORDER = ["A", "B", "C", "Cp", "Ap", "CB"]
PAIRS = [("A", "B"), ("B", "C"), ("A", "C"), ("A", "Cp"), ("Ap", "Cp")]
BOOT = 2000
# Every harness's mutating tools; each harness exposes only its own, so the union is safe.
WRITE_TOOLS = {"write_atom", "transclude", "edit_node", "write_note", "edit_note", "write_file",
               "edit_file"}  # fmt: skip
GATE_MAX_FAILING = 0.20
UNKNOWN_TOOL = "ERROR: ValueError: unknown tool"
# question kind -> family for the per-family paired tests (Review item 4)
FAMILY = {"perturbed": "read-perturbed", "perturbed-multihop": "read-perturbed",
          "unperturbed": "read-unperturbed", "personal": "read-personal",
          "aggregate": "read-aggregate", "unanswerable": "read-unanswerable",
          "write-followup": "write", "update-followup": "update"}  # fmt: skip


def load(model: str, tier: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    base = RAW / model_dir(model) / tier
    for cond in ORDER:
        d = base / cond
        if d.exists():
            out[cond] = [json.loads(p.read_text()) for p in sorted(d.glob("*.json"))]
    return out


def questions(rec: dict) -> list[dict]:
    """Every answered question in a record: the READ task itself, or WRITE/UPDATE follow-ups."""
    if rec["family"] == "read":
        return [{"id": rec["task"]["id"], "kind": rec["task"]["kind"], "task": rec["task"],
                 "agent": rec["agent"], "score": score.rescore(rec["task"], rec.get("score")),
                 "judge": rec.get("judge")}]  # fmt: skip
    kind = "write-followup" if rec["family"] == "write" else "update-followup"
    return [
        {
            "id": f["task"]["id"],
            "kind": kind,
            "task": f["task"],
            "agent": f["agent"],
            "score": score.rescore(f["task"], f.get("score")),
            "judge": f.get("judge"),
        }  # fmt: skip
        for f in rec.get("followups", [])
    ]


def correct(q: dict) -> bool:
    """Fully correct: exact answer value present (code), or abstained on an unanswerable, or the
    UPDATE follow-up quotes the new definition; judge 2 as the fallback for free-text answers."""
    sc, j = q["score"], q["judge"]
    if sc is None or q["agent"].get("status") != "ok":
        return False
    if q["kind"] == "unanswerable":
        return bool(sc["abstained"])
    if q["kind"] == "update-followup":
        return bool(sc.get("update_correct"))
    if q["task"].get("answer_values"):
        return bool(sc["exact_hit"]) and not sc["abstained"]
    return bool(j and j.get("score") == 2)


def mean(xs: list[float]) -> float | None:
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 4) if xs else None


def boot_ci(xs: list[float], seed: int = 0) -> list[float] | None:
    xs = [x for x in xs if x is not None]
    if len(xs) < 2:
        return None
    rng = random.Random(seed)
    ms = sorted(sum(rng.choice(xs) for _ in xs) / len(xs) for _ in range(BOOT))
    return [round(ms[int(0.025 * BOOT)], 4), round(ms[int(0.975 * BOOT) - 1], 4)]


def binom_two_sided(k: int, n: int) -> float:
    if n == 0:
        return 1.0
    p = sum(math.comb(n, i) for i in range(0, min(k, n - k) + 1)) / 2**n
    return round(min(1.0, 2 * p), 4)


def net_tokens(run: dict) -> int | None:
    """Total tokens minus the tool schema resent with every tool-bearing request (Review item 5).
    Requests without tools (the forced final at the step cap, the empty-final re-ask) are not
    charged; the schema count is tiktoken's, so this is an estimate."""
    if run.get("status") != "ok":
        return None
    req = (
        len(run["turns"]) - int(bool(run.get("hit_step_cap"))) - int(bool(run.get("reasked_empty")))
    )
    return run["total_tokens"] - (run.get("tool_schema_tokens") or 0) * max(req, 0)


def unexposed_calls(run: dict) -> int:
    """Calls to tools the harness does not expose (gpt-oss priors, e.g. bm's find_in_note)."""
    return sum(c["result"].startswith(UNKNOWN_TOOL) for t in run.get("turns", [])
               for c in t["tool_calls"])  # fmt: skip


def shown(agent: dict, values: list[str]) -> bool:
    """Did any answer value appear in a tool result as shown to the agent (after truncation)?"""
    vs = [score.norm(v) for v in values if v]
    return any(v in score.norm(c["result"]) for t in agent.get("turns", [])
               for c in t["tool_calls"] for v in vs)  # fmt: skip


def miss_split(qs: list[dict]) -> dict[str, Any]:
    """Review item 6a: every wrong answer to a question with an exact value, split by whether the
    value was ever shown to the agent; plus shown-then-abstained ("top hit had it, NOT FOUND")."""
    shown_n = never = shown_abst = 0
    for q in qs:
        vals = q["task"].get("answer_values") or []
        if not vals or q["kind"] == "unanswerable" or correct(q):
            continue
        if q["agent"].get("status") != "ok":
            continue
        if shown(q["agent"], vals):
            shown_n += 1
            shown_abst += bool(q["score"] and q["score"].get("abstained"))
        else:
            never += 1
    return {"misses": shown_n + never, "shown": shown_n, "never_shown": never,
            "shown_then_abstained": shown_abst}  # fmt: skip


def agent_stats(runs: list[dict]) -> dict[str, Any]:
    ok = [r for r in runs if r.get("status") == "ok"]
    calls = [c for r in ok for t in r["turns"] for c in t["tool_calls"]]
    return {
        "runs": len(runs),
        "failed": sum(r.get("status") != "ok" for r in runs),
        "failed_rate": round(sum(r.get("status") != "ok" for r in runs) / max(len(runs), 1), 4),
        "tokens_mean": mean([r["total_tokens"] for r in ok]),
        "tokens_median": statistics.median([r["total_tokens"] for r in ok]) if ok else None,
        "tool_calls_mean": mean([r["tool_calls"] for r in ok]),
        "steps_mean": mean([r["steps"] for r in ok]),
        "step_cap_rate": mean([float(r["hit_step_cap"]) for r in ok]),
        "empty_final_rate": mean([float(not r["final"].strip()) for r in ok]),
        "tool_error_rate": round(
            sum(c["result"].startswith("ERROR:") for c in calls) / len(calls), 4
        )
        if calls
        else None,
        "latency_s_mean": mean([r["latency_ms"] / 1000 for r in ok]),
        "tool_schema_tokens": ok[0]["tool_schema_tokens"] if ok else None,
        "tokens_net_mean": mean([net_tokens(r) for r in ok]),
        "unexposed_tool_calls": sum(unexposed_calls(r) for r in ok),
        **(window_stats(runs) if any("context_window" in r for r in runs) else {}),
    }


def window_stats(runs: list[dict]) -> dict[str, Any]:
    """M11 context-window runs: how often the window bound, and the largest prompt the gateway
    actually counted (must be <= window - max_tokens)."""
    pts = [t["prompt_tokens"] for r in runs for t in r.get("turns", []) if t.get("usage_source") == "api"]  # fmt: skip
    return {
        "context_window": runs[0].get("context_window"),
        "context_overflow_rate": mean([float(r.get("status") == "context_overflow") for r in runs]),
        "evicted_run_rate": mean([float(r.get("evictions", 0) > 0) for r in runs]),
        "max_api_prompt_tokens": max(pts) if pts else None,
    }


def write_calls(run: dict) -> int:
    return sum(c["name"] in WRITE_TOOLS for t in run.get("turns", []) for c in t["tool_calls"])


def gate(recs: list[dict]) -> dict[str, Any]:
    """PLAN §8 M7 pilot gate. A task fails if any agent run in it is not ok or ends with an empty
    final, or (WRITE/UPDATE) its writer made 0 write calls. Reported alongside: writers with a
    non-empty final and no effect (0 facts landed / 0 copies updated), split into those that made
    0 write calls ("claimed DONE but wrote nothing") and those whose write calls had no effect,
    and malformed tool calls over all runs."""
    failing, zero_writes, claimed_nothing, no_effect, malformed = [], 0, 0, 0, 0
    for r in recs:
        runs = [r["agent"]] if r["family"] == "read" else (
            [r["writer"]] + [f["agent"] for f in r.get("followups", [])])  # fmt: skip
        malformed += sum(x.get("malformed_calls", 0) or 0 for x in runs)
        bad = any(x.get("status") != "ok" or not (x.get("final") or "").strip() for x in runs)
        if r["family"] != "read":
            w = r["writer"]
            if w.get("status") == "ok" and write_calls(w) == 0:
                zero_writes += 1
                bad = True
            effect = (r.get("write_score") or {}).get("landed_rate") if r["family"] == "write" \
                else (r.get("update_score") or {}).get("copies_updated")  # fmt: skip
            if w.get("status") == "ok" and (w.get("final") or "").strip() and not effect:
                if write_calls(w) == 0:
                    claimed_nothing += 1
                else:
                    no_effect += 1
        if bad:
            failing.append(r["task"]["id"])
    rate = round(len(failing) / max(len(recs), 1), 4)
    return {"tasks": len(recs), "failing": len(failing), "failing_rate": rate,
            "failing_ids": failing, "writers_zero_write_calls": zero_writes,
            "claimed_done_wrote_nothing": claimed_nothing, "writes_no_effect": no_effect,
            "malformed_calls": malformed,
            "pass": rate <= GATE_MAX_FAILING}  # fmt: skip


def landed_relaxed(rec: dict) -> float | None:
    """Review item 3: a WRITE fact counts as landed if its answer newly appears in a file whose
    path, title or paragraph (for an edited file: the diff hunk) also names the fact's marker.
    basic-memory writers often file an entity note named after the marker, with the answer in a
    bullet, which the strict rule (marker and answer in one paragraph) misses. Strict implies
    relaxed."""
    diff = rec.get("diff")
    facts = (rec.get("write_score") or {}).get("facts")
    if not diff or not facts:
        return None
    hits = []
    for f, strict in zip(rec["task"]["facts"], facts, strict=True):
        mk, an = (score.norm(x) for x in score.fact_marker(f["sentence"]))
        ok = bool(strict["landed"])
        for path, content in (diff.get("added") or {}).items():
            text = score.norm(content)
            title = next((ln for ln in content.splitlines() if ln.startswith(("title:", "# "))), "")
            pars = [score.norm(p) for p in content.split("\n\n")]
            if an in text and (mk in score.norm(path) or mk in score.norm(title)
                               or any(mk in p and an in p for p in pars)):  # fmt: skip
                ok = True
        for path, ud in (diff.get("changed") or {}).items():
            for hunk in ud.split("\n@@")[1:]:
                lines = hunk.splitlines()[1:]
                plus = score.norm(" ".join(ln[1:] for ln in lines if ln.startswith("+")))
                minus = score.norm(" ".join(ln[1:] for ln in lines if ln.startswith("-")))
                ctx = score.norm(" ".join(ln[1:] for ln in lines if not ln.startswith("-")))
                if an in plus and an not in minus and (mk in score.norm(path) or mk in ctx):
                    ok = True
        hits.append(float(ok))
    return mean(hits)


def update_split(qs: list[dict]) -> dict[str, int]:
    """Review item 2: wrong UPDATE follow-ups quoting the old phrase (stale) vs another
    definition (concept notes carry the user's own `Definition:` line)."""
    wrong = [q for q in qs if not correct(q)]
    stale = sum(bool(q["score"] and q["score"].get("update_stale")) for q in wrong)
    return {"wrong": len(wrong), "stale": stale, "other_definition": len(wrong) - stale}


def unit_of(q: dict) -> str:
    """Clustering unit: the task (WRITE/UPDATE follow-ups of one task form one unit)."""
    return q["id"].rsplit("-q", 1)[0] if q["kind"] in ("write-followup", "update-followup") \
        else q["id"]  # fmt: skip


def clustered_pair(qa: dict[str, dict], qb: dict[str, dict], seed: int = 0) -> dict[str, Any]:
    """Task-level paired comparison: per unit, the mean correct over its questions; sign test on
    units that differ; bootstrap CI of the mean unit difference (resampling units)."""
    ua: dict[str, list[float]] = defaultdict(list)
    ub: dict[str, list[float]] = defaultdict(list)
    for qid in sorted(set(qa) & set(qb)):
        u = unit_of(qa[qid])
        ua[u].append(float(correct(qa[qid])))
        ub[u].append(float(correct(qb[qid])))
    diffs = [sum(ua[u]) / len(ua[u]) - sum(ub[u]) / len(ub[u]) for u in sorted(ua)]
    wins, losses = sum(d > 0 for d in diffs), sum(d < 0 for d in diffs)
    ci = None
    if len(diffs) >= 2:
        rng = random.Random(seed)
        ms = sorted(sum(rng.choice(diffs) for _ in diffs) / len(diffs) for _ in range(BOOT))
        ci = [round(ms[int(0.025 * BOOT)], 4), round(ms[int(0.975 * BOOT) - 1], 4)]
    return {"units": len(diffs), "questions": sum(len(v) for v in ua.values()),
            "mean_diff": mean(diffs), "diff_ci95": ci, "a_better": wins, "b_better": losses,
            "sign_p": binom_two_sided(min(wins, losses), wins + losses)}  # fmt: skip


def summarize(model: str, tier: str) -> dict[str, Any]:
    data = load(model, tier)
    out: dict[str, Any] = {"model": model, "tier": tier, "conditions": {}, "paired": {},
                           "paired_by_family": {}}  # fmt: skip
    qcorrect: dict[str, dict[str, bool]] = defaultdict(dict)
    qobj: dict[str, dict[str, dict]] = defaultdict(dict)
    qscore: dict[str, dict[str, int]] = defaultdict(dict)
    for cond, recs in data.items():
        c: dict[str, Any] = {}
        qs = [q for r in recs for q in questions(r)]
        for q in qs:
            qcorrect[cond][q["id"]] = correct(q)
            qobj[cond][q["id"]] = q
            if q["judge"] and q["judge"].get("score") is not None:
                qscore[cond][q["id"]] = q["judge"]["score"]
        by_kind: dict[str, list[dict]] = defaultdict(list)
        for q in qs:
            by_kind[q["kind"]].append(q)
        c["questions"] = {}
        for kind, items in sorted(by_kind.items()):
            cs = [float(correct(q)) for q in items]
            c["questions"][kind] = {
                "n": len(items),
                "correct": mean(cs),
                "correct_ci95": boot_ci(cs),
                "judge_mean": mean(
                    [
                        q["judge"]["score"]
                        for q in items
                        if q["judge"] and q["judge"].get("score") is not None
                    ]
                ),  # fmt: skip
                "judge_full": mean(
                    [
                        float(q["judge"]["score"] == 2)
                        for q in items
                        if q["judge"] and q["judge"].get("score") is not None
                    ]
                ),  # fmt: skip
                "abstained": mean([float(q["score"]["abstained"]) for q in items if q["score"]]),
                "cited_gold_span_recall": mean(
                    [q["score"].get("cited_gold_span_recall") for q in items if q["score"]]
                ),  # fmt: skip
                "cited_source_precision": mean(
                    [q["score"].get("cited_source_precision") for q in items if q["score"]]
                ),  # fmt: skip
                "stale_read": mean(
                    [float(bool(q["score"].get("update_stale"))) for q in items if q["score"]]
                )
                if kind == "update-followup"
                else None,
                "tokens_mean": mean(
                    [
                        q["agent"].get("total_tokens")
                        for q in items
                        if q["agent"].get("status") == "ok"
                    ]
                ),  # fmt: skip
            }
        pert = [q for q in qs if q["kind"] in ("perturbed", "perturbed-multihop")]
        c["headline_perturbed_correct"] = mean([float(correct(q)) for q in pert])
        c["headline_perturbed_ci95"] = boot_ci([float(correct(q)) for q in pert])
        c["misses_perturbed"] = miss_split(pert)
        c["misses_all"] = miss_split(qs)
        reads = [r for r in recs if r["family"] == "read"]
        c["read_agent"] = agent_stats([r["agent"] for r in reads])
        writes = [r for r in recs if r["family"] == "write"]
        if writes:
            c["write"] = {
                "tasks": len(writes),
                "writer": agent_stats([r["writer"] for r in writes]),
                "landed": mean([r["write_score"]["landed_rate"] for r in writes]),
                "landed_relaxed": mean([landed_relaxed(r) for r in writes]),
                "linked": mean([r["write_score"]["linked_rate"] for r in writes]),
                "duplicates_per_task": mean([r["write_score"]["duplicates"] for r in writes]),
                "violations_after": mean(
                    [
                        (r["status_after"] or {}).get("violations", 0)
                        + (r["status_after"] or {}).get("conflicts", 0)
                        for r in writes
                    ]
                )
                if writes[0].get("status_after")
                else None,  # fmt: skip
                "reviews_after": mean(
                    [(r["status_after"] or {}).get("reviews_open", 0) for r in writes]
                )
                if writes[0].get("status_after")
                else None,  # fmt: skip
                "followup_correct": mean([float(correct(q)) for r in writes for q in questions(r)]),
                "tokens_per_task": mean(
                    [
                        r["writer"].get("total_tokens", 0)
                        + sum(f["agent"].get("total_tokens", 0) or 0 for f in r["followups"])
                        for r in writes
                    ]
                ),  # fmt: skip
            }
        updates = [r for r in recs if r["family"] == "update"]
        if updates:
            c["update"] = {
                "tasks": len(updates),
                "writer": agent_stats([r["writer"] for r in updates]),
                "copies_updated_rate": mean(
                    [r["update_score"]["copies_updated_rate"] for r in updates]
                ),  # fmt: skip
                "copies_stale_mean": mean([r["update_score"]["copies_stale"] for r in updates]),
                "copies_total_mean": mean([r["update_score"]["copies_total"] for r in updates]),
                "all_copies_updated": mean(
                    [float(r["update_score"]["copies_updated_rate"] == 1.0) for r in updates]
                ),  # fmt: skip
                "followup_correct": mean(
                    [float(correct(q)) for r in updates for q in questions(r)]
                ),
                "followup_wrong_split": update_split([q for r in updates for q in questions(r)]),
                "stale_read_rate": mean(
                    [
                        float(bool(q["score"] and q["score"].get("update_stale")))
                        for r in updates
                        for q in questions(r)
                    ]
                ),  # fmt: skip
                "violations_after": mean(
                    [
                        (r["status_after"] or {}).get("violations", 0)
                        + (r["status_after"] or {}).get("conflicts", 0)
                        for r in updates
                    ]
                )
                if updates[0].get("status_after")
                else None,  # fmt: skip
                "tokens_per_task": mean(
                    [
                        r["writer"].get("total_tokens", 0)
                        + sum(f["agent"].get("total_tokens", 0) or 0 for f in r["followups"])
                        for r in updates
                    ]
                ),  # fmt: skip
            }
        all_runs = (
            [r["agent"] for r in reads]
            + [r["writer"] for r in writes + updates]
            + [f["agent"] for r in writes + updates for f in r["followups"]]
        )
        c["all_agents"] = agent_stats(all_runs)
        c["tasks"] = len(recs)
        c["tasks_failed"] = sum(
            (r.get("agent") or r.get("writer") or {}).get("status") != "ok" for r in recs
        )
        c["gate"] = gate(recs)
        out["conditions"][cond] = c
    for a, b in PAIRS:
        if a not in qcorrect or b not in qcorrect:
            continue
        common = sorted(set(qcorrect[a]) & set(qcorrect[b]))
        a_only = sum(qcorrect[a][q] and not qcorrect[b][q] for q in common)
        b_only = sum(qcorrect[b][q] and not qcorrect[a][q] for q in common)
        sc = sorted(set(qscore[a]) & set(qscore[b]))
        wins = sum(qscore[a][q] > qscore[b][q] for q in sc)
        losses = sum(qscore[a][q] < qscore[b][q] for q in sc)
        out["paired"][f"{a}-vs-{b}"] = {
            "questions": len(common),
            "correct_a": sum(qcorrect[a][q] for q in common),
            "correct_b": sum(qcorrect[b][q] for q in common),
            "mcnemar_a_only": a_only,
            "mcnemar_b_only": b_only,
            "mcnemar_p": binom_two_sided(min(a_only, b_only), a_only + b_only),
            "judge_wins": wins,
            "judge_losses": losses,
            "sign_p": binom_two_sided(min(wins, losses), wins + losses),
        }
        fams = sorted({FAMILY.get(q["kind"], q["kind"]) for q in qobj[a].values()})
        for fam in fams:
            qa = {k: q for k, q in qobj[a].items() if FAMILY.get(q["kind"], q["kind"]) == fam}
            qb = {k: q for k, q in qobj[b].items() if k in qa}
            if qb:
                out["paired_by_family"].setdefault(f"{a}-vs-{b}", {})[fam] = clustered_pair(qa, qb)
    return out


def fmt(x: Any) -> str:
    if x is None:
        return "–"
    if isinstance(x, float):
        return f"{x:.2f}"
    return str(x)


def markdown(s: dict[str, Any]) -> str:
    conds = [c for c in ORDER if c in s["conditions"]]
    lines = [f"# kb-io-bench results: {s['model']}, tier {s['tier']}", ""]
    kinds = sorted({k for c in conds for k in s["conditions"][c]["questions"]})
    lines += ["## Correct rate by question kind", "",
              "| kind | " + " | ".join(conds) + " |", "|---|" + "---:|" * len(conds)]  # fmt: skip
    for k in kinds:
        row = [fmt(s["conditions"][c]["questions"].get(k, {}).get("correct")) for c in conds]
        n = max(s["conditions"][c]["questions"].get(k, {}).get("n", 0) for c in conds)
        lines.append(f"| {k} (n={n}) | " + " | ".join(row) + " |")
    lines.append(
        "| **perturbed (headline)** | "
        + " | ".join(fmt(s["conditions"][c]["headline_perturbed_correct"]) for c in conds)
        + " |"
    )
    lines += ["", "## Cost and reliability (all agent runs)", "",
              "| metric | " + " | ".join(conds) + " |", "|---|" + "---:|" * len(conds)]  # fmt: skip
    for m in ("runs", "failed_rate", "tokens_mean", "tokens_median", "tool_calls_mean",
              "step_cap_rate", "empty_final_rate", "tool_error_rate", "tool_schema_tokens",
              "tokens_net_mean", "unexposed_tool_calls", "context_window", "context_overflow_rate",
              "evicted_run_rate", "max_api_prompt_tokens"):  # fmt: skip
        if all(m not in s["conditions"][c]["all_agents"] for c in conds):
            continue
        lines.append(f"| {m} | " + " | ".join(fmt(s["conditions"][c]["all_agents"].get(m)) for c in conds) + " |")  # fmt: skip
    lines += ["", "## Pilot gate (fail > 20% of tasks = stop)", "",
              "| metric | " + " | ".join(conds) + " |", "|---|" + "---:|" * len(conds)]  # fmt: skip
    for m in ("tasks", "failing", "failing_rate", "writers_zero_write_calls",
              "claimed_done_wrote_nothing", "writes_no_effect", "malformed_calls", "pass"):  # fmt: skip
        lines.append(f"| {m} | " + " | ".join(fmt(s["conditions"][c]["gate"].get(m)) for c in conds) + " |")  # fmt: skip
    wc = [c for c in conds if "write" in s["conditions"][c]]
    if wc:
        lines += ["", "## WRITE", "", "| metric | " + " | ".join(wc) + " |", "|---|" + "---:|" * len(wc)]  # fmt: skip
        for m in ("tasks", "landed", "landed_relaxed", "linked", "duplicates_per_task", "violations_after",
                  "reviews_after", "followup_correct", "tokens_per_task"):  # fmt: skip
            lines.append(f"| {m} | " + " | ".join(fmt(s["conditions"][c]["write"].get(m)) for c in wc) + " |")  # fmt: skip
    uc = [c for c in conds if "update" in s["conditions"][c]]
    if uc:
        lines += ["", "## UPDATE", "", "| metric | " + " | ".join(uc) + " |", "|---|" + "---:|" * len(uc)]  # fmt: skip
        for m in ("tasks", "copies_updated_rate", "all_copies_updated", "copies_stale_mean",
                  "copies_total_mean", "followup_correct", "stale_read_rate", "violations_after",
                  "tokens_per_task"):  # fmt: skip
            lines.append(f"| {m} | " + " | ".join(fmt(s["conditions"][c]["update"].get(m)) for c in uc) + " |")  # fmt: skip
    lines += ["", "## Misses: was the answer value ever shown to the agent?", "",
              "| scope | " + " | ".join(conds) + " |", "|---|" + "---:|" * len(conds)]  # fmt: skip
    for scope in ("misses_perturbed", "misses_all"):
        cell = [s["conditions"][c][scope] for c in conds]
        lines.append(f"| {scope} (shown / never / shown+abstained) | " + " | ".join(
            f"{x['shown']} / {x['never_shown']} / {x['shown_then_abstained']}" for x in cell) + " |")  # fmt: skip
    if uc:
        lines.append("| update follow-ups wrong (stale / other definition) | " + " | ".join(
            f"{s['conditions'][c]['update']['followup_wrong_split']['stale']} / "
            f"{s['conditions'][c]['update']['followup_wrong_split']['other_definition']}"
            if c in uc else "–" for c in conds) + " |")  # fmt: skip
    if s.get("paired_by_family"):
        lines += ["", "## Paired tests per family (task-clustered; the headline tests)", "",
                  "| pair | family | units | mean diff a-b [95% CI] | a better / b better | sign p |",
                  "|---|---|---:|---:|---:|---:|"]  # fmt: skip
        for k, fams in s["paired_by_family"].items():
            for fam, v in fams.items():
                ci = v["diff_ci95"] or ["–", "–"]
                lines.append(f"| {k} | {fam} | {v['units']} | {fmt(v['mean_diff'])} "
                             f"[{fmt(ci[0])}, {fmt(ci[1])}] | {v['a_better']} / {v['b_better']} "
                             f"| {v['sign_p']} |")  # fmt: skip
    if s["paired"]:
        lines += ["", "## Paired tests (all questions pooled; clustered, so p is overstated)", "",
                  "| pair | n | correct a / b | McNemar a-only / b-only | p | judge wins / losses | sign p |",
                  "|---|---:|---:|---:|---:|---:|---:|"]  # fmt: skip
        for k, v in s["paired"].items():
            lines.append(f"| {k} | {v['questions']} | {v['correct_a']} / {v['correct_b']} | "
                         f"{v['mcnemar_a_only']} / {v['mcnemar_b_only']} | {v['mcnemar_p']} | "
                         f"{v['judge_wins']} / {v['judge_losses']} | {v['sign_p']} |")  # fmt: skip
    return "\n".join(lines) + "\n"


def per_question(model: str, tier: str) -> str:
    data = load(model, tier)
    by_q: dict[str, dict[str, dict]] = defaultdict(dict)
    for cond, recs in data.items():
        for r in recs:
            for q in questions(r):
                by_q[q["id"]][cond] = q
    lines = [f"# Per-question evidence: {model}, tier {tier}", ""]
    for qid in sorted(by_q):
        any_q = next(iter(by_q[qid].values()))
        t = any_q["task"]
        lines += [f"## {qid} ({any_q['kind']})", "", f"**Q:** {t['question']}", "",
                  f"**Gold:** {t.get('answer')}", ""]  # fmt: skip
        for g in t.get("gold", []):
            lines.append(f"- gold span [{g['source']}]: {g['span'][:300]}")
        for cond in ORDER:
            q = by_q[qid].get(cond)
            if not q:
                continue
            sc = q["score"] or {}
            j = q["judge"] or {}
            lines += ["", f"### {cond}: {'CORRECT' if correct(q) else 'wrong'}; judge "
                      f"{j.get('score')} ({j.get('reason', '')}); tokens "
                      f"{q['agent'].get('total_tokens')}", "",
                      "> " + (q["agent"].get("final") or "(empty)").replace("\n", "\n> ")]  # fmt: skip
            for c in sc.get("citations", []):
                lines.append(f"- cited {c['source']} -> {c['files']} ({c['how']})")
        lines.append("")
    return "\n".join(lines) + "\n"


def calls_per_hour() -> dict[str, Any]:
    log = DATA / "logs" / "llm-calls.log"
    if not log.exists():
        return {}
    ts = [float(line.split("\t")[0]) for line in log.read_text().splitlines() if line.strip()]
    if len(ts) < 2:
        return {}
    hours = (ts[-1] - ts[0]) / 3600
    return {
        "calls": len(ts),
        "span_hours": round(hours, 3),
        "calls_per_hour": round(len(ts) / hours, 1),
    }


def main() -> None:
    ap = argparse.ArgumentParser(prog="kbio analyze")
    ap.add_argument("--model", default="gpt-oss:120b")
    ap.add_argument("--tiers", default="S")
    args = ap.parse_args()
    RESULTS.mkdir(exist_ok=True)
    summary_path = RESULTS / "summary.json"
    summary = json.loads(summary_path.read_text()) if summary_path.exists() else {"runs": {}}
    for tier in args.tiers.split(","):
        s = summarize(args.model, tier)
        summary["runs"][f"{args.model}/{tier}"] = s
        (RESULTS / f"{model_dir(args.model)}-{tier}.md").write_text(markdown(s))
        (DATA / f"per-question-{model_dir(args.model)}-{tier}.md").write_text(
            per_question(args.model, tier)
        )
    summary["runs"] = dict(sorted(summary["runs"].items()))
    summary_path.write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n")
    print((RESULTS / f"{model_dir(args.model)}-{args.tiers.split(',')[-1]}.md").read_text())


if __name__ == "__main__":
    main()
