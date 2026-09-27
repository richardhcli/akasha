"""Aggregate results/cache into results/summary.json and a per-question REPORT.md
(gold answer + source, each condition's retrieved and cited sources, answer, verdict)."""

from __future__ import annotations

import json
from math import comb

from common import DATA, EXP

CONDS = ["raw", "clean", "clean-atoms", "akasha", "akasha-1hop"]


def mean(xs: list) -> float | None:
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 3) if xs else None


def sign_test(wins: int, losses: int) -> float:
    n = wins + losses
    if n == 0:
        return 1.0
    k = min(wins, losses)
    return round(min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2**n), 3)


def main() -> None:
    questions = json.loads((DATA / "questions.json").read_text())
    retrieval = json.loads((DATA / "results" / "retrieval.json").read_text())
    res: dict[str, dict[str, dict]] = {}
    for d in sorted((DATA / "results" / "cache").iterdir()):
        res[d.name] = {p.stem: json.loads(p.read_text()) for p in d.glob("*.json")}
    # Judge audit: results/audit-overrides.json holds hand grades where the judge was wrong (it
    # gave 2 to answers claiming the information was unavailable, and 0 to some partial answers).
    overrides = json.loads((EXP / "results" / "audit-overrides.json").read_text())
    corrections = 0
    for key, by_q in res.items():
        for qid, r in by_q.items():
            r["judge_score"] = r["score"]
            o = overrides.get(f"{key}/{qid}")
            if o is not None and o["score"] != r["score"]:
                r["score"] = o["score"]
                r["audit"] = o["why"]
                corrections += 1
    budgets = sorted({int(k.split("@")[1]) for k in res})
    summary: dict = {"retrieval_only": {}, "llm": {}, "paired_vs_raw": {}}
    chunk_map = {
        key: {r["qid"]: r["chunks"] for r in recs} for key, recs in retrieval["runs"].items()
    }
    for key, recs in retrieval["runs"].items():
        ans = [r for r in recs if r["recall"] is not None]
        summary["retrieval_only"][key] = {
            "gold_span_recall": mean([r["recall"] for r in ans]),
            "all_spans_hit": mean([float(all(r["gold_hits"])) for r in ans]),
            "context_tokens": mean([r["context_tokens"] for r in recs]),
            "chunks": mean([len(r["chunks"]) for r in recs]),
        }
    for key, by_q in res.items():
        rs = list(by_q.values())
        ans = [r for r in rs if r["type"] != "unanswerable"]
        un = [r for r in rs if r["type"] == "unanswerable"]
        summary["llm"][key] = {
            "n": len(rs),
            "score_mean_0to2": mean([r["score"] for r in rs]),
            "judge_score_uncorrected": mean([r["judge_score"] for r in rs]),
            "correct_rate": mean([float(r["score"] == 2) for r in rs if r["score"] is not None]),
            "answerable_score": mean([r["score"] for r in ans]),
            "single_score": mean([r["score"] for r in ans if r["type"] == "single"]),
            "multi_score": mean([r["score"] for r in ans if r["type"] == "multi"]),
            "unanswerable_abstained": mean([float(r["score"] == 2) for r in un]),
            "cited_gold_span_recall": mean([r["cited_gold_span_recall"] for r in ans]),
            "cited_source_precision": mean([r["cited_source_precision"] for r in ans]),
            "answers_with_citation": mean(
                [float(bool(r["cited"])) for r in ans if not r["not_found"]]
            ),
            "tokens_per_cited_chunk": mean(
                [chunk_map[key][r["qid"]][x["n"] - 1]["tokens"] for r in ans for x in r["cited"]]
            ),
            "prompt_tokens": mean([r["prompt_tokens"] for r in rs]),
            "completion_tokens": mean([r["completion_tokens"] for r in rs]),
            "context_tokens": mean([r["context_tokens"] for r in rs]),
            "score_per_1k_prompt_tokens": None,
        }
        s = summary["llm"][key]
        if s["score_mean_0to2"] is not None and s["prompt_tokens"]:
            s["score_per_1k_prompt_tokens"] = round(
                s["score_mean_0to2"] / (s["prompt_tokens"] / 1000), 3
            )
    for b in budgets:
        base = res.get(f"raw@{b}", {})
        for c in CONDS[1:]:
            other = res.get(f"{c}@{b}", {})
            wins = losses = ties = 0
            for qid, r in other.items():
                if qid in base and r["score"] is not None and base[qid]["score"] is not None:
                    d = r["score"] - base[qid]["score"]
                    wins, losses, ties = wins + (d > 0), losses + (d < 0), ties + (d == 0)
            summary["paired_vs_raw"][f"{c}@{b}"] = {
                "wins": wins,
                "losses": losses,
                "ties": ties,
                "sign_test_p": sign_test(wins, losses),
            }
    summary["judge_corrections"] = corrections
    (EXP / "results" / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")

    # ---- REPORT.md: per-question evidence so every verdict can be checked by hand
    lines = [
        "# Per-question evidence",
        "",
        "Each block: the question, the gold answer and gold source(s), "
        "then per condition the verdict "
        "(judge score 0-2), the answer as given, the sources it cited, and what was retrieved.",
        "",
    ]
    for b in budgets:
        lines += [f"## Budget {b} context tokens", ""]
        for q in questions:
            lines += [
                f"### {q['id']} ({q['type']}): {q['question']}",
                "",
                f"- **Gold answer:** {q['answer']}",
            ]
            for g in q["gold"]:
                lines.append(f"- **Gold source:** `{g['source']}` — “{g['span']}”")
            lines.append("")
            for c in CONDS:
                r = res.get(f"{c}@{b}", {}).get(q["id"])
                if not r:
                    continue
                cites = ", ".join(f"S{x['n']}=`{x['cite']}`" for x in r["cited"]) or "(none)"
                lines += [
                    f"**{c}** — score **{r['score']}** (judge {r['judge_score']}) · "
                    f"prompt {r['prompt_tokens']} tok · "
                    f"gold in context: {r['context_gold_hits']} · "
                    f"cited-gold-span recall: {r['cited_gold_span_recall']}",
                    "",
                    "> " + r["answer"].replace("\n", "\n> "),
                    "",
                    f"- cited: {cites}",
                    f"- judge: {r['judge_reason']}"
                    + (f" — **audit override:** {r['audit']}" if r.get("audit") else ""),
                    "- retrieved: " + "; ".join(f"`{x}`" for x in r["retrieved"]),
                ]
                chunks = {k + 1: c for k, c in enumerate(chunk_map[f"{c}@{b}"][q["id"]])}
                for x in r["cited"]:
                    body = chunks[x["n"]]["body"].strip().replace("\n", "\n  > ")
                    lines.append(f"- cited text S{x['n']} (`{x['cite']}`):\n  > {body}")
                lines.append("")
    (DATA / "results" / "per-question.md").write_text("\n".join(lines) + "\n")
    print(json.dumps(summary["llm"], indent=1))
    print(json.dumps(summary["paired_vs_raw"], indent=1))


if __name__ == "__main__":
    main()
