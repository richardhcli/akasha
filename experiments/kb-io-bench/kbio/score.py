"""Code scoring: answers, citations, WRITE landings and UPDATE copies, plus the blind judge.

Everything here works from a vault's text (captured in the result record), never from the live
harness, so `kbio analyze` can recompute it offline."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from kbio import llm
from kbio.perturb import FICTIONAL

JUDGE_MODEL = "qwen2.5:72b"
NOT_FOUND = "NOT FOUND IN KNOWLEDGE BASE"
JUDGE_SYSTEM = "You are a strict, fair grader. Output only JSON."
JUDGE_PROMPT = """Grade the candidate answer against the reference answer for the question.
Score 2 = correct and complete (all key facts of the reference, nothing contradicting it);
1 = partially correct (some key facts right, some missing, or minor errors);
0 = wrong, missing, or claims the information is unavailable when the reference has an answer.
If the reference is "Not in the knowledge base.", score 2 only if the candidate says the information is not available, and 0 if it gives a substantive answer.
Ignore citation markers and ignore style. Return exactly: {{"score": 0|1|2, "reason": "<one sentence>"}}

QUESTION: {question}
REFERENCE ANSWER: {reference}
CANDIDATE ANSWER: {candidate}"""

ANCHOR_RE = re.compile(r"(?:\^tm-|\{tm-)([a-z2-7]{8})")
WIKILINK_RE = re.compile(r"(?<!!)\[\[([^\]|#]*)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
SOURCES_RE = re.compile(r"^\s*\**\s*SOURCES?\s*\**\s*:?\s*\**\s*$", re.I | re.M)
CITE_LINE_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.+?)\s*$")
QUOTE_RE = re.compile(r"[\"“”„«»']{1}(.+?)[\"“”„«»']{1}\s*$")


# gpt-oss writes non-breaking hyphens (U+2011 in "97-W"); NFKC folds U+2011 to U+2010, not to "-".
DASHES = str.maketrans({"\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2212": "-", "\u00ad": None})


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).translate(DASHES)
    s = s.replace("“", '"').replace("”", '"').replace("’", "'").replace("‘", "'")
    s = re.sub(r"[*_`]", "", s)
    s = s.replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", s).strip().lower()


def split_answer(final: str) -> tuple[str, list[tuple[str, str]]]:
    """(answer text, [(source, quote)]) from a `SOURCES:` block."""
    m = SOURCES_RE.search(final)
    if not m:
        m2 = re.search(r"SOURCES?\s*:", final, re.I)
        if not m2:
            return final.strip(), []
        body, tail = final[: m2.start()], final[m2.end() :]
    else:
        body, tail = final[: m.start()], final[m.end() :]
    cites = []
    for line in tail.splitlines():
        lm = CITE_LINE_RE.match(line)
        if not lm:
            continue
        item = lm.group(1).strip()
        q = ""
        k = item.find(":")
        # the source ends at the first ': ' followed by a quote, else the whole item
        qm = re.search(r":\s*[\"“”'«]", item)
        if qm:
            src, q = item[: qm.start()], item[qm.end() :]
            q = re.sub(r"[\"“”'»]\s*$", "", q)
        elif k > 0 and not item[:k].lower().startswith(("http", "memory")):
            src = item[:k]
        else:
            src = item
        cites.append((src.strip().strip("`*[]()").strip(), q.strip()))
    return body.strip(), cites


class Resolver:
    """Maps a cited source (path, akasha node id, basic-memory permalink/URI, or title) to vault
    files. Order: exact path, node id, permalink, exact-case title; ambiguous titles are not
    credited."""

    def __init__(self, texts: dict[str, str]) -> None:
        self.files = set(texts)
        self.by_id: dict[str, set[str]] = {}
        for f, t in texts.items():
            for m in ANCHOR_RE.finditer(t):
                self.by_id.setdefault(m.group(1), set()).add(f)
        self.by_slug: dict[str, set[str]] = {}
        self.by_stem: dict[str, set[str]] = {}
        self.by_stem_ci: dict[str, set[str]] = {}
        for f in self.files:
            self.by_slug.setdefault(slug(f[:-3]), set()).add(f)
            stem = Path(f).stem
            self.by_stem.setdefault(stem, set()).add(f)
            self.by_stem_ci.setdefault(stem.lower(), set()).add(f)

    def resolve(self, src: str) -> tuple[set[str], str]:
        s = src.strip().strip("<>").strip()
        s = re.sub(r"^memory://", "", s)
        s = re.sub(r"#.*$", "", s)  # path#^tm-id
        s = s.lstrip("/")
        cands = [s, s + ".md"] if not s.endswith(".md") else [s]
        for c in cands:
            if c in self.files:
                return {c}, "path"
        m = re.fullmatch(r"(?:node\s+)?(?:\^?tm-)?([a-z2-7]{8})", s)
        if m and m.group(1) in self.by_id:
            return set(self.by_id[m.group(1)]), "node"
        for sl in (slug(s.removesuffix(".md")), slug(re.sub(r"^kb/", "", s.removesuffix(".md")))):
            if sl in self.by_slug:
                return set(self.by_slug[sl]), "permalink"
        stem = Path(s).stem if s.endswith(".md") else s
        if stem in self.by_stem and len(self.by_stem[stem]) == 1:
            return set(self.by_stem[stem]), "title"
        if stem.lower() in self.by_stem_ci and len(self.by_stem_ci[stem.lower()]) == 1:
            return set(self.by_stem_ci[stem.lower()]), "title-ci"
        if stem in self.by_stem or stem.lower() in self.by_stem_ci:
            return set(), "ambiguous"
        return set(), "unresolved"


def slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def score_read(task: dict, final: str, texts: dict[str, str] | None) -> dict:
    """Code metrics for one READ-style answer. `texts` = the vault the agent saw (None = CB)."""
    body, cites = split_answer(final)
    out: dict = {"answer_text": body, **text_fields(task, body), "citations": []}
    if texts is None or not cites:
        out.update(cited_source_precision=None, cited_gold_span_recall=None, n_citations=0)
        if texts is not None and task.get("gold"):
            out["cited_gold_span_recall"] = 0.0
        return out
    res = Resolver(texts)
    gold = [g for g in task.get("gold", []) if g.get("role") != "hop"]
    gold_sources = {g["source"] for g in task.get("gold", [])}
    resolved = []
    for src, q in cites:
        files, how = res.resolve(src)
        resolved.append({"source": src, "quote": q[:300], "files": sorted(files), "how": how})
    out["citations"] = resolved
    out["n_citations"] = len(resolved)
    good = [c for c in resolved if c["files"]]
    out["cited_source_precision"] = (
        sum(bool(set(c["files"]) & gold_sources) for c in good) / len(good) if good and gold_sources else None
    )  # fmt: skip
    out["cited_gold_span_recall"] = gold_recall(gold, good, task) if gold else None
    return out


def text_fields(task: dict, body: str) -> dict:
    """The score fields that depend only on the answer text."""
    nb = norm(body)
    out = {
        "abstained": norm(NOT_FOUND) in nb or "not found in knowledge base" in nb,
        "exact_hit": any(norm(v) in nb for v in task.get("answer_values", [])),
        "stale_hit": any(norm(v) in nb for v in task.get("stale_values", []) or []),
    }
    if "new_sentence" in task:  # UPDATE follow-up: judged on the surrounding words
        out.update(update_answer(task, body))
    return out


def gold_recall(gold: list[dict], good: list[dict], task: dict) -> float:
    """Share of gold spans covered by a resolved citation to the gold file."""
    covered = 0
    vals = [norm(v) for v in task.get("answer_values", [])]
    for g in gold:
        span = norm(g["span"])
        for c in good:
            if g["source"] not in c["files"]:
                continue
            q = norm(c["quote"])
            if (len(q) >= 15 and (q in span or span in q)) or any(v and v in q for v in vals):
                covered += 1
                break
    return covered / len(gold)


def rescore(task: dict, sc: dict | None) -> dict | None:
    """Recompute a stored `score_read` result's text-derived fields with the current norm().

    Uses only what the record stores (answer text, resolved citations), so `kbio analyze` stays
    offline; the resolver-derived fields (files, cited_source_precision) are kept as stored."""
    if sc is None:
        return None
    out = {**sc, **text_fields(task, sc["answer_text"])}
    gold = [g for g in task.get("gold", []) if g.get("role") != "hop"]
    good = [c for c in sc.get("citations", []) if c["files"]]
    if gold and sc.get("cited_gold_span_recall") is not None and good:
        out["cited_gold_span_recall"] = gold_recall(gold, good, task)
    return out


def update_context(sentence: str, phrase: str) -> str:
    """The phrase with up to 3 words of left context and 2 of right context."""
    i = sentence.find(phrase)
    left = sentence[:i].split()[-3:]
    right = sentence[i + len(phrase) :].split()[:2]
    return " ".join(left + [phrase] + right)


def words(s: str) -> str:
    """norm() without punctuation, for phrase-in-context matching."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", norm(s))).strip()


def update_answer(task: dict, body: str) -> dict:
    nb = words(body)
    new_ctx = words(update_context(task["new_sentence"], task["new_phrase"]))
    old_ctx = words(update_context(task["old_sentence"], task["old_phrase"]))
    new_ok = words(task["new_sentence"]) in nb or new_ctx in nb
    old_seen = words(task["old_sentence"]) in nb or old_ctx in nb
    return {"update_correct": new_ok and not old_seen, "update_stale": old_seen}


# ---- WRITE ----
def _template_regexes() -> list[tuple[re.Pattern, str]]:
    out = []
    for tmpl, _q, key in FICTIONAL:
        pat = re.escape(tmpl)
        pat = re.sub(r"\\\{([a-z]+)\\\}", lambda m: f"(?P<{m.group(1)}>.+?)", pat)
        # a name may repeat within a template: only the first occurrence is a group
        seen: set[str] = set()

        def dedupe(m: re.Match) -> str:
            name = m.group(1)
            if name in seen:
                return f"(?P={name})"
            seen.add(name)
            return m.group(0)

        pat = re.sub(r"\(\?P<([a-z]+)>\.\+\?\)", dedupe, pat)
        out.append((re.compile(pat), key))
    return out


TEMPLATES = _template_regexes()
MARKER = {
    "code": "{sur} Index",
    "town": "{sur} Lexicon Society",
    "count": "{first} {sur}",
    "year": "{sur} Prize",
    "shelf": "{sur} Reading Room",
    "person": "surveyor",
}


def fact_marker(sentence: str) -> tuple[str, str]:
    """(distinctive name, answer) of a planted-style fact sentence."""
    for rx, key in TEMPLATES:
        m = rx.fullmatch(sentence)
        if m:
            vals = m.groupdict()
            vals["person"] = f"{vals.get('first', '')} {vals.get('sur', '')}".strip()
            ans = {"code": "code", "town": "town", "count": "count", "year": "year"}.get(key, key)
            return MARKER[key].format(**vals), vals[ans]
    raise ValueError(f"not a fact sentence: {sentence}")


def paragraphs(texts: dict[str, str]) -> list[tuple[str, str]]:
    out = []
    for f, t in texts.items():
        for p in re.split(r"\n\s*\n", t):
            if p.strip():
                out.append((f, p))
    return out


def score_write(task: dict, before: dict[str, str], after: dict[str, str]) -> dict:
    before_pars = paragraphs(before)
    after_pars = paragraphs(after)
    facts = []
    for f in task["facts"]:
        marker, ans = fact_marker(f["sentence"])
        mk, an = norm(marker), norm(ans)

        def has(p: str, mk: str = mk, an: str = an) -> bool:
            n = norm(p)
            return mk in n and an in n

        base = sum(has(p) for _f, p in before_pars)
        hits = [(fn, p) for fn, p in after_pars if has(p)]
        landed = base == 0 and bool(hits)
        targets = {f["wiki_file"], *f["concept_notes"]}
        link_names = {Path(t).stem for t in targets} | {t[:-3] for t in targets}
        linked = False
        for fn, _p in hits:
            if fn in targets:
                linked = True
            links = {m.group(1).strip() for m in WIKILINK_RE.finditer(after.get(fn, ""))}
            if links & link_names:
                linked = True
        groups = set()
        for fn, p in hits:
            ids = ANCHOR_RE.findall(p)
            groups.add(("id", ids[-1]) if ids else ("text", fn, norm(p)))
        facts.append(
            {
                "answer": ans,
                "marker": marker,
                "landed": landed,
                "linked": landed and linked,
                "copies": len(hits),
                "distinct": len(groups),
                "duplicates": max(len(groups) - 1, 0),
                "files": sorted({fn for fn, _p in hits}),
            }
        )
    n = len(facts)
    return {
        "facts": facts,
        "landed_rate": sum(f["landed"] for f in facts) / n,
        "linked_rate": sum(f["linked"] for f in facts) / n,
        "duplicates": sum(f["duplicates"] for f in facts),
    }


def score_update(task: dict, copies: list[str], after: dict[str, str]) -> dict:
    old = re.sub(r"\s+", " ", task["old_sentence"])
    new = re.sub(r"\s+", " ", task["new_sentence"])
    per = {}
    for f in copies:
        t = re.sub(r"\s+", " ", after.get(f, ""))
        per[f] = {"updated": new in t, "stale": old in t, "missing": f not in after}
    extra_new = sorted(
        f for f, t in after.items() if f not in per and new in re.sub(r"\s+", " ", t)
    )
    n = len(copies)
    return {
        "copies": per,
        "copies_total": n,
        "copies_updated": sum(v["updated"] for v in per.values()),
        "copies_stale": sum(v["stale"] for v in per.values()),
        "copies_updated_rate": sum(v["updated"] and not v["stale"] for v in per.values()) / n,
        "new_sentence_elsewhere": extra_new,
    }


# ---- blind judge ----
def neutral(answer_text: str, ids: set[str]) -> str:
    """Strip everything that could reveal the condition: node ids present in the vault, paths,
    permalinks and memory:// URIs. The SOURCES block is already removed by split_answer."""
    s = answer_text
    s = re.sub(r"memory://\S+", "", s)
    s = re.sub(r"\bkb/\S+", "", s)
    s = re.sub(r"\S+\.md\b", "", s)
    s = re.sub(r"\^?tm-[a-z2-7]{8}", "", s)
    if ids:
        s = re.sub(r"\b(" + "|".join(sorted(ids)) + r")\b", "", s)
    s = re.sub(r"\[\s*\]|\(\s*\)", "", s)
    return re.sub(r"[ \t]{2,}", " ", s).strip()


def judge(question: str, reference: str, candidate: str) -> dict:
    prompt = JUDGE_PROMPT.format(question=question, reference=reference, candidate=candidate)
    try:
        raw = llm.ask(JUDGE_MODEL, JUDGE_SYSTEM, prompt, max_tokens=200)
        v = llm.extract_json(raw)
        return {"score": int(v["score"]), "reason": str(v.get("reason", ""))}
    except (ValueError, KeyError, TypeError, llm.LLMError) as e:
        return {"score": None, "reason": f"judge failed: {e}"}
