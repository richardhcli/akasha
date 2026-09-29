"""v2 scoring (V5): black-box first, from the stored answer and tool results only.

Per answered question (READ, control, and the follow-ups of CREATE / UPDATE / DELETE):
- `correct`: single / multi-hop / CREATE follow-up = an answer value present and no abstention;
  aggregate (`answer_mode: all`) = every value present (`partial` = the share present);
  unanswerable = abstained; UPDATE follow-up = new value present, old value absent (`stale` if
  the old one is present); DELETE follow-up = abstained and the retracted value absent (`zombie`
  if present); counterfactual = planted year present and the real year absent (`stale`).
- citations: each `SOURCES:` item resolves to document ids (exact id, md file name, or a unique
  exact title); precision against every gold id (hop pages included), recall over distinct gold
  spans (a fact stored in 3 copies is covered by citing any one of them).
- `shown`: whether any answer value appeared in a tool result as shown to the agent.
White-box effects (optional, from the writer's export diff): CREATE landed / landed_relaxed,
UPDATE copies updated / stale, DELETE copies removed and collateral (text lost beyond the
retracted sentence, or whole documents deleted).
The text rules are v1's (`score.norm`, `score.split_answer`, `score.NOT_FOUND`), so v1 records
rescored here give the same verdicts (`regression_v1`)."""

from __future__ import annotations

import json
import re
from typing import Any

from kbio import score

COLLATERAL_CHARS = 40  # lost text beyond the retracted sentence that counts as collateral


def abstained(nb: str) -> bool:
    return score.norm(score.NOT_FOUND) in nb or "not found in knowledge base" in nb


def verdict(q: dict[str, Any], body: str) -> dict[str, Any]:
    """Text-only verdict for one question (`q` is the task or follow-up dict)."""
    nb = score.norm(body)
    vals = q.get("answer_values") or []
    present = [v for v in vals if score.norm(v) in nb]
    stale = [v for v in q.get("stale_values") or [] if score.norm(v) in nb]
    zombie = [v for v in q.get("zombie_values") or [] if score.norm(v) in nb]
    ab = abstained(nb)
    out: dict[str, Any] = {"abstained": ab, "present": present, "stale": bool(stale),
                           "zombie": bool(zombie)}  # fmt: skip
    if q.get("zombie_values") is not None or (not vals and q.get("kind") == "unanswerable"):
        out["correct"] = ab and not zombie
    elif q.get("answer_mode") == "all":
        out["partial"] = len(present) / len(vals)
        out["correct"] = len(present) == len(vals) and not ab
    elif vals:
        out["correct"] = bool(present) and not ab and not stale
    else:
        out["correct"] = None  # judge-only (v1 personal / aggregate); not code-scored
    return out


class Ids:
    """Resolves cited sources to document ids: exact id, md file name, or a unique exact title."""

    def __init__(self, titles: dict[str, str], md: dict[str, str]) -> None:
        self.ids = set(titles)
        self.by_md = {v.lower(): k for k, v in md.items()}
        by_title: dict[str, set[str]] = {}
        for i, t in titles.items():
            by_title.setdefault(t.lower(), set()).add(i)
        self.by_title = {t: next(iter(s)) for t, s in by_title.items() if len(s) == 1}

    def resolve(self, src: str) -> str | None:
        s = src.strip().strip("<>`*[]()\"'").strip()
        for pre in ("id ", "id:", "doc ", "document ", "#"):
            if s.lower().startswith(pre):
                s = s[len(pre) :].strip()
        if s in self.ids or re.fullmatch(r"\d+|n\d+", s):  # crud-kb: KILT ids, minted n<k>
            return s
        name = s.split("/")[-1]
        if name.lower() in self.by_md or (name + ".md").lower() in self.by_md:
            return self.by_md.get(name.lower()) or self.by_md[(name + ".md").lower()]
        return self.by_title.get(name.removesuffix(".md").lower())


def citations(q: dict[str, Any], final: str, ids: Ids, gold: list[dict]) -> dict[str, Any]:
    _body, cites = score.split_answer(final)
    got = [ids.resolve(src) for src, _q in cites]
    good = [g for g in got if g]
    gold_ids = {g["id"] for g in gold}
    spans: dict[str, set[str]] = {}
    for g in gold:
        if g.get("role") not in ("hop", "clue"):
            spans.setdefault(g["span"], set()).add(g["id"])
    return {
        "n_citations": len(cites),
        "resolved": good,
        # every citation counts, resolved or not, so harnesses whose ids resolve differently
        # (numeric KILT ids vs md names) are scored alike
        "precision": sum(g in gold_ids for g in good) / len(cites) if cites and gold_ids else None,
        "recall": sum(bool(s & set(good)) for s in spans.values()) / len(spans) if spans else None,
    }


def shown(agent: dict[str, Any], values: list[str]) -> bool:
    vs = [score.norm(v) for v in values if v]
    return any(v in score.norm(c["result"]) for t in agent.get("turns", [])
               for c in t["tool_calls"] for v in vs)  # fmt: skip


def score_answer(q: dict[str, Any], agent: dict[str, Any], ids: Ids | None,
                 gold: list[dict] | None = None) -> dict[str, Any] | None:  # fmt: skip
    if agent.get("status") != "ok":
        return None
    final = agent.get("final") or ""
    body, _c = score.split_answer(final)
    out = verdict(q, body)
    vals = (q.get("answer_values") or []) + (q.get("zombie_values") or [])
    out["shown"] = shown(agent, vals) if vals else None
    if ids is not None:
        out["cite"] = citations(q, final, ids, gold if gold is not None else q.get("gold", []))
    return out


# ---- white-box effects of a writer (from run2's export diff) --------------------------------


def paragraphs(text: str) -> list[str]:
    return [score.norm(p) for p in text.split("\n\n")]


def create_effects(task: dict[str, Any], diff: dict[str, Any]) -> dict[str, Any]:
    docs = {**diff.get("changed", {}), **diff.get("added", {})}
    facts = []
    for f in task["facts"]:
        mk, an = (score.norm(x) for x in score.fact_marker(f["sentence"]))
        strict = [
            i for i, d in docs.items() if any(mk in p and an in p for p in paragraphs(d["body"]))
        ]
        relaxed = [i for i, d in docs.items() if an in score.norm(d["body"])
                   and (mk in score.norm(d["title"]) or mk in score.norm(d["body"]))]  # fmt: skip
        facts.append({"answer": f["answer"], "landed": bool(strict), "landed_relaxed": bool(relaxed),
                      "docs": sorted(set(strict) | set(relaxed)),
                      "on_topic_page": f["page"] in strict})  # fmt: skip
    n = max(len(facts), 1)
    return {"facts": facts, "landed_rate": sum(f["landed"] for f in facts) / n,
            "landed_relaxed_rate": sum(f["landed_relaxed"] for f in facts) / n,
            "docs_added": len(diff.get("added", {})), "docs_removed": len(diff.get("removed", []))}  # fmt: skip


def copy_texts(
    task: dict[str, Any], diff: dict[str, Any], base: dict[str, str]
) -> dict[str, str | None]:
    """After-text of every recorded copy page (None = the document was deleted)."""
    out: dict[str, str | None] = {}
    for c in task["copies"]:
        i = c["id"]
        if i in diff.get("removed", []):
            out[i] = None
        else:
            out[i] = diff.get("changed", {}).get(i, {}).get("body", base[i])
    return out


def update_effects(
    task: dict[str, Any], diff: dict[str, Any], base: dict[str, str]
) -> dict[str, Any]:
    after = copy_texts(task, diff, base)
    new, old = task["new_sentence"], task["old_sentence"]
    updated = [i for i, t in after.items() if t is not None and new in t and old not in t]
    stale = [i for i, t in after.items() if t is not None and old in t]
    elsewhere = [i for i, d in {**diff.get("changed", {}), **diff.get("added", {})}.items()
                 if i not in after and new in d["body"]]  # fmt: skip
    return {"copies_total": len(after), "copies_updated": len(updated), "copies_stale": len(stale),
            "copies_deleted": sum(t is None for t in after.values()),
            "copies_updated_rate": len(updated) / len(after), "new_elsewhere": elsewhere,
            "docs_added": len(diff.get("added", {})), "docs_removed": len(diff.get("removed", []))}  # fmt: skip


def delete_effects(
    task: dict[str, Any], diff: dict[str, Any], base: dict[str, str]
) -> dict[str, Any]:
    after = copy_texts(task, diff, base)
    s = task["sentence"]
    removed = [i for i, t in after.items() if t is None or s not in t]
    lost = {}
    for i, t in after.items():
        if t is None:
            lost[i] = len(base[i]) - len(s)
        else:
            lost[i] = max(0, len(base[i]) - len(t) - (len(s) if s not in t else 0))
    other_changed = [i for i in diff.get("changed", {}) if i not in after]
    other_removed = [i for i in diff.get("removed", []) if i not in after]
    collateral = [i for i, n in lost.items() if n > COLLATERAL_CHARS] + other_removed
    return {"copies_total": len(after), "copies_removed": len(removed),
            "copies_removed_rate": len(removed) / len(after),
            "docs_deleted": sum(t is None for t in after.values()) + len(other_removed),
            "collateral_docs": sorted(collateral), "chars_lost": sum(lost.values()),
            "other_docs_changed": other_changed}  # fmt: skip


# ---- regression: v1 records rescored with the v2 verdicts -------------------------------------


def regression_v1(model: str = "gpt-oss:120b", tier: str = "S") -> dict[str, Any]:
    """Every code-scored v1 question (exact values; unanswerable) gets the same correct/incorrect
    verdict from `verdict` as from `analyze.correct`. UPDATE follow-ups (v1 phrase-in-context
    rule) and judge-only kinds are outside v2's rules and are counted, not compared."""
    from kbio import analyze

    data = analyze.load(model, tier)
    same = diff = skipped = 0
    diffs: list[str] = []
    for cond, recs in data.items():
        for r in recs:
            for q in analyze.questions(r):
                if q["kind"] == "update-followup" or q["score"] is None:
                    skipped += 1
                    continue
                t = dict(q["task"])
                t.setdefault("kind", q["kind"])
                v = verdict(t, q["score"]["answer_text"])["correct"]
                if v is None:
                    skipped += 1
                    continue
                if v == analyze.correct(q):
                    same += 1
                else:
                    diff += 1
                    diffs.append(f"{cond}/{q['id']}")
    return {"compared": same + diff, "same": same, "different": diff, "skipped": skipped,
            "differences": diffs[:20]}  # fmt: skip


if __name__ == "__main__":
    print(json.dumps(regression_v1(), indent=1))
