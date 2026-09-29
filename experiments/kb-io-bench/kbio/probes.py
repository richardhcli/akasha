"""v2 probe set (V4): planted facts on the rung-1e2 entities, CRUD task families, patched rungs.

`python -m kbio.probes build`    facts + tasks (v2/probes.json) and page patches (v2/patches.json)
`python -m kbio.probes draft`    counterfactual READ questions (qwen2.5:72b, cached)
`python -m kbio.probes cb MODEL` closed-book filter: asks every question without tools, records
                                 which probes a model answers with the planted value (dropped)
`python -m kbio.probes verify [--exposure]`  spans verbatim, markers unique, hops unique, values
                                 unique; --exposure streams pages.jsonl for counterfactual exposure
`python -m kbio.probes corpus RUNG ARM OUT [--md]`  rung RUNG with patches applied (jsonl or md)

The background is unperturbed KILT except the 100 rung-1e2 pages (in every rung, kilt.py). Each of
them gets `perturb.perturb_page` (1 counterfactual + 2 fictional facts), so about 300 facts are
planted and about 150 are probed; the rest stay as distractors. Candidate marker phrases, towns and
person names were counted in the full KILT source (`v2/scan.sh`, whole-word, case-insensitive): a
value found there is never drawn, a fact whose marker is found there is never probed.

Arms: `base` = planted pages + the redundant copies of UPDATE/DELETE facts (READ, CREATE, UPDATE and
DELETE snapshots); `control` = base + the CREATE facts bulk-inserted (the control arm for CREATE
reads). Hops use what the reader sees: KILT anchors are not rendered in page text, so a hop is "the
page C mentions a topic (its title occurs in C's text) whose page mentions ...", kept only when
exactly one mentioned page carries a fact of that kind."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from pathlib import Path
from typing import Any

from kbio import llm, score
from kbio.kilt import PAGES, RUNGS_DIR, safe_name
from kbio.paths import DATA
from kbio.perturb import FICTIONAL, FIRST, SURNAMES, TOWNS, perturb_page
from kbio.prompts2 import READ_CLOSED_BOOK, SYSTEM_CLOSED_BOOK
from kbio.tasks import HOP_DESC, HOP_Q, counterfactual_question, fresh_values, template_key

V2 = DATA / "v2"
PROBES = V2 / "probes.json"
PATCHES = V2 / "patches.json"
SCAN_HITS = V2 / "scan-hits.txt"
SEED = 20260928
N_PAGES = 100
N = {"fi": 30, "cf": 20, "hop_page": 10, "hop_probe": 10, "agg": 10, "unans": 10}
N_CREATE, N_UPDATE, N_DELETE, K_COPIES = 20, 20, 20, 3
NOT_IN_KB = "Not in the knowledge base."
MARKERS = {
    "code": "{sur} Index",
    "town": "{sur} Lexicon Society",
    "count": "{first} {sur}",
    "year": "{sur} Prize",
    "shelf": "{sur} Reading Room",
}
# the clue that names a topic by one of its planted facts (probe -> page -> probe hops)
CLUE = {
    "code": "The topic catalogued as entry {code} in the {year} {sur} Index",
    "town": "The topic that the {sur} Lexicon Society first discussed at a meeting held in {town}",
    "count": "The topic of which the archivist {first} {sur} catalogued {count} distinct variants",
    "year": "The topic for work on which the {sur} Prize was first awarded in {year}",
    "shelf": "The topic whose material the {sur} Reading Room shelves under the mark {shelf}",
    "person": "The topic whose earliest surviving field notes are credited to the surveyor "
    "{first} {sur}",
}
AGG_Q = {
    "code": "Which topics are catalogued in a {sur} Index (of any year)? List every topic with "
    "its entry.",
    "town": "Which topics did the {sur} Lexicon Society first discuss? List every topic with the "
    "place of that meeting.",
    "count": "Which topics does a catalogue compiled by the archivist {first} {sur} cover? List "
    "every topic with the number of distinct variants listed.",
    "year": "For work on which topics is a {sur} Prize awarded? List every topic with the year "
    "the prize was first awarded.",
    "shelf": "Which topics have material in the {sur} Reading Room? List every topic with its "
    "shelf mark.",
}


# --- inputs -------------------------------------------------------------------------------------


def rung_pages(n: int = N_PAGES) -> list[dict[str, Any]]:
    out = []
    with open(PAGES, encoding="utf-8") as f:
        for line in f:
            out.append(json.loads(line))
            if len(out) >= n:
                break
    return out


def md_names(pages: list[dict[str, Any]]) -> dict[str, str]:
    """page id -> file name as kilt.write_md names it (rung-1e2 pages come first in every rung)."""
    names: set[str] = set()
    out = {}
    for p in pages:
        name = safe_name(p["title"])
        if name.lower() in names:
            name = f"{name} ({p['id']})"
        names.add(name.lower())
        out[p["id"]] = f"{name}.md"
    return out


def scan_hits() -> set[str]:
    """Lower-cased candidate phrases that occur anywhere in the unperturbed KILT source."""
    hits = set()
    for line in SCAN_HITS.read_text().splitlines():
        _n, phrase = line.strip().split(" ", 1)
        hits.add(phrase.lower())
    return hits


def blocks(text: str) -> list[str]:
    return text.rstrip("\n").split("\n\n")


def join(bl: list[str]) -> str:
    return "\n\n".join(bl) + "\n"


def groups(sentence: str) -> tuple[str, dict[str, str]]:
    for rx, key in score.TEMPLATES:
        m = rx.fullmatch(sentence)
        if m:
            return key, m.groupdict()
    raise ValueError(f"not a planted sentence: {sentence}")


def marker(sentence: str) -> str | None:
    key, g = groups(sentence)
    return MARKERS[key].format(**g) if key in MARKERS else None


def mentions(title: str, text: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(title)}(?!\w)", text, re.I) is not None


def body_blocks(bl: list[str]) -> list[int]:
    lead = next(i for i, b in enumerate(bl) if not b.startswith("#"))
    return [i for i, b in enumerate(bl) if i != lead and not b.startswith("#") and len(b) > 80]


def new_value(key: str, rng: random.Random, used: set[str], old: str) -> str:
    """A fresh value of the same kind (UPDATE); `used` holds `key:value` and bare person names."""
    for _ in range(1000):
        v = {
            "year": lambda: str(rng.randint(1780, 1935)),
            "code": lambda: f"{rng.randint(11, 97)}-{rng.choice('ABCDEFGHJKLMNPQRSTUVWXYZ')}",
            "count": lambda: str(rng.randint(13, 480)),
            "shelf": lambda: (
                f"MS {rng.choice('ABCDEFGHKLMNPRT')}.{rng.randint(2, 90)}.{rng.randint(3, 400)}"
            ),
            "town": lambda: rng.choice(TOWNS),
            "person": lambda: f"{rng.choice(FIRST)} {rng.choice(SURNAMES)}",
        }[key]()
        uniq = v if key == "person" else f"{key}:{v}"
        if v != old and uniq not in used:
            used.add(uniq)
            return v
    raise RuntimeError(f"no fresh {key} value")


def with_value(sentence: str, new: str) -> str:
    """The planted sentence with its answer value replaced (template rebuilt from its groups)."""
    key, g = groups(sentence)
    g = dict(g)
    if key == "person":
        g["first"], g["sur"] = new.split(" ", 1)
    else:
        g[key] = new
    tmpl = next(t for t, _q, k in FICTIONAL if k == key)
    out = tmpl.format(**g, person=f"{g.get('first', '')} {g.get('sur', '')}")
    assert out != sentence and new in out, (sentence, new)
    return out


# --- build --------------------------------------------------------------------------------------


def build() -> dict[str, Any]:
    rng = random.Random(SEED)
    pages = rung_pages()
    man = json.loads((RUNGS_DIR / "rungs.json").read_text())
    ids = [p["id"] for p in pages]
    assert set(ids) == {x for c in man["core_pairs"] for x in (c["core"], c["linked"])}
    partner = {}
    for c in man["core_pairs"]:
        partner[c["core"]], partner[c["linked"]] = c["linked"], c["core"]
    md = md_names(pages)
    title = {p["id"]: p["title"] for p in pages}
    hits = scan_hits()

    # values found anywhere in KILT are never drawn: perturb_page / fresh_values skip `used`
    used: set[str] = {f"town:{t}" for t in TOWNS if t.lower() in hits}
    used |= {f"{f} {s}" for f in FIRST for s in SURNAMES if f"{f} {s}".lower() in hits}
    n_excluded = len(used)

    text: dict[str, list[str]] = {}
    facts: dict[str, dict[str, Any]] = {}
    for p in pages:
        bl, recs = perturb_page(int(p["id"]), p["title"], blocks(p["text"]), used, seed=SEED)
        text[p["id"]] = bl
        for r in recs:
            f = {
                "fid": r.pid,
                "page": p["id"],
                "title": p["title"],
                "md": md[p["id"]],
                "kind": r.kind,
                "sentence": r.sentence,
                "answer": r.answer,
                "old_value": r.old_value,
                "question": r.question,
            }
            if r.kind == "fictional":
                f["key"] = template_key(r.sentence)
                mk = marker(r.sentence)
                f["marker"] = mk
                f["probe_ok"] = mk is None or mk.lower() not in hits
            else:
                f["key"], f["marker"], f["probe_ok"] = "cf", None, True
            f["copies"] = [{"id": p["id"], "md": md[p["id"]]}]
            facts[r.pid] = f

    fict = [f for f in facts.values() if f["kind"] == "fictional" and f["probe_ok"]]
    rng.shuffle(fict)
    taken: set[str] = set()

    def place(sentence: str, host: str, r: random.Random) -> None:
        bl = text[host]
        i = r.choice(body_blocks(bl))
        bl[i] = bl[i] + " " + sentence

    # --- UPDATE (all K copies) and DELETE (half single, half K copies): distinct pages ---
    upd, dele = [], []
    upages: set[str] = set()
    for f in fict:
        if len(upd) + len(dele) >= N_UPDATE + N_DELETE:
            break
        if f["page"] in upages:
            continue
        upages.add(f["page"])
        taken.add(f["fid"])
        (upd if len(upd) < N_UPDATE else dele).append(f)
    for i, f in enumerate(upd + dele):
        k = K_COPIES if (f in upd or i >= N_UPDATE + N_DELETE // 2) else 1
        hosts = [partner[f["page"]]]
        others = [x for x in ids if x not in (f["page"], partner[f["page"]])]
        hosts += rng.sample(others, K_COPIES - 2)
        for h in hosts[: k - 1]:
            place(f["sentence"], h, rng)
            f["copies"].append({"id": h, "md": md[h]})

    planted_keys: dict[str, set[str]] = {x: set() for x in ids}  # after copies
    for f in facts.values():
        if f["kind"] == "fictional":
            for c in f["copies"]:
                planted_keys[c["id"]].add(f["key"])
    joined = {x: join(text[x]) for x in ids}

    mentioned = {c: [x for x in ids if x != c and mentions(title[x], joined[c])] for c in ids}

    def hop_unique(c: str, f: dict[str, Any]) -> bool:
        cands = [x for x in mentioned[c] if f["key"] in planted_keys[x]]
        return cands == [f["page"]] and f["key"] not in planted_keys[c] and len(f["copies"]) == 1

    tasks: list[dict[str, Any]] = []

    def gold(f: dict[str, Any]) -> list[dict[str, str]]:
        return [{"span": f["sentence"], "id": c["id"], "md": c["md"]} for c in f["copies"]]

    # --- READ: hops (page -> probe, probe -> page -> probe) ---
    n_hp = n_pp = 0
    hop_c: set[str] = set()
    for c in sorted(ids, key=lambda _: rng.random()):
        for f in fict:
            if f["fid"] in taken or c in hop_c or f["page"] in hop_c or not hop_unique(c, f):
                continue
            k = f["key"]
            surface = re.search(rf"(?<!\w){re.escape(f['title'])}(?!\w)", joined[c], re.I)
            assert surface
            hop = {"span": surface.group(0), "id": c, "md": md[c], "role": "hop"}
            clue = next(
                (g for g in fict if g["page"] == c and g["fid"] not in taken
                 and len(g["copies"]) == 1),
                None,
            )  # fmt: skip
            if n_hp < N["hop_page"]:
                n_hp += 1
                tid = f"read-hop-page-{n_hp:02d}"
                q = (
                    f"The Wikipedia page '{title[c]}' mentions a topic that has its own page in "
                    f"the knowledge base, and that page mentions {HOP_DESC[k]}. {HOP_Q[k]}"
                )
                used_f = [f["fid"]]
                gl = [hop, *gold(f)]
            elif n_pp < N["hop_probe"] and clue is not None:
                n_pp += 1
                tid = f"read-hop-probe-{n_pp:02d}"
                ck, cg = groups(clue["sentence"])
                q = (
                    CLUE[ck].format(**cg) + " has its own page in the knowledge base. That page "
                    f"mentions another topic with its own page, which mentions {HOP_DESC[k]}. "
                    + HOP_Q[k]
                )
                used_f = [clue["fid"], f["fid"]]
                gl = [{**gold(clue)[0], "role": "clue"}, hop, *gold(f)]
            else:
                continue
            taken.update(used_f)
            hop_c.update({c, f["page"]})
            tasks.append(
                {
                    "id": tid,
                    "family": "read",
                    "kind": "multihop",
                    "subkind": tid.split("-")[2] + "-hop",
                    "question": q,
                    "answer": f["answer"],
                    "answer_values": [f["answer"]],
                    "gold": gl,
                    "facts": used_f,
                }
            )
            break

    # --- READ: single-hop fictional (distinct pages) and counterfactual (drafted later) ---
    n_fi = 0
    fi_pages: set[str] = set()
    for f in fict:
        if n_fi >= N["fi"]:
            break
        if f["fid"] in taken or f["page"] in fi_pages or len(f["copies"]) > 1:
            continue
        n_fi += 1
        taken.add(f["fid"])
        fi_pages.add(f["page"])
        tasks.append(
            {
                "id": f"read-fi-{n_fi:02d}",
                "family": "read",
                "kind": "single",
                "subkind": "fictional",
                "question": f["question"],
                "answer": f["answer"],
                "answer_values": [f["answer"]],
                "gold": gold(f),
                "facts": [f["fid"]],
            }
        )
    cf_pool = [f["fid"] for f in facts.values() if f["kind"] == "counterfactual"]
    rng.shuffle(cf_pool)

    # --- READ: aggregates over markers shared by 2-4 pages (every base copy counts) ---
    by_marker: dict[str, list[dict[str, Any]]] = {}
    for f in facts.values():
        if f["kind"] == "fictional" and f["marker"] and f["probe_ok"]:
            key, g = groups(f["sentence"])
            by_marker.setdefault(MARKERS[key].format(**g), []).append(f)
    agg = [
        (m, fs) for m, fs in sorted(by_marker.items())
        if 2 <= len(fs) <= 4
        and len({f["title"] for f in fs}) == len(fs)
    ]  # fmt: skip
    rng.shuffle(agg)
    for i, (m, fs) in enumerate(agg[: N["agg"]], start=1):
        key, g = groups(fs[0]["sentence"])
        tasks.append(
            {
                "id": f"read-agg-{i:02d}",
                "family": "read",
                "kind": "aggregate",
                "subkind": key,
                "marker": m,
                "question": AGG_Q[key].format(**g),
                "answer": "; ".join(f"{f['title']}: {f['answer']}" for f in fs),
                "answer_values": [f["answer"] for f in fs],
                "answer_mode": "all",
                "answer_topics": [f["title"] for f in fs],
                "gold": [x for f in fs for x in gold(f)],
                "facts": [f["fid"] for f in fs],
            }
        )

    # --- READ: near-miss unanswerables (planted-style question, marker never with that topic) ---
    wrng = random.Random(SEED + 1)
    n_un = 0
    for x in sorted(ids, key=lambda _: rng.random()):
        if n_un >= N["unans"]:
            break
        _s, q, key, _a = fresh_values(wrng, used, title[x])
        mk = marker(_s)
        if key not in MARKERS or mk is None or mk.lower() in hits:
            continue
        if any(mk.lower() in joined[y].lower() and mentions(title[x], joined[y]) for y in ids):
            continue
        n_un += 1
        tasks.append(
            {
                "id": f"read-unans-{n_un:02d}",
                "family": "read",
                "kind": "unanswerable",
                "question": q,
                "answer": NOT_IN_KB,
                "answer_values": [],
                "gold": [],
                "near_miss_page": x,
                "marker": mk,
            }
        )

    # --- UPDATE / DELETE ---
    for i, f in enumerate(upd, start=1):
        new = new_value(f["key"], rng, used, f["answer"])
        new_s = with_value(f["sentence"], new)
        tasks.append(
            {
                "id": f"update-{i:02d}",
                "family": "update",
                "fact": f["fid"],
                "old_sentence": f["sentence"],
                "new_sentence": new_s,
                "instruction": f'Correction: the knowledge base says "{f["sentence"]}" That is '
                f"wrong. The correct statement is: {new_s} Update the knowledge base.",
                "copies": f["copies"],
                "followup": {
                    "id": f"update-{i:02d}-q",
                    "question": f["question"],
                    "answer": new,
                    "answer_values": [new],
                    "stale_values": [f["answer"]],
                },
            }
        )
    for i, f in enumerate(dele, start=1):
        tasks.append(
            {
                "id": f"delete-{i:02d}",
                "family": "delete",
                "fact": f["fid"],
                "sentence": f["sentence"],
                "instruction": f'Retraction: the statement "{f["sentence"]}" is false. Remove it '
                "from the knowledge base; keep everything else.",
                "copies": f["copies"],
                "followup": {
                    "id": f"delete-{i:02d}-q",
                    "question": f["question"],
                    "answer": NOT_IN_KB,
                    "answer_values": [],
                    "zombie_values": [f["answer"]],
                },
            }
        )

    # --- CREATE: memos of 3-4 new facts on rung-1e2 topics; control arm bulk-inserts them ---
    control = {x: list(text[x]) for x in ids}
    crng = random.Random(SEED + 2)
    for w in range(1, N_CREATE + 1):
        picks = rng.sample(ids, rng.choice([3, 4]))
        fs = []
        for x in picks:
            while True:
                sent, q, key, ans = fresh_values(crng, used, title[x])
                mk = marker(sent)
                if key not in planted_keys[x] and (mk is None or mk.lower() not in hits):
                    break
            planted_keys[x].add(key)
            bl = control[x]
            j = crng.choice(body_blocks(bl))
            bl[j] = bl[j] + " " + sent
            fs.append(
                {
                    "sentence": sent,
                    "answer": ans,
                    "key": key,
                    "topic": title[x],
                    "page": x,
                    "md": md[x],
                    "question": q,
                }
            )
        tasks.append(
            {
                "id": f"create-{w:02d}",
                "family": "create",
                "memo": "Archive memo. New facts gathered this week:\n"
                + "\n".join(f"{i}. {f['sentence']}" for i, f in enumerate(fs, start=1)),
                "facts": fs,
                "followups": [
                    {
                        "id": f"create-{w:02d}-q{i}",
                        "question": f["question"],
                        "answer": f["answer"],
                        "answer_values": [f["answer"]],
                        "control_gold": [{"span": f["sentence"], "id": f["page"], "md": f["md"]}],
                    }
                    for i, f in enumerate(fs, start=1)
                ],
            }
        )

    patches = {
        "base": {x: join(text[x]) for x in ids},
        "control": {x: join(control[x]) for x in ids},
    }
    counts: dict[str, int] = {}
    for t in tasks:
        k = t["family"] + (f":{t['kind']}" if t["family"] == "read" else "")
        counts[k] = counts.get(k, 0) + 1
    out = {
        "seed": SEED,
        "pages": N_PAGES,
        "counts": counts,
        "planted": {
            "facts": len(facts),
            "fictional": sum(f["kind"] == "fictional" for f in facts.values()),
            "counterfactual": len(cf_pool),
            "not_probeable_marker_in_kilt": sum(not f["probe_ok"] for f in facts.values()),
            "values_excluded_by_scan": n_excluded,
        },
        "facts": facts,
        "cf_pool": cf_pool,
        "tasks": tasks,
        "cb": {},
    }
    V2.mkdir(parents=True, exist_ok=True)
    old = json.loads(PROBES.read_text()) if PROBES.exists() else {}
    out["cb"] = keep_cb(old.get("cb", {}), out)
    PROBES.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    PATCHES.write_text(json.dumps(patches, ensure_ascii=False) + "\n")
    return {"counts": counts, "planted": out["planted"]}


# --- LLM steps ----------------------------------------------------------------------------------


def draft() -> dict[str, Any]:
    """Counterfactual single-hop READ tasks from the cf pool (drafted question, code-verified)."""
    data = json.loads(PROBES.read_text())
    tasks = [t for t in data["tasks"] if t.get("subkind") != "counterfactual"]
    n, rejected = 0, []
    pages_used: set[str] = set()
    for fid in data["cf_pool"]:
        if n >= N["cf"]:
            break
        f = data["facts"][fid]
        if f["page"] in pages_used:
            continue
        d = counterfactual_question({"title": f["title"], "sentence": f["sentence"],
                                     "answer": f["answer"]})  # fmt: skip
        if not d:
            rejected.append(fid)
            continue
        n += 1
        pages_used.add(f["page"])
        tasks.append(
            {
                "id": f"read-cf-{n:02d}",
                "family": "read",
                "kind": "single",
                "subkind": "counterfactual",
                "question": d["question"],
                "answer": f["answer"],
                "answer_values": [f["answer"]],
                "stale_values": [f["old_value"]],
                "gold": [{"span": f["sentence"], "id": f["page"], "md": f["md"]}],
                "facts": [fid],
                "drafted_by": d["drafted_by"],
            }
        )
    data["tasks"] = tasks
    data["cb"] = keep_cb(data["cb"], data)
    data["counts"]["read:single-cf"] = n
    data["cf_rejected"] = rejected
    PROBES.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    return {"cf_tasks": n, "rejected": rejected, "llm": llm.STATS}


def questions(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Every scored question: READ tasks and the follow-ups of CREATE / UPDATE / DELETE."""
    out = []
    for t in data["tasks"]:
        if t["family"] == "read":
            out.append(t)
        out += t.get("followups", [])
        if "followup" in t:
            out.append(t["followup"])
    return out


CB_MODELS = ["gpt-oss:120b", "llama3.3:70b"]  # the models that will run the ladder


def keep_cb(cb: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    """Closed-book entries survive a rebuild only where the question text is unchanged."""
    now = {q["id"]: q["question"] for q in questions(data)}
    return {m: {k: v for k, v in r.items() if now.get(k) == v.get("question")}
            for m, r in cb.items()}  # fmt: skip


def leaks(answer: str, values: list[str]) -> list[str]:
    """Values present in an answer as whole tokens (same boundaries as perturb_page's check), so
    a planted count "14" does not match "2014"."""
    na = score.norm(answer)
    return [v for v in values if v and re.search(
        rf"(?<![\w.-]){re.escape(score.norm(v))}(?![\w-])", na)]  # fmt: skip


def probes_sha(data: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(data["tasks"], sort_keys=True).encode()).hexdigest()[:16]


def patches_sha() -> str:
    return hashlib.sha256(PATCHES.read_bytes()).hexdigest()[:16]


def closed_book(model: str) -> dict[str, Any]:
    """Ask every question with no tools (v2's closed-book prompt pair, which is also the CB floor
    of V2-PLAN §4.7); a probe is dropped for all models if any model's answer contains one of its
    planted values (or, for DELETE, the retracted value). Empty answers and errors are not stored,
    so a rerun retries them."""
    data = json.loads(PROBES.read_text())
    res = data["cb"].setdefault(model, {})
    empty = errors = 0
    for q in questions(data):
        if q["id"] in res:
            continue
        try:
            prompt = READ_CLOSED_BOOK.format(question=q["question"])
            ans = llm.ask(model, SYSTEM_CLOSED_BOOK, prompt, max_tokens=2000)
        except llm.LLMError:
            errors += 1
            continue
        if not ans.strip():
            empty += 1
            continue
        vals = q.get("answer_values", []) + (q.get("zombie_values") or [])
        res[q["id"]] = {"question": q["question"], "answer": ans[:500], "leaked": leaks(ans, vals)}
        PROBES.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    PROBES.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    leaked = sorted(k for k, v in res.items() if v.get("leaked"))
    return {"model": model, "answered": len(res), "empty": empty, "errors": errors,
            "leaked": leaked, "llm": llm.STATS}  # fmt: skip


def dropped(data: dict[str, Any]) -> set[str]:
    return {qid for r in data["cb"].values() for qid, v in r.items() if v.get("leaked")}


# --- corpora + verification ---------------------------------------------------------------------


def corpus(rung: int, arm: str, out: Path, as_md: bool = False) -> dict[str, Any]:
    """The first `rung` pages of pages.jsonl with the arm's patches applied: jsonl (id, title,
    text) for id-based harnesses, or one md file per page named like kilt.write_md."""
    patch = json.loads(PATCHES.read_text())[arm]
    n = 0
    names: set[str] = set()
    ids: dict[str, str] = {}
    if as_md:
        out.mkdir(parents=True, exist_ok=False)
        w = None
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        w = open(out.with_suffix(".partial"), "w", encoding="utf-8")
    with open(PAGES, encoding="utf-8") as f:
        for line in f:
            if n >= rung:
                break
            p = json.loads(line)
            body = patch.get(p["id"], p["text"])
            if as_md:
                name = safe_name(p["title"])
                if name.lower() in names:
                    name = f"{name} ({p['id']})"
                names.add(name.lower())
                (out / f"{name}.md").write_text(body, encoding="utf-8")
                ids[f"{name}.md"] = p["id"]
            else:
                assert w is not None
                rec = {"id": p["id"], "title": p["title"], "text": body}
                w.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
    if w is not None:
        w.close()
        out.with_suffix(".partial").replace(out)
    else:  # md name -> page id, beside (never inside) the vault: vault exports map back to ids
        (out.parent / f"{out.name}.ids.json").write_text(json.dumps(ids) + "\n")
    if n < rung:
        raise ValueError(f"pages.jsonl holds only {n} pages")
    meta = {"rung": rung, "arm": arm, "pages": n, "patches_sha": patches_sha()}
    (out.parent / f"{out.name}.meta.json").write_text(json.dumps(meta) + "\n")
    return {**meta, "out": str(out), "patched": len(patch)}


def hop_problems(t: dict[str, Any], data: dict[str, Any], base: dict[str, str],
                 title: dict[str, str]) -> list[str]:  # fmt: skip
    """A hop resolves uniquely through what the reader sees: exactly one page mentioned in C
    (whole word, case-insensitive) carries a fact of the answer's kind, C carries none, and the
    clue (probe hops) sits on C only."""
    keys: dict[str, set[str]] = {x: set() for x in base}
    for f in data["facts"].values():
        if f["kind"] == "fictional":
            for x, tx in base.items():
                if f["sentence"] in tx:
                    keys[x].add(f["key"])
    hop = next(g for g in t["gold"] if g.get("role") == "hop")
    ans = data["facts"][t["facts"][-1]]
    c, k = hop["id"], ans["key"]
    out = []
    cands = [x for x in base if x != c and mentions(title[x], base[c]) and k in keys[x]]
    if cands != [ans["page"]]:
        out.append(f"hop candidates {cands} != [{ans['page']}]")
    if k in keys[c]:
        out.append("C carries the answer kind")
    if len(t["facts"]) == 2:
        clue = data["facts"][t["facts"][0]]
        on = [x for x, tx in base.items() if clue["sentence"] in tx]
        if on != [c]:
            out.append(f"clue on {on}")
    return out


def verify(exposure: bool = False, require_cb: bool = True) -> dict[str, Any]:
    data = json.loads(PROBES.read_text())
    patches = json.loads(PATCHES.read_text())
    base, control = patches["base"], patches["control"]
    hits = scan_hits()
    pages = rung_pages()
    title = {p["id"]: p["title"] for p in pages}
    md = md_names(pages)
    bad: list[Any] = []
    spans = 0

    def span_ok(g: dict[str, str], texts: dict[str, str]) -> bool:
        return md[g["id"]] == g["md"] and g["span"] in texts[g["id"]]

    probed: dict[str, dict[str, Any]] = {}
    for t in data["tasks"]:
        for g in t.get("gold", []):
            spans += 1
            if not span_ok(g, base):
                bad.append((t["id"], "span", g["id"]))
        for fid in t.get("facts", []) if t["family"] == "read" else []:
            probed[fid] = data["facts"][fid]
        if t["family"] in ("update", "delete"):
            f = data["facts"][t["fact"]]
            probed[f["fid"]] = f
            s = t.get("old_sentence") or t["sentence"]
            found = sorted(x for x, tx in base.items() if s in tx)
            if found != sorted(c["id"] for c in t["copies"]):
                bad.append((t["id"], "copies", found))
            for c in t["copies"]:
                if base[c["id"]].count(s) != 1:
                    bad.append((t["id"], "copy count", c["id"]))
        if t["family"] == "create":
            for f, fu in zip(t["facts"], t["followups"], strict=True):
                spans += 1
                mk = marker(f["sentence"])
                if any(f["answer"] in tx and mentions(f["topic"], tx) and mk and mk in tx
                       for tx in base.values()):  # fmt: skip
                    bad.append((t["id"], "create fact already in base", f["answer"]))
                if not span_ok(fu["control_gold"][0], control):
                    bad.append((t["id"], "control span"))
        if t.get("kind") == "multihop":
            bad += [(t["id"], p) for p in hop_problems(t, data, base, title)]
        if t.get("kind") == "aggregate":
            where = {x for x, tx in base.items() if t["marker"] in tx}
            if where != {g["id"] for g in t["gold"]}:
                bad.append((t["id"], "aggregate marker elsewhere", sorted(where)))
        if t.get("kind") == "unanswerable":
            mk = t["marker"]
            if any(mk.lower() in tx.lower() and mentions(title[t["near_miss_page"]], tx)
                   for tx in control.values()):  # fmt: skip
                bad.append((t["id"], "answerable"))
    # probed fictional facts: marker absent from KILT, marker+topic only in the fact's copies
    for fid, f in probed.items():
        if f["kind"] != "fictional":
            continue
        if f["marker"] and f["marker"].lower() in hits:
            bad.append((fid, "marker in KILT"))
        if f["key"] in ("town", "person") and f["answer"].lower() in hits:
            bad.append((fid, "value in KILT"))
        mk = f["marker"] or f["answer"]
        where = sorted(x for x, tx in base.items() if mk in tx and f["title"] in tx
                       and f["answer"] in tx)  # fmt: skip
        if not set(where) >= {c["id"] for c in f["copies"]}:
            bad.append((fid, "copy missing", where))
    # values unique among all planted + created + updated facts
    vals: dict[str, str] = {}
    allv = [(f["key"], f["answer"], fid) for fid, f in data["facts"].items()
            if f["kind"] == "fictional"]  # fmt: skip
    allv += [(f["key"], f["answer"], t["id"]) for t in data["tasks"] for f in t.get("facts", [])
             if isinstance(f, dict)]  # fmt: skip
    allv += [(data["facts"][t["fact"]]["key"], t["followup"]["answer"], t["id"])
             for t in data["tasks"] if t["family"] == "update"]  # fmt: skip
    for key, v, who in allv:
        u = v if key == "person" else f"{key}:{v}"
        if u in vals:
            bad.append((who, "duplicate value", u, vals[u]))
        vals[u] = who
    drop = dropped(data)
    cb_missing = {m: sorted(q["id"] for q in questions(data) if q["id"] not in drop
                            and not (data["cb"].get(m, {}).get(q["id"]) or {}).get("answer"))
                  for m in CB_MODELS}  # fmt: skip
    if require_cb:
        bad += [(m, "no closed-book answer", len(v)) for m, v in cb_missing.items() if v]
    res: dict[str, Any] = {
        "tasks": len(data["tasks"]),
        "questions": len(questions(data)),
        "gold_spans": spans,
        "failures": bad,
        "cb_models": sorted(data["cb"]),
        "cb_missing": {m: len(v) for m, v in cb_missing.items()},
        "cb_dropped": sorted(drop),
        "probes_sha": probes_sha(data),
        "patches_sha": patches_sha(),
    }
    if exposure:
        res["cf_exposure"] = cf_exposure(data)
    return res


def cf_exposure(data: dict[str, Any]) -> dict[str, Any]:
    """Per counterfactual READ task: pages outside rung 1e2 that contain both the topic title and
    the real (old) year, by rung. Real contradicting evidence the reader may meet as N grows."""
    cf = [(t["id"], data["facts"][t["facts"][0]]) for t in data["tasks"]
          if t.get("subkind") == "counterfactual"]  # fmt: skip
    rungs = [int(r["pages"]) for k, r in json.loads(
        (RUNGS_DIR / "rungs.json").read_text())["rungs"].items() if k != "all"]  # fmt: skip
    by_year: dict[str, list[tuple[str, str]]] = {}
    for tid, f in cf:
        by_year.setdefault(f["old_value"], []).append((tid, f["title"]))
    counts = {tid: {str(r): 0 for r in rungs} for tid, _ in cf}
    yr = re.compile(r"\b(" + "|".join(sorted(by_year)) + r")\b") if by_year else None
    with open(PAGES, encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            if i < N_PAGES or yr is None:
                continue
            p = json.loads(line)
            found = set(yr.findall(p["text"]))
            for y in found:
                for tid, t in by_year[y]:
                    if t in p["text"]:
                        for r in rungs:
                            if i < r:
                                counts[tid][str(r)] += 1
    return counts


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="kbio.probes")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    sub.add_parser("draft")
    c = sub.add_parser("cb")
    c.add_argument("model")
    v = sub.add_parser("verify")
    v.add_argument("--exposure", action="store_true")
    v.add_argument("--no-cb", action="store_true", help="skip the closed-book completeness check")
    k = sub.add_parser("corpus")
    k.add_argument("rung", type=int)
    k.add_argument("arm", choices=["base", "control"])
    k.add_argument("out", type=Path)
    k.add_argument("--md", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "build":
        res = build()
    elif a.cmd == "draft":
        res = draft()
    elif a.cmd == "cb":
        res = closed_book(a.model)
    elif a.cmd == "verify":
        res = verify(a.exposure, not a.no_cb)
    else:
        res = corpus(a.rung, a.arm, a.out, a.md)
    print(json.dumps(res, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
