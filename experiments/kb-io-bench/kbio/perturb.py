"""Leakage control: plant facts the models cannot know. Deterministic (seeded per page), no LLM.

Per page: one counterfactual edit (a year in a real sentence shifted) when a usable year exists,
plus two fictional attributes appended to body paragraphs. The lead sentence is never perturbed:
it is the definition that gets copied into concept notes, and UPDATE tasks own it. Planted values
are unique across the corpus, so an answer can only come from its own page."""

from __future__ import annotations

import random
import re
from dataclasses import asdict, dataclass

ABBREV = {
    "e.g", "i.e", "etc", "vs", "cf", "c", "ca", "mr", "mrs", "ms", "dr", "st", "jr", "sr", "inc",
    "ltd", "co", "no", "vol", "fig", "al", "approx", "prof", "gen", "col", "lt", "rev", "u.s",
    "u.k", "b.c", "a.d", "ph.d", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept",
    "oct", "nov", "dec", "est", "op", "pp", "ed", "eds", "trans", "ibid",
}  # fmt: skip
SENT_END_RE = re.compile(r"[.!?][\"')\]]?\s+(?=[A-Z0-9\"(\[])")
YEAR_RE = re.compile(r"\b(1[0-9]{3}|20[0-2][0-9])\b")

SURNAMES = [
    "Marlow", "Halvorsen", "Quennell", "Ashdown", "Tamberlyn", "Oakhurst", "Pellew", "Castellane",
    "Brightwater", "Vossberg", "Ellery", "Harrowgate", "Linwood", "Farquhar", "Delacourt",
    "Winterbourne", "Straughan", "Pemberton", "Achterberg", "Rosendahl", "Kilbride", "Thistlewood",
    "Moncrieff", "Averill", "Drummore", "Easterby", "Fenwright", "Galloway", "Ingleby", "Jessop",
]  # fmt: skip
FIRST = [
    "Edith", "Anselm", "Margit", "Tobias", "Cordelia", "Lysander", "Ottoline", "Barnaby",
    "Ingrid", "Casimir", "Philippa", "Rupert", "Solveig", "Evander", "Theodora", "Mortimer",
]  # fmt: skip
TOWN_HEADS = [
    "Brant", "Hollow", "Ormsby", "Kestrel", "Wex", "Caddon", "Larch", "Saltmarsh", "Dun", "Ravens",
    "Elder", "Fallow", "Quarry", "Merrow", "Thorn", "Ashcombe", "Pellam", "Stoney", "Gorse", "Wren",
]  # fmt: skip
TOWN_TAILS = [
    "wick", "mere", " Vale", " Bay", "moor", " Ferry", "field", " Cross", "holt", "brook", "gate",
    " Hythe", "by", "leigh", " Magna", " Reach", "hurst",
]  # fmt: skip
TOWNS = [h + t for h in TOWN_HEADS for t in TOWN_TAILS]

# (template sentence, question, answer key). Every value is unguessable without the page.
FICTIONAL = [
    (
        "In the {year} {sur} Index, the topic of {title} is catalogued as entry {code}.",
        "Under which entry is {title} catalogued in the {year} {sur} Index?",
        "code",
    ),
    (
        "The {sur} Lexicon Society first discussed {title} at a meeting held in {town}.",
        "Where did the {sur} Lexicon Society first discuss {title}?",
        "town",
    ),
    (
        "A catalogue compiled by the archivist {first} {sur} lists {count} distinct variants of "
        "{title}.",
        "How many distinct variants of {title} does the catalogue compiled by the archivist "
        "{first} {sur} list?",
        "count",
    ),
    (
        "The {sur} Prize for work on {title} was first awarded in {year}.",
        "In which year was the {sur} Prize for work on {title} first awarded?",
        "year",
    ),
    (
        "In the {sur} Reading Room, material on {title} is shelved under the mark {shelf}.",
        "Under which shelf mark is material on {title} kept in the {sur} Reading Room?",
        "shelf",
    ),
    (
        "The earliest surviving field notes on {title} are credited to the surveyor {first} {sur}.",
        "To which surveyor are the earliest surviving field notes on {title} credited?",
        "person",
    ),
]


@dataclass
class Perturbation:
    pid: str  # stable id: p<pageid>-<n>
    pageid: int
    title: str
    kind: str  # counterfactual | fictional
    block: int  # index into the page's blocks
    old_block: str
    new_block: str
    sentence: str  # the gold span (verbatim in the perturbed page)
    answer: str
    old_value: str | None = None
    question: str | None = None  # template question (fictional only)


def sentences(par: str) -> list[str]:
    """Deterministic splitter that does not break on abbreviations or initials."""
    out, start = [], 0
    for m in SENT_END_RE.finditer(par):
        tok = re.search(r"([A-Za-z.]+)$", par[start : m.start()])
        word = tok.group(1).lower().rstrip(".") if tok else ""
        if word in ABBREV or (tok and len(tok.group(1)) == 1):
            continue
        out.append(par[start : m.end()].strip())
        start = m.end()
    if par[start:].strip():
        out.append(par[start:].strip())
    return out


def split_lead(blocks: list[str]) -> list[str]:
    """Make the first paragraph's first sentence its own block (the lead / definition atom)."""
    for i, b in enumerate(blocks):
        if b.startswith("#"):
            continue
        s = sentences(b)
        if len(s) > 1 and len(s[0]) >= 30:
            return blocks[:i] + [s[0], b[len(s[0]) :].strip()] + blocks[i + 1 :]
        return blocks
    return blocks


def lead_index(blocks: list[str]) -> int:
    return next(i for i, b in enumerate(blocks) if not b.startswith("#"))


def perturb_page(
    pageid: int, title: str, blocks: list[str], used: set[str], seed: int = 0
) -> tuple[list[str], list[Perturbation]]:
    """Return the perturbed blocks and the records. `used` collects planted values corpus-wide."""
    rng = random.Random(f"{seed}:{pageid}")
    blocks = list(blocks)
    lead = lead_index(blocks)
    body = [i for i, b in enumerate(blocks) if i != lead and not b.startswith("#") and len(b) > 80]
    page_text = "\n".join(blocks)
    out: list[Perturbation] = []

    # counterfactual: a sentence with exactly one year, shifted to a year absent from the page
    cands = []
    for i in body:
        for s in sentences(blocks[i]):
            years = YEAR_RE.findall(s)
            if len(years) == 1 and 40 <= len(s) <= 400 and blocks[i].count(s) == 1:
                cands.append((i, s, years[0]))
    if cands:
        i, s, old = rng.choice(cands)
        for _ in range(40):
            new = str(int(old) + rng.choice([-1, 1]) * rng.randint(3, 19))
            if not re.search(rf"\b{new}\b", page_text) and int(new) < 2026:
                break
        else:
            new = ""
        if new:
            new_s = YEAR_RE.sub(new, s, count=1)
            new_block = blocks[i].replace(s, new_s)
            out.append(
                Perturbation(
                    f"p{pageid}-0",
                    pageid,
                    title,
                    "counterfactual",
                    i,
                    blocks[i],
                    new_block,
                    new_s,
                    new,
                    old_value=old,
                )  # fmt: skip
            )
            blocks[i] = new_block

    # fictional attributes: two templates, each appended to a different body paragraph
    free = [i for i in body if i not in {p.block for p in out}]
    for n, t in enumerate(rng.sample(range(len(FICTIONAL)), 2), start=1):
        if not free:
            break
        tmpl, q, key = FICTIONAL[t]
        for _ in range(500):
            vals = {
                "title": title,
                "sur": rng.choice(SURNAMES),
                "first": rng.choice(FIRST),
                "town": rng.choice(TOWNS),
                "year": str(rng.randint(1780, 1935)),
                "code": f"{rng.randint(11, 97)}-{rng.choice('ABCDEFGH')}",
                "count": str(rng.randint(13, 211)),
                "shelf": f"MS {rng.choice('KLMNPRT')}.{rng.randint(2, 48)}.{rng.randint(3, 99)}",
            }
            vals["person"] = f"{vals['first']} {vals['sur']}"
            answer = vals[key]
            uniq = f"{key}:{answer}" if key != "person" else answer
            if uniq not in used and not re.search(
                rf"(?<![\w.-]){re.escape(answer)}(?![\w-])", page_text
            ):
                used.add(uniq)
                break
        else:
            raise RuntimeError(f"no unique planted value for page {pageid}")
        i = free.pop(rng.randrange(len(free)))
        sentence = tmpl.format(**vals)
        new_block = blocks[i] + " " + sentence
        out.append(
            Perturbation(
                f"p{pageid}-{n}",
                pageid,
                title,
                "fictional",
                i,
                blocks[i],
                new_block,
                sentence,
                answer,
                question=q.format(**vals),
            )  # fmt: skip
        )
        blocks[i] = new_block
    return blocks, out


def as_dict(p: Perturbation) -> dict:
    return asdict(p)
