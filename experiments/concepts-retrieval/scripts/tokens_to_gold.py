"""'Fewer tokens' measured directly: with no budget cap, how many context tokens does each
condition's ranked list consume before every gold span of a question has been retrieved?"""

from __future__ import annotations

import json
import statistics

from common import DATA, EXP
from retrieve import BM25, CONDITIONS, load_units, retrieve, terms

CAP = 30000


def main() -> None:
    questions = [q for q in json.loads((DATA / "questions.json").read_text()) if q["gold"]]
    out = {}
    for cond in CONDITIONS:
        units = load_units(cond)
        index = BM25([terms(u["index_text"]) for u in units])
        per_q = {}
        for q in questions:
            chunks = retrieve(cond, units, index, q["question"], CAP)
            used, need = 0, None
            remaining = {g["span"] for g in q["gold"]}
            for c in chunks:
                used += c["tokens"]
                remaining -= {s for s in remaining if s in c["body"]}
                if not remaining:
                    need = used
                    break
            per_q[q["id"]] = need
        found = [v for v in per_q.values() if v is not None]
        out[cond] = {
            "median_tokens_to_all_gold": statistics.median(found) if found else None,
            "mean_tokens_to_all_gold": round(statistics.mean(found)) if found else None,
            "questions_never_fully_found_in_top60": sum(v is None for v in per_q.values()),
            "per_question": per_q,
        }
        print(
            f"{cond:<13} median {out[cond]['median_tokens_to_all_gold']:>6}  "
            f"mean {out[cond]['mean_tokens_to_all_gold']:>6}"
            f"  not found {out[cond]['questions_never_fully_found_in_top60']}"
        )
    # Compare conditions on the same questions: those every condition eventually retrieves.
    common = [q for q in out["raw"]["per_question"] if all(out[c]["per_question"][q] for c in out)]
    for cond, v in out.items():
        vals = [v["per_question"][q] for q in common]
        v["common_set"] = {
            "n": len(common),
            "median": statistics.median(vals),
            "mean": round(statistics.mean(vals)),
        }
        print(
            f"{cond:<13} common set (n={len(common)}): median {statistics.median(vals):>6}  "
            f"mean {round(statistics.mean(vals)):>5}"
        )
    (EXP / "results" / "tokens_to_gold.json").write_text(json.dumps(out, indent=1) + "\n")


if __name__ == "__main__":
    main()
