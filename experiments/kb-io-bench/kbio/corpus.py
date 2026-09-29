"""Build the regular and akasha vaults of a tier (config/tiers.toml).

Both vaults hold the same cleaned content; only the format differs.
- Concept notes: `clean_note` of the vault note (template scaffolding stripped, content lines
  byte-for-byte), plus a `## Reference` section with the mapped page's lead sentence and a
  wikilink to `Wikipedia/<Title>`. A note that links a mapped concept note gets that page's lead
  under `## Linked definitions` (at most 2 per note, 2 linked copies per lead).
- `Wikipedia/<Title>.md`: a source line (URL, revid, licence), the lead sentence as its own
  block, the perturbed body, and `## Related pages` wikilinks to linked pages in the tier.
- akasha = regular with every block anchored (contract grammar v1, the concepts-retrieval
  builder). Every copy of a lead sentence carries the same id: akasha transclusion.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
import tomllib
from collections import Counter
from pathlib import Path

from kbio.paths import CONCEPTS_RETRIEVAL, CONFIG, DATA, EXP, SOURCE_VAULT, TRASH
from kbio.perturb import as_dict, perturb_page, split_lead
from kbio.sources import wikipedia as wp

sys.path.insert(0, str(EXP.parent / "concepts-retrieval" / "scripts"))
from build_datasets import akasha_source, parse_blocks, tm_id  # noqa: E402
from common import WIKILINK_RE, clean_note  # noqa: E402

CORPORA = DATA / "corpora"
PERTURBATIONS = DATA / "sources" / "perturbations.json"
UNSAFE_RE = re.compile(r'[\\/:*?"<>|#^\[\]]')
# Related pages are not capped: a cap would let a bigger tier push a hop-gold link out of a page's
# list (S pages have at most 12 in-tier links, so S is unchanged).


def load_tiers() -> dict[str, dict]:
    return tomllib.loads((CONFIG / "tiers.toml").read_text())["tiers"]


def wiki_file(title: str) -> str:
    return f"Wikipedia/{UNSAFE_RE.sub('-', title)}.md"


def all_notes() -> list[str]:
    return sorted(str(p.relative_to(SOURCE_VAULT)) for p in SOURCE_VAULT.rglob("*.md"))


def link_targets(text: str, by_name: dict[str, str], self_rel: str) -> list[str]:
    out: list[str] = []
    for m in WIKILINK_RE.finditer(text):
        t = by_name.get(m.group(1).strip().lower())
        if t and t != self_rel and t not in out:
            out.append(t)
    return out


def by_name_index(notes: list[str]) -> dict[str, str]:
    idx: dict[str, str] = {}
    for n in notes:
        idx.setdefault(Path(n).stem.lower(), n)
    return idx


def build_perturbations() -> dict[int, dict]:
    """Perturb every mapped page once, in pageid order (planted values unique corpus-wide)."""
    if PERTURBATIONS.exists():
        return {int(k): v for k, v in json.loads(PERTURBATIONS.read_text())["pages"].items()}
    mapping = json.loads(wp.MAPPING.read_text())
    titles = {m["page"] for m in mapping.values() if m["page"]}
    pages = sorted(
        (json.loads(p.read_text()) for p in wp.PAGES.glob("*.json")), key=lambda p: p["pageid"]
    )
    used: set[str] = set()
    out: dict[int, dict] = {}
    for p in pages:
        if p["title"] not in titles:
            continue
        blocks = split_lead([b.strip() for b in p["text"].split("\n\n") if b.strip()])
        new_blocks, recs = perturb_page(p["pageid"], p["title"], blocks, used)
        out[p["pageid"]] = {"blocks": new_blocks, "perturbations": [as_dict(r) for r in recs]}
    PERTURBATIONS.write_text(
        json.dumps({"seed": 0, "pages": out}, indent=1, ensure_ascii=False) + "\n"
    )
    return out


def select(tier: str, tiers: dict[str, dict]) -> tuple[list[str], list[int], list[int]]:
    """(concept notes, core page ids, distractor page ids) of a tier."""
    cfg = tiers[tier]
    mapping = json.loads(wp.MAPPING.read_text())
    title_to_id = {json.loads(p.read_text())["title"]: int(p.stem) for p in wp.PAGES.glob("*.json")}
    if "base" in cfg:
        notes, core, distract = select(cfg["base"], tiers)
    elif cfg["notes"] == "all":
        notes, distract = all_notes(), []
        core = []
    else:
        manifest = CONCEPTS_RETRIEVAL / "datasets" / "subset-manifest.json"
        notes = list(json.loads(manifest.read_text())["notes"])
        distract, core = [], []
        pages = {mapping[n]["page"] for n in notes if mapping[n]["page"]}
        everything = all_notes()
        by_name = by_name_index(everything)
        texts = {n: (SOURCE_VAULT / n).read_text(errors="replace") for n in everything}
        from_subset = Counter(t for n in notes for t in link_targets(texts[n], by_name, n))
        indeg = Counter(t for n in everything for t in link_targets(texts[n], by_name, n))
        extra = sorted(
            (n for n in everything if n not in notes and mapping[n]["page"]),
            key=lambda n: (-from_subset[n], -indeg[n], n),
        )
        for n in extra:
            if len(pages) >= cfg["fill_pages"]:
                break
            if mapping[n]["page"] not in pages:
                notes.append(n)
                pages.add(mapping[n]["page"])
        notes.sort()
    if not core:
        core = sorted({title_to_id[mapping[n]["page"]] for n in notes if mapping[n]["page"]})
    hops = cfg.get("expand_hops", 0)
    if hops and "max_pages" in cfg:
        distract = expand(core, distract, hops, cfg["max_pages"], cfg.get("seed", 0))
    return notes, core, distract


def expand(core: list[int], distract: list[int], hops: int, max_pages: int, seed: int) -> list[int]:
    """Distractor pages: link targets of the tier's pages, ranked by in-links (fetched pages only;
    `kbio corpus fetch-expansion` fetches them)."""
    import random

    have = {json.loads(p.read_text())["title"]: int(p.stem) for p in wp.PAGES.glob("*.json")}
    chosen = list(distract)
    frontier = list(core) + list(distract)
    for _ in range(hops if not distract else 1):
        votes: Counter[str] = Counter()
        for pid in frontier:
            for t in wp.load_page(pid)["links"]:
                votes[t] += 1
        rng = random.Random(seed)
        cands = [t for t in votes if t in have and have[t] not in core and have[t] not in chosen]
        rng.shuffle(cands)
        cands.sort(key=lambda t: -votes[t])
        room = max_pages - len(core) - len(chosen)
        new = [have[t] for t in cands[: max(room, 0)]]
        chosen += new
        frontier = new
    return chosen


def expansion_titles(tier: str, tiers: dict[str, dict]) -> list[str]:
    """Titles a tier's expansion wants that are not fetched yet (ranked, capped)."""
    cfg = tiers[tier]
    notes, core, distract = select(cfg["base"], tiers) if "base" in cfg else select(tier, tiers)
    have = {json.loads(p.read_text())["title"] for p in wp.PAGES.glob("*.json")}
    votes: Counter[str] = Counter()
    for pid in core + distract:
        for t in wp.load_page(pid)["links"]:
            votes[t] += 1
    want = cfg["max_pages"] - len(core) - len(distract)
    ranked = sorted((t for t in votes if "(identifier)" not in t), key=lambda t: (-votes[t], t))
    missing = [t for t in ranked[: want + 200] if t not in have]
    return missing


def anchor(text: str, rel: str, shared: dict[str, str], stats: Counter) -> str:
    """Regular markdown -> contract grammar v1. Lines in `shared` (lead sentences) get their
    shared id; every other block gets a fresh deterministic id. Headings stay bare."""
    blocks = parse_blocks(text)
    out: list[str | None] = list(text.split("\n"))
    for i, b in enumerate(blocks):
        if b.kind != "atom":
            continue
        body = "\n".join(b.lines)
        b.id = shared.get(body) or tm_id(f"{rel}\x00{i}\x00{body}")
        try:
            src = akasha_source(b)
        except ValueError:  # unbalanced braces cannot form a span: leave the block unmanaged
            stats["unanchored_blocks"] += 1
            continue
        stats["atoms"] += 1
        stats["shared_atoms" if body in shared else "own_atoms"] += 1
        out[b.start] = src
        for k in range(b.start + 1, b.start + len(b.lines)):
            out[k] = None
    return "\n".join(line for line in out if line is not None)


def wiki_note(page: dict, blocks: list[str], related: list[str]) -> str:
    lines = [
        f"# {page['title']}",
        f"Source: {page['url']} (revision {page['revid']}, {page['timestamp']}). "
        "Text from Wikipedia, CC BY-SA 4.0.",
        *blocks,
    ]
    if related:
        lines.append("## Related pages")
        lines.append("\n".join(f"- [[{wiki_file(t)[:-3]}]]" for t in related))
    return "\n\n".join(lines).rstrip() + "\n"


def build(tier: str) -> dict:
    tiers = load_tiers()
    notes, core, distract = select(tier, tiers)
    perts = build_perturbations()
    mapping = json.loads(wp.MAPPING.read_text())
    pages = {pid: wp.load_page(pid) for pid in core + distract}
    in_tier = {p["title"] for p in pages.values()}
    blocks: dict[int, list[str]] = {}
    for pid, p in pages.items():
        if pid in perts:
            blocks[pid] = perts[pid]["blocks"]
        else:
            blocks[pid] = split_lead([b.strip() for b in p["text"].split("\n\n") if b.strip()])
    lead = {pid: next(b for b in bl if not b.startswith("#")) for pid, bl in blocks.items()}
    shared = {lead[pid]: tm_id(f"lead:{pid}") for pid in core}
    title_to_id = {p["title"]: pid for pid, p in pages.items()}

    root = CORPORA / tier
    if root.exists():
        TRASH.mkdir(parents=True, exist_ok=True)
        shutil.move(str(root), str(TRASH / f"corpus-{tier}-{time.strftime('%Y%m%d-%H%M%S')}"))
    regular, akasha = root / "regular", root / "akasha"
    stats: Counter = Counter()
    copies: dict[int, list[str]] = {pid: [wiki_file(pages[pid]["title"])] for pid in core}
    files: dict[str, str] = {}

    for pid, p in pages.items():
        related = [t for t in p["links"] if t in in_tier and t != p["title"]]
        files[wiki_file(p["title"])] = wiki_note(p, blocks[pid], related)

    by_name = by_name_index(notes)
    raw = {n: (SOURCE_VAULT / n).read_text(encoding="utf-8", errors="replace") for n in notes}
    linked_count: Counter[int] = Counter()
    for n in notes:
        text = clean_note(raw[n].replace("\r\n", "\n")).rstrip()
        own = mapping[n]["page"]
        own_id = title_to_id.get(own) if own else None
        if own_id in copies:
            text += (
                f"\n\n## Reference\n\n{lead[own_id]}\n\n- Wikipedia page: [[{wiki_file(own)[:-3]}]]"
            )
            copies[own_id].append(n)
        defs = []
        for t in link_targets(raw[n], by_name, n):
            tp = mapping[t]["page"]
            tid = title_to_id.get(tp) if tp else None
            if tid in copies and tid != own_id and tid not in [d[1] for d in defs]:
                if linked_count[tid] < 2 and len(defs) < 2:
                    defs.append((t, tid))
                    linked_count[tid] += 1
                    copies[tid].append(n)
        if defs:
            text += "\n\n## Linked definitions"
            for t, tid in defs:
                text += f"\n\n### [[{Path(t).stem}]]\n\n{lead[tid]}"
        files[n] = text + "\n"

    for rel, text in sorted(files.items()):
        for base, body in ((regular, text), (akasha, anchor(text, rel, shared, stats) + "\n")):
            (base / rel).parent.mkdir(parents=True, exist_ok=True)
            (base / rel).write_text(body, encoding="utf-8")

    manifest = {
        "tier": tier,
        "notes": notes,
        "core_pages": [{"pageid": pid, "title": pages[pid]["title"]} for pid in core],
        "distractor_pages": [{"pageid": pid, "title": pages[pid]["title"]} for pid in distract],
        "lead_ids": {str(pid): shared[lead[pid]] for pid in core},
        "copies": {str(pid): c for pid, c in copies.items()},
        "stats": {
            "files": len(files),
            "concept_notes": len(notes),
            "wiki_pages": len(pages),
            "bytes_regular": sum(len(t.encode()) for t in files.values()),
            "bytes_akasha": sum(p.stat().st_size for p in akasha.rglob("*.md")),
            "lead_copies": sum(len(c) for c in copies.values()),
            "leads_with_3plus_copies": sum(len(c) >= 3 for c in copies.values()),
            **dict(stats),
        },
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n")
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser(description="build a tier's regular and akasha vaults")
    ap.add_argument("tier")
    ap.add_argument("--fetch-expansion", action="store_true", help="fetch distractor pages first")
    ap.add_argument("--fetch-only", action="store_true", help="fetch, but do not build")
    args = ap.parse_args()
    if args.fetch_expansion:
        missing = expansion_titles(args.tier, load_tiers())
        print(f"fetching {len(missing)} expansion pages", flush=True)
        api = wp.Api()
        pages = wp.fetch_all(api, missing, with_links=False)
        wp.write_manifest(pages)
        print(f"http_requests={api.requests} cache_hits={api.cache_hits}", flush=True)
    if args.fetch_only:
        return
    m = build(args.tier)
    print(json.dumps(m["stats"], indent=1))


if __name__ == "__main__":
    main()
