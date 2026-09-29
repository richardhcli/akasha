"""Task generator + verifier (M3): READ, WRITE and UPDATE tasks for a tier, as one JSON file.

Every task carries machine-checkable gold: verbatim spans with their source file, and exact answer
values. Questions come from templates (planted facts) or are drafted by qwen2.5:72b from a gold
span (counterfactual and unperturbed facts, lead-definition updates), then verified in code: each
gold span occurs verbatim in its source file in both vault formats, and the answer occurs in the
span. The tasks quote the personal vault, so they live under data/ only."""

from __future__ import annotations

import argparse
import json
import random
import re
from pathlib import Path

from kbio import llm, score
from kbio.corpus import CORPORA, PERTURBATIONS, wiki_file
from kbio.paths import CONCEPTS_RETRIEVAL, DATA
from kbio.perturb import FICTIONAL, FIRST, SURNAMES, TOWNS

DRAFT_MODEL = "qwen2.5:72b"
TASKS = DATA / "tasks"
SEED = 11
ANCHOR_RE = re.compile(r"\s*\^tm-[a-z2-7]{8}\s*$", re.M)
SPAN_RE = re.compile(r"\}\{tm-[a-z2-7]{8}\}")
DRAFT_SYSTEM = "You write precise quiz questions from given text. Output only JSON."

# title-free questions for multi-hop items (the page must be reached through a link)
HOP_Q = {
    "code": "Under which entry is the topic catalogued in the index that page mentions?",
    "town": "In which place did a Lexicon Society first discuss the topic, according to that page?",
    "count": "How many distinct variants of the topic does the archivist's catalogue on that page "
    "list?",
    "year": "In which year was the prize for work on the topic first awarded, according to that "
    "page?",
    "shelf": "Under which shelf mark is material on the topic kept in the reading room named on "
    "that page?",
    "person": "To which surveyor does that page credit the earliest surviving field notes on the "
    "topic?",
}
HOP_DESC = {
    "code": "an index entry for its topic",
    "town": "where a Lexicon Society first discussed its topic",
    "count": "an archivist's catalogue of variants of its topic",
    "year": "a prize for work on its topic",
    "shelf": "a reading-room shelf mark for its topic",
    "person": "a surveyor's early field notes on its topic",
}


def template_key(sentence: str) -> str:
    for tmpl, _q, key in FICTIONAL:
        pat = re.escape(tmpl)
        pat = re.sub(r"\\\{[a-z]+\\\}", "(.+?)", pat)
        if re.fullmatch(pat, sentence):
            return key
    raise ValueError(f"not a planted sentence: {sentence}")


def plain(text: str) -> str:
    """akasha markup stripped (anchors and span braces), for span checks."""
    text = ANCHOR_RE.sub("", text)
    text = SPAN_RE.sub("", text)
    return re.sub(r"(^|\n)(\s*(?:[-*+]|\d+[.)]|>)?\s*)\{", r"\1\2", text)


def verify_span(tier: str, span: str, source: str) -> bool:
    root = CORPORA / tier
    reg = (root / "regular" / source).read_text()
    aka = (root / "akasha" / source).read_text()
    return span in reg and (span in aka or span in plain(aka))


def draft(prompt: str, attempt: int = 0) -> dict:
    suffix = f"\n(variant {attempt})" if attempt else ""
    raw = llm.ask(DRAFT_MODEL, DRAFT_SYSTEM, prompt + suffix, max_tokens=500)
    return llm.extract_json(raw)


def counterfactual_question(rec: dict) -> dict | None:
    for attempt in range(3):
        try:
            d = draft(
                f"Sentence (from the Wikipedia page on {rec['title']}):\n{rec['sentence']}\n\n"
                f'Write ONE question whose exact answer is "{rec["answer"]}" and that can be '
                f"answered only from this sentence. Name the topic ({rec['title']}) or the event "
                "so the question is unambiguous. The question must not contain the answer.\n"
                'Return JSON: {"question": "..."}',
                attempt,
            )
        except (ValueError, llm.LLMError):
            continue
        q = str(d.get("question", "")).strip()
        if q and rec["answer"] not in q and q.endswith("?"):
            return {"question": q, "drafted_by": DRAFT_MODEL}
    return None


def unperturbed_question(page_title: str, block: str, planted: list[str]) -> dict | None:
    for attempt in range(3):
        try:
            d = draft(
                f"Paragraph from the Wikipedia page on {page_title}:\n{block}\n\n"
                "Pick ONE specific fact stated in a single sentence (a date, number, or name). "
                "Copy that sentence EXACTLY, character for character, as `span`. Give `answer` as "
                "a short exact substring of the span (1-6 words), and a `question` that names "
                f"the topic ({page_title}), is answerable from the span alone, and does not "
                'contain the answer.\nReturn JSON: {"span": "...", "answer": "...", '
                '"question": "..."}',
                attempt,
            )
        except (ValueError, llm.LLMError):
            continue
        span, ans, q = (str(d.get(k, "")).strip() for k in ("span", "answer", "question"))
        ok = (
            span
            and len(span) >= 30
            and span in block
            and ans
            and ans in span
            and ans.lower() not in q.lower()
            and q.endswith("?")
            and not any(span in p or p in span for p in planted)
        )
        if ok:
            return {"question": q, "answer": ans, "span": span, "drafted_by": DRAFT_MODEL}
    return None


def update_rewrite(title: str, lead: str) -> dict | None:
    for attempt in range(3):
        try:
            d = draft(
                f"Definition sentence of '{title}':\n{lead}\n\n"
                "Make a modified version that changes exactly ONE short factual phrase (1-6 "
                "words) to a different, plausible but false phrase; keep everything else "
                "byte-identical. Then write a question about that detail whose answer is the new "
                "phrase (the question must name the topic and must not contain either phrase).\n"
                'Return JSON: {"old_phrase": "...", "new_phrase": "...", "new_sentence": "...", '
                '"question": "..."}',
                attempt,
            )
        except (ValueError, llm.LLMError):
            continue
        old, new, sent, q = (
            str(d.get(k, "")).strip()
            for k in ("old_phrase", "new_phrase", "new_sentence", "question")
        )
        ok = (
            old
            and new
            and old != new
            and old in lead
            and lead.count(old) == 1
            and sent == lead.replace(old, new)
            and new not in lead
            and old.lower() not in q.lower()
            and new.lower() not in q.lower()
            and q.endswith("?")
        )
        if ok:
            return {
                "old_phrase": old,
                "new_phrase": new,
                "new_sentence": sent,
                "question": q,
                "drafted_by": DRAFT_MODEL,
            }
    return None


def fresh_values(rng: random.Random, used: set[str], title: str) -> tuple[str, str, str, str]:
    """A planted-style fact with values unused anywhere in the corpus: (sentence, q, key, ans)."""
    while True:
        tmpl, q, key = FICTIONAL[rng.randrange(len(FICTIONAL))]
        vals = {
            "title": title,
            "sur": rng.choice(SURNAMES),
            "first": rng.choice(FIRST),
            "town": rng.choice(TOWNS),
            "year": str(rng.randint(1780, 1935)),
            "code": f"{rng.randint(11, 97)}-{rng.choice('JKLMNPQRSTUVWXYZ')}",
            "count": str(rng.randint(213, 480)),
            "shelf": f"MS {rng.choice('ABCDEFGH')}.{rng.randint(50, 90)}.{rng.randint(100, 400)}",
        }
        vals["person"] = f"{vals['first']} {vals['sur']}"
        ans = vals[key]
        if f"{key}:{ans}" not in used and ans not in used:
            used.add(f"{key}:{ans}")
            return tmpl.format(**vals), q.format(**vals), key, ans


def build(tier: str, n_update: int = 15, n_write: int = 15) -> dict:
    rng = random.Random(SEED)
    root = CORPORA / tier
    man = json.loads((root / "manifest.json").read_text())
    perts = json.loads(PERTURBATIONS.read_text())["pages"]
    core = {str(p["pageid"]): p["title"] for p in man["core_pages"]}
    recs = [r for pid in sorted(core) for r in perts[pid]["perturbations"]]
    planted = [r["sentence"] for r in recs]
    used = {r["answer"] for p in perts.values() for r in p["perturbations"]}
    used |= {
        f"{template_key(r['sentence'])}:{r['answer']}" for r in recs if r["kind"] == "fictional"
    }
    tasks: list[dict] = []
    rejected: list[dict] = []

    def add(t: dict) -> None:
        for g in t.get("gold", []):
            if not verify_span(tier, g["span"], g["source"]):
                rejected.append({"id": t["id"], "why": "span not verbatim", "span": g["span"]})
                return
            if t.get("answer_values") and g.get("role") != "hop":
                if not any(v in g["span"] for v in t["answer_values"]):
                    rejected.append({"id": t["id"], "why": "answer not in span"})
                    return
        tasks.append(t)

    # --- READ: perturbed single-hop (8 fictional + 7 counterfactual), distinct pages ---
    fict = [r for r in recs if r["kind"] == "fictional"]
    cf = [r for r in recs if r["kind"] == "counterfactual"]
    rng.shuffle(fict)
    rng.shuffle(cf)
    used_pages: set[int] = set()
    n = 0
    for r in cf:
        if n >= 7:
            break
        if r["pageid"] in used_pages:
            continue
        d = counterfactual_question(r)
        if not d:
            rejected.append({"id": r["pid"], "why": "no valid counterfactual question"})
            continue
        n += 1
        used_pages.add(r["pageid"])
        add(
            {
                "id": f"read-cf-{n:02d}",
                "family": "read",
                "kind": "perturbed",
                "subkind": "counterfactual",
                "question": d["question"],
                "answer": r["answer"],
                "answer_values": [r["answer"]],
                "stale_values": [r["old_value"]],
                "gold": [{"span": r["sentence"], "source": wiki_file(r["title"])}],
                "perturbation": r["pid"],
                "drafted_by": d["drafted_by"],
            }
        )
    n = 0
    for r in fict:
        if n >= 8:
            break
        if r["pageid"] in used_pages:
            continue
        n += 1
        used_pages.add(r["pageid"])
        add(
            {
                "id": f"read-fi-{n:02d}",
                "family": "read",
                "kind": "perturbed",
                "subkind": "fictional",
                "question": r["question"],
                "answer": r["answer"],
                "answer_values": [r["answer"]],
                "gold": [{"span": r["sentence"], "source": wiki_file(r["title"])}],
                "perturbation": r["pid"],
            }
        )

    # --- READ: perturbed multi-hop. 6 note -> page, 4 wiki -> wiki (title-free questions) ---
    notes_by_page: dict[str, list[str]] = {}
    mapping = json.loads((DATA / "sources" / "wiki" / "mapping.json").read_text())
    for note in man["notes"]:
        page = mapping[note]["page"]
        if page:
            notes_by_page.setdefault(page, []).append(note)
    n = 0
    for r in fict:
        if n >= 6:
            break
        notes = [x for x in notes_by_page.get(r["title"], []) if Path(x).stem != r["title"]]
        if not notes or r["pageid"] in used_pages:
            continue
        note = notes[0]
        key = template_key(r["sentence"])
        n += 1
        used_pages.add(r["pageid"])
        ref_line = f"- Wikipedia page: [[{wiki_file(r['title'])[:-3]}]]"
        add(
            {
                "id": f"read-hop-note-{n:02d}",
                "family": "read",
                "kind": "perturbed-multihop",
                "subkind": "note-to-page",
                "question": f"My note '{Path(note).stem}' references a Wikipedia page. "
                + HOP_Q[key],
                "answer": r["answer"],
                "answer_values": [r["answer"]],
                "gold": [
                    {"span": ref_line, "source": note, "role": "hop"},
                    {"span": r["sentence"], "source": wiki_file(r["title"])},
                ],
                "perturbation": r["pid"],
            }
        )
    title_by_file = {wiki_file(t): t for t in core.values()}
    related: dict[str, list[str]] = {}
    for f, t in title_by_file.items():
        text = (root / "regular" / f).read_text()
        sec = text.split("## Related pages", 1)[1] if "## Related pages" in text else ""
        related[t] = [
            title_by_file[m + ".md"]
            for m in re.findall(r"\[\[(Wikipedia/[^\]]+)\]\]", sec)
            if m + ".md" in title_by_file
        ]
    keys_of = {
        t: [
            template_key(r["sentence"])
            for r in recs
            if r["title"] == t and r["kind"] == "fictional"
        ]
        for t in core.values()
    }
    n = 0
    for a in sorted(related, key=lambda _: rng.random()):
        if n >= 4:
            break
        for r in fict:
            b = r["title"]
            if b not in related[a] or r["pageid"] in used_pages:
                continue
            key = template_key(r["sentence"])
            if sum(key in keys_of[x] for x in related[a]) != 1:
                continue  # the key must pick out one related page
            n += 1
            used_pages.add(r["pageid"])
            add(
                {
                    "id": f"read-hop-wiki-{n:02d}",
                    "family": "read",
                    "kind": "perturbed-multihop",
                    "subkind": "wiki-to-wiki",
                    "question": f"One of the related pages linked from the Wikipedia page "
                    f"'{a}' mentions {HOP_DESC[key]}. " + HOP_Q[key],
                    "answer": r["answer"],
                    "answer_values": [r["answer"]],
                    "gold": [
                        {
                            "span": f"- [[{wiki_file(b)[:-3]}]]",
                            "source": wiki_file(a),
                            "role": "hop",
                        },
                        {"span": r["sentence"], "source": wiki_file(b)},
                    ],
                    "perturbation": r["pid"],
                }
            )
            break

    # --- READ: unperturbed Wikipedia facts (reported next to closed-book) ---
    n = 0
    pages = sorted(core, key=lambda _: rng.random())
    for pid in pages:
        if n >= 10:
            break
        blocks = perts[pid]["blocks"]
        lead_i = next(i for i, b in enumerate(blocks) if not b.startswith("#"))
        cands = [
            b
            for i, b in enumerate(blocks)
            if i > lead_i + 1 and not b.startswith("#") and 200 < len(b) < 1500
            and not any(p in b for p in planted)
        ]  # fmt: skip
        if not cands:
            continue
        d = unperturbed_question(core[pid], rng.choice(cands), planted)
        if not d:
            rejected.append({"id": f"unperturbed-{pid}", "why": "no valid draft"})
            continue
        n += 1
        add(
            {
                "id": f"read-wiki-{n:02d}",
                "family": "read",
                "kind": "unperturbed",
                "question": d["question"],
                "answer": d["answer"],
                "answer_values": [d["answer"]],
                "gold": [{"span": d["span"], "source": wiki_file(core[pid])}],
                "drafted_by": d["drafted_by"],
            }
        )

    # --- READ: personal notes, aggregate, unanswerable (concepts-retrieval questions) ---
    cr = json.loads((CONCEPTS_RETRIEVAL / "questions.json").read_text())
    singles = [q for q in cr if q["type"] == "single"]
    multis = [q for q in cr if q["type"] == "multi"]
    unans = [q for q in cr if q["type"] == "unanswerable"]
    for kind, pool, k in (("personal", singles, 15), ("aggregate", multis, 5)):
        for q in sorted(rng.sample(pool, k), key=lambda q: q["id"]):
            add(
                {
                    "id": f"read-{kind}-{q['id']}",
                    "family": "read",
                    "kind": kind,
                    "question": q["question"],
                    "answer": q["answer"],
                    "answer_values": [],
                    "gold": [{"span": g["span"], "source": g["source"]} for g in q["gold"]],
                    "origin": "concepts-retrieval",
                }
            )
    for q in unans:
        add(
            {
                "id": f"read-unans-{q['id']}",
                "family": "read",
                "kind": "unanswerable",
                "question": q["question"],
                "answer": "Not in the knowledge base.",
                "answer_values": [],
                "gold": [],
                "origin": "concepts-retrieval",
            }
        )
    # near misses: a planted-style question about a fact that is not in the KB
    for i, r in enumerate(rng.sample(fict, 2), start=1):
        _s, q, _k, _a = fresh_values(rng, used, r["title"])
        add(
            {
                "id": f"read-unans-planted-{i}",
                "family": "read",
                "kind": "unanswerable",
                "question": q,
                "answer": "Not in the knowledge base.",
                "answer_values": [],
                "gold": [],
                "near_miss_of": r["pid"],
            }
        )

    # --- WRITE: memos of 3-4 new planted-style facts about concepts already in the KB ---
    concept_notes = [
        (note, mapping[note]["page"])
        for note in man["notes"]
        if mapping[note]["page"] in core.values() and not note.startswith("Wikipedia/")
    ]
    # A memo fact must not repeat the kind of a fact already planted on its page: a second "first
    # discussed at"/"earliest field notes" claim reads as a contradiction, and writers stalled on
    # it (STATUS log, 2026-09-27). Redraws use their own rng: the main stream (UPDATE) is unchanged.
    planted_keys: dict[str, set[str]] = {}
    for r in recs:
        if r["kind"] == "fictional":
            planted_keys.setdefault(r["title"], set()).add(template_key(r["sentence"]))
    wrng = random.Random(SEED + 1)
    for w in range(1, n_write + 1):
        picks = rng.sample(concept_notes, rng.choice([3, 4]))
        facts = []
        for note, page in picks:
            sent, q, key, ans = fresh_values(rng, used, page)
            while key in planted_keys.get(page, set()):
                sent, q, key, ans = fresh_values(wrng, used, page)
            facts.append(
                {
                    "sentence": sent,
                    "answer": ans,
                    "key": key,
                    "concept": page,
                    "concept_notes": notes_by_page[page],
                    "wiki_file": wiki_file(page),
                    "question": q,
                }
            )
        memo = "Archive memo. New facts gathered this week:\n" + "\n".join(
            f"{i}. {f['sentence']}" for i, f in enumerate(facts, start=1)
        )
        tasks.append(
            {
                "id": f"write-{w:02d}",
                "family": "write",
                "memo": memo,
                "facts": facts,
                "followups": [
                    {
                        "id": f"write-{w:02d}-q{i}",
                        "question": f["question"],
                        "answer": f["answer"],
                        "answer_values": [f["answer"]],
                    }
                    for i, f in enumerate(facts, start=1)
                ],
            }
        )

    # --- UPDATE: change one detail of a lead definition that has 3+ copies ---
    multi = [pid for pid, files in man["copies"].items() if len(files) >= 3]
    n = 0
    for pid in sorted(multi, key=lambda _: rng.random()):
        if n >= n_update:
            break
        lead = next(b for b in perts[pid]["blocks"] if not b.startswith("#"))
        d = update_rewrite(core[pid], lead)
        if not d:
            rejected.append({"id": f"update-{pid}", "why": "no valid rewrite"})
            continue
        n += 1
        tasks.append(
            {
                "id": f"update-{n:02d}",
                "family": "update",
                "page": core[pid],
                "pageid": int(pid),
                "lead_id": man["lead_ids"][pid],
                "old_sentence": lead,
                "new_sentence": d["new_sentence"],
                "old_phrase": d["old_phrase"],
                "new_phrase": d["new_phrase"],
                "instruction": f"The lead definition of {core[pid]} has changed. It is now: "
                f'"{d["new_sentence"]}"',
                "copies": man["copies"][pid],
                # neutral, code-scored follow-up (the drafted question sometimes leaked the
                # change, e.g. "according to the modified definition"; audit.md)
                "followup": {
                    "id": f"update-{n:02d}-q",
                    "question": f"What is the lead definition of {core[pid]} in the knowledge "
                    "base? Quote it exactly.",
                    "drafted_question": d["question"],
                    "answer": d["new_sentence"],
                    "answer_values": [d["new_phrase"]],
                    "stale_values": [d["old_phrase"]],
                },
                "drafted_by": d["drafted_by"],
            }
        )

    out = {
        "tier": tier,
        "seed": SEED,
        "counts": {},
        "tasks": tasks,
        "rejected": rejected,
    }
    for t in tasks:
        k = t["family"] + (f":{t['kind']}" if t["family"] == "read" else "")
        out["counts"][k] = out["counts"].get(k, 0) + 1
    TASKS.mkdir(parents=True, exist_ok=True)
    (TASKS / f"{tier}.json").write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    return out


def question_marker(question: str) -> tuple[str, str] | None:
    """(distinctive name, topic) of a planted-style question (score.fact_marker for questions)."""
    for _tmpl, q, key in FICTIONAL:
        pat = re.sub(r"\\\{([a-z]+)\\\}", lambda m: f"(?P<{m.group(1)}>.+?)", re.escape(q))
        m = re.fullmatch(pat, question)
        if m:
            vals = m.groupdict()
            return score.MARKER[key].format(**vals), vals["title"]
    return None


def verify(tier: str) -> dict:
    """Recheck the S tasks against a tier (any tier >= S). Every gold span / answer occurs
    verbatim; each UPDATE old sentence occurs in exactly the tier manifest's copy list (run.py
    scores against that list, which grows with the tier); no WRITE fact is already in the tier
    (marker + answer in one paragraph); planted unanswerable questions stay unanswerable."""
    data = json.loads((TASKS / "S.json").read_text())
    root = CORPORA / tier
    man = json.loads((root / "manifest.json").read_text())
    reg = root / "regular"
    texts = {str(p.relative_to(reg)): p.read_text() for p in reg.rglob("*.md")}
    texts |= {
        "akasha:" + str(p.relative_to(root / "akasha")): plain(p.read_text())
        for p in (root / "akasha").rglob("*.md")
    }
    pars = [score.norm(p) for _f, p in score.paragraphs(texts)]
    bad = []
    spans = 0
    for t in data["tasks"]:
        for g in t.get("gold", []):
            spans += 1
            if not verify_span(tier, g["span"], g["source"]):
                bad.append((t["id"], g["source"]))
        for f in t.get("facts", []):
            if f["answer"] not in f["sentence"]:
                bad.append((t["id"], "fact"))
            mk, an = (score.norm(x) for x in score.fact_marker(f["sentence"]))
            if any(mk in p and an in p for p in pars):
                bad.append((t["id"], f"write fact already present: {f['answer']}"))
        if t["family"] == "update":
            copies = man["copies"][str(t["pageid"])]
            for c in copies:
                if not verify_span(tier, t["old_sentence"], c):
                    bad.append((t["id"], c))
            # a pasted copy is a whole line; the sentence may also occur inside other prose
            # (Wikipedia/Philosophy.md quotes Logic's lead mid-paragraph), which is not a copy
            found = {f for f, x in texts.items() if not f.startswith("akasha:")
                     and t["old_sentence"] in x.splitlines()}  # fmt: skip
            if found != set(copies):
                bad.append((t["id"], f"untracked copies: {sorted(found ^ set(copies))}"))
        if t.get("kind") == "unanswerable" and "near_miss_of" in t:
            qm = question_marker(t["question"])
            if qm is None:
                bad.append((t["id"], "not a planted-style question"))
            else:
                mk, topic = (score.norm(x) for x in qm)
                if any(mk in p and topic in p for p in pars):
                    bad.append((t["id"], "answerable"))
    return {"tier": tier, "tasks": len(data["tasks"]), "gold_spans": spans, "failures": bad}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tier", nargs="?", default="S")
    ap.add_argument("--verify-only", action="store_true")
    args = ap.parse_args()
    if not args.verify_only:
        out = build(args.tier)
        print(json.dumps({"counts": out["counts"], "rejected": out["rejected"]}, indent=1))
        print("llm", llm.STATS)
    print(json.dumps(verify(args.tier), indent=1))


if __name__ == "__main__":
    main()
