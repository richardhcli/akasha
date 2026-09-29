"""M12 task set (tasks/S-kb.json): the knowledge-base effect on keeping facts true.

- 40 multi-copy UPDATEs, `mc-<pageid>`: every tier-S lead definition pasted into 2-4 notes. The
  15 v1 rewrites are reused unchanged (`seen_in_v1`). The other 25 are drafted by qwen2.5:72b with
  the same checks as v1 (`tasks.update_rewrite`). Each has two follow-ups:
  q1 is v1's generic question; q2 targets one pasted copy in a note, named by path.
- 20 single-copy UPDATEs, `sc-<pid>`: a planted fictional fact (one sentence in one Wikipedia page)
  gets a fresh value of the same shape. It is the specificity control: with one copy, there is
  nothing to keep in sync, so the KBs should not differ.

Verified in code: every old sentence occurs in exactly its scored copies in both vaults (pasted
lines for multi-copy, one occurrence for single-copy), and no new value or sentence occurs
anywhere in the tier. Tasks quote the personal vault, so they live under data/ only."""

from __future__ import annotations

import json
import random
import re

from kbio import llm
from kbio.corpus import CORPORA, PERTURBATIONS, wiki_file
from kbio.tasks import TASKS, plain, update_rewrite

SEED = 12
N_SINGLE = 20
SHAPES = {
    "year": re.compile(r"^\d{4}$"),
    "code": re.compile(r"^\d{2}-[A-Z]$"),
    "count": re.compile(r"^\d{3}$"),
    "shelf": re.compile(r"^MS [A-H]\.\d{2}\.\d{3}$"),
}


def new_value(rng: random.Random, old: str) -> str | None:
    for shape, rx in SHAPES.items():
        if rx.match(old):
            if shape == "year":
                return str(rng.randint(1780, 1935))
            if shape == "code":
                return f"{rng.randint(11, 97)}-{rng.choice('JKLMNPQRSTUVWXYZ')}"
            if shape == "count":
                return str(rng.randint(213, 480))
            return f"MS {rng.choice('ABCDEFGH')}.{rng.randint(50, 90)}.{rng.randint(100, 400)}"
    return None


def load_texts(tier: str) -> tuple[dict[str, str], dict[str, str]]:
    root = CORPORA / tier
    r, a = root / "regular", root / "akasha"
    reg = {str(p.relative_to(r)): p.read_text() for p in r.rglob("*.md")}
    aka = {str(p.relative_to(a)): plain(p.read_text()) for p in a.rglob("*.md")}
    return reg, aka


def build(tier: str = "S") -> dict:
    rng = random.Random(SEED)
    root = CORPORA / tier
    man = json.loads((root / "manifest.json").read_text())
    perts = json.loads(PERTURBATIONS.read_text())["pages"]
    core = {str(p["pageid"]): p["title"] for p in man["core_pages"]}
    v1 = {str(t["pageid"]): t for t in json.loads((TASKS / "S.json").read_text())["tasks"]
          if t["family"] == "update"}  # fmt: skip
    reg, aka = load_texts(tier)
    everything = "\n".join(reg.values()) + "\n".join(aka.values())
    tasks: list[dict] = []
    rejected: list[dict] = []

    for pid in sorted(man["copies"], key=int):
        copies = man["copies"][pid]
        if len(copies) < 2:
            continue
        tid = f"mc-{pid}"
        if pid in v1:
            t = v1[pid]
            d = {k: t[k] for k in ("old_phrase", "new_phrase", "new_sentence")}
            lead, seen = t["old_sentence"], t["id"]
        else:
            lead = next(b for b in perts[pid]["blocks"] if not b.startswith("#"))
            r = update_rewrite(core[pid], lead)
            if not r:
                rejected.append({"id": tid, "why": "no valid rewrite"})
                continue
            d, seen = r, None
        bad = [c for c in copies if lead not in reg.get(c, "").splitlines()
               or lead not in aka.get(c, "")]  # fmt: skip
        found = {f for f, x in reg.items() if lead in x.splitlines()}
        if bad or found != set(copies) or d["new_sentence"] in everything:
            extra = sorted(found ^ set(copies))
            rejected.append({"id": tid, "why": f"copies {bad} / untracked {extra}"})
            continue
        notes = [c for c in copies if not c.startswith("Wikipedia/")]
        target = rng.choice(notes) if notes else copies[0]
        title = core[pid]
        tasks.append({
            "id": tid, "family": "update", "kind": "multi-copy", "page": title, "pageid": int(pid),
            "seen_in_v1": seen, "old_sentence": lead, "new_sentence": d["new_sentence"],
            "old_phrase": d["old_phrase"], "new_phrase": d["new_phrase"],
            "instruction": f"The lead definition of {title} has changed. It is now: "
            f'"{d["new_sentence"]}"',
            "copies": copies, "copies_scored": copies,
            "update_followups": [
                {"id": f"{tid}-q1", "target": None,
                 "question": f"What is the lead definition of {title} in the knowledge base? "
                 "Quote it exactly.",
                 "answer": d["new_sentence"], "answer_values": [d["new_phrase"]],
                 "stale_values": [d["old_phrase"]]},
                {"id": f"{tid}-q2", "target": target,
                 "question": f'In the note "{target}", which sentence is given as the '
                 f"Wikipedia definition of {title}? Quote it exactly.",
                 "answer": d["new_sentence"], "answer_values": [d["new_phrase"]],
                 "stale_values": [d["old_phrase"]]},
            ],
        })  # fmt: skip

    recs = [r for pid in sorted(core, key=int) for r in perts[pid]["perturbations"]
            if r["kind"] == "fictional" and r.get("question")]  # fmt: skip
    rng.shuffle(recs)
    for r in recs:
        if sum(t["kind"] == "single-copy" for t in tasks) >= N_SINGLE:
            break
        s, old = r["sentence"], r["answer"]
        page = wiki_file(r["title"])
        new = new_value(rng, old)
        if new is None or s.count(old) != 1:
            continue
        new_s = s.replace(old, new)
        once = sum(x.count(s) for x in reg.values()) == 1 and reg.get(page, "").count(s) == 1
        once_a = sum(x.count(s) for x in aka.values()) == 1 and aka.get(page, "").count(s) == 1
        if not (once and once_a) or new in everything or new_s in everything:
            rejected.append({"id": f"sc-{r['pid']}", "why": "not single-copy or value used"})
            continue
        tid = f"sc-{r['pid']}"
        tasks.append({
            "id": tid, "family": "update", "kind": "single-copy", "page": r["title"],
            "pageid": r["pageid"], "old_sentence": s, "new_sentence": new_s,
            "old_phrase": old, "new_phrase": new,
            "instruction": f"A fact about {r['title']} in the knowledge base has changed. "
            f'It is now: "{new_s}"',
            "copies": [page], "copies_scored": [page],
            "update_followups": [
                {"id": f"{tid}-q1", "target": None, "question": r["question"], "answer": new,
                 "answer_values": [new], "stale_values": [old]},
            ],
        })  # fmt: skip

    counts: dict[str, int] = {}
    for t in tasks:
        counts[t["kind"]] = counts.get(t["kind"], 0) + 1
    counts["multi-copy seen_in_v1"] = sum(bool(t.get("seen_in_v1")) for t in tasks)
    out = {"tier": tier, "seed": SEED, "counts": counts, "tasks": tasks, "rejected": rejected}
    (TASKS / f"{tier}-kb.json").write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    return out


if __name__ == "__main__":
    o = build()
    print(json.dumps({"counts": o["counts"], "rejected": o["rejected"]}, indent=1))
    print("llm", llm.STATS)
