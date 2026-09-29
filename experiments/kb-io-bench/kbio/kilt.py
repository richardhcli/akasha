"""v2 corpus: the KILT knowledge source (2019-08-01 Wikipedia, 5.9M pages) cut into nested rungs.

`python -m kbio.kilt index`   one parallel pass over kilt_knowledgesource.json -> index.tsv
                              (id, hash key, byte offset, size, flags, title, first links)
`python -m kbio.kilt plan`    rung order + manifests (rungs.json, order.tsv, core pairs)
`python -m kbio.kilt build [--upto N]`  compact pages.jsonl in rung order (first N pages)
`python -m kbio.kilt md --rung N OUT`   rung N as one markdown file per page (md harnesses)
`python -m kbio.kilt verify`  rung k is a prefix (so a subset) of rung k+1; hashes match

Rung order (seeded, deterministic): rung 1e2 is built so that multi-hop probes stay inside it: the
50 lowest-key "core" pages (>= CORE_CHARS characters, not a list or disambiguation page), each
followed by the first page it links to that is itself substantial and not yet chosen. Every
other non-empty page follows, sorted by key = sha256(seed:id). Rung N = the first N pages of that
order, so rung k is a prefix of rung k+1 and every probe entity (in rung 1e2) is in every rung.
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import os
import re
import subprocess
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

from kbio.paths import DATA

KILT = DATA / "kilt"
SRC = KILT / "kilt_knowledgesource.json"
INDEX = KILT / "index.tsv"
RUNGS_DIR = KILT / "rungs"
PAGES = KILT / "pages.jsonl"
SEED = 20260927
RUNGS = [100, 1_000, 10_000, 100_000, 1_000_000]  # plus "all"
CORE = 50
CORE_CHARS = 2000
LINK_CHARS = 1000
MAX_LINKS = 30
SECTION = re.compile(r"^Section::::(.*?)\.?$")
BULLET = re.compile(r"^BULLET::::-?\s*")


def key(page_id: str, seed: int = SEED) -> str:
    return hashlib.sha256(f"{seed}:{page_id}".encode()).hexdigest()[:16]


def page_text(rec: dict[str, Any]) -> str:
    """KILT paragraphs -> markdown: the title paragraph becomes '# Title', 'Section::::A:::B.'
    becomes a heading (one level per ':::'), 'BULLET::::- x' a list item."""
    out = [f"# {rec['wikipedia_title']}"]
    for p in rec["text"][1:]:
        p = p.rstrip("\n")
        m = SECTION.match(p)
        if m:
            parts = m.group(1).split(":::")
            out.append("#" * min(len(parts) + 1, 6) + " " + parts[-1].strip())
        elif p.startswith("BULLET::::"):
            out.append("- " + BULLET.sub("", p))
        elif p.strip():
            out.append(p)
    return "\n\n".join(out) + "\n"


def _flags(rec: dict[str, Any]) -> str:
    title = rec["wikipedia_title"]
    cats = (rec.get("categories") or "").lower()
    f = ""
    if "(disambiguation)" in title or "disambiguation" in cats:
        f += "d"
    if title.startswith(("List of", "Lists of", "Index of")):
        f += "l"
    return f or "-"


def _index_range(args: tuple[int, int, int, str, str]) -> str:
    # paths are passed in: workers may start by forkserver (3.14), which drops patched globals
    part, start, end, src, index = args
    out = Path(index).with_suffix(f".part{part:02d}")
    with open(src, "rb") as f, open(out, "w", encoding="utf-8") as w:
        f.seek(start)
        if start:
            f.readline()  # the previous range owns the line that straddles `start`
        while True:
            off = f.tell()
            if off > end:
                break
            line = f.readline()
            if not line:
                break
            rec = json.loads(line)
            pid = str(rec["wikipedia_id"])
            n_chars = sum(len(p) for p in rec["text"][1:])
            n_par = sum(1 for p in rec["text"][1:] if p.strip() and not p.startswith("Section::::"))
            links: list[str] = []
            for a in rec.get("anchors") or []:
                t = a.get("wikipedia_id")
                if t and t != pid and t not in links:
                    links.append(str(t))
                    if len(links) >= MAX_LINKS:
                        break
            title = " ".join(rec["wikipedia_title"].split())
            w.write(
                f"{pid}\t{key(pid)}\t{off}\t{len(line)}\t{n_chars}\t{n_par}\t{_flags(rec)}\t"
                f"{title}\t{','.join(links)}\n"
            )
    return str(out)


def build_index(workers: int = 6) -> dict[str, Any]:
    size = SRC.stat().st_size
    step = size // (workers * 4) + 1
    ranges = [
        (i, s, min(s + step, size), str(SRC), str(INDEX))
        for i, s in enumerate(range(0, size, step))
    ]
    with ProcessPoolExecutor(workers) as ex:
        parts = list(ex.map(_index_range, ranges))
    n = 0
    with open(INDEX, "w", encoding="utf-8") as w:
        for p in parts:
            with open(p, encoding="utf-8") as r:
                for line in r:
                    w.write(line)
                    n += 1
            Path(p).unlink()
    return {"pages": n, "index": str(INDEX), "bytes": size}


def _stream():  # noqa: ANN202
    with open(INDEX, encoding="utf-8") as f:
        for line in f:
            yield line.rstrip("\n").split("\t")


def _substantial(r: list[str], chars: int) -> bool:
    return int(r[4]) >= chars and r[6] == "-"


def plan(pool: int = 2000) -> dict[str, Any]:
    """Streams the index (5.9M rows do not fit in memory next to a running job); the tail of the
    order is sorted by key with an external `sort`."""
    # 1. the `pool` lowest-key core candidates, with their link lists
    cands: list[list[str]] = []
    n_rows = n_eligible = 0
    for r in _stream():
        n_rows += 1
        if int(r[5]) < 1:
            continue
        n_eligible += 1
        if _substantial(r, CORE_CHARS) and r[8]:
            cands.append(r)
            if len(cands) > 4 * pool:
                cands = heapq.nsmallest(pool, cands, key=lambda x: x[1])
    cands = heapq.nsmallest(pool, cands, key=lambda x: x[1])
    # 2. what the candidates link to
    wanted = {t for r in cands for t in r[8].split(",")}
    info = {r[0]: r for r in _stream() if r[0] in wanted}
    chosen: list[str] = []
    taken: set[str] = set()
    pairs: list[tuple[str, str, str, str]] = []
    for r in cands:
        if len(pairs) >= CORE:
            break
        if r[0] in taken:
            continue
        target = next(
            (t for t in r[8].split(",")
             if t in info and t not in taken and t != r[0] and _substantial(info[t], LINK_CHARS)),
            None,
        )  # fmt: skip
        if target is None:
            continue
        chosen += [r[0], target]
        taken |= {r[0], target}
        pairs.append((r[0], r[7], target, info[target][7]))
    if len(pairs) < CORE:
        raise RuntimeError(f"only {len(pairs)} core pairs in a pool of {pool}")
    # 3. order = chosen pairs, then every other eligible page by key (external sort)
    RUNGS_DIR.mkdir(parents=True, exist_ok=True)
    head: dict[str, str] = {}
    tail = RUNGS_DIR / "tail.unsorted"
    with open(tail, "w", encoding="utf-8") as w:
        for r in _stream():
            if int(r[5]) < 1:
                continue
            row = f"{r[0]}\t{r[2]}\t{r[3]}\t{r[7]}"
            if r[0] in taken:
                head[r[0]] = row
            else:
                w.write(f"{r[1]}\t{row}\n")
    sorted_tail = RUNGS_DIR / "tail.sorted"
    subprocess.run(
        ["sort", "-t", "\t", "-k1,1", "-S", "1G", "-T", str(RUNGS_DIR), "-o", str(sorted_tail),
         str(tail)],
        check=True,
        env={**os.environ, "LC_ALL": "C"},
    )  # fmt: skip
    tail.unlink()
    rung_sizes = [*RUNGS, n_eligible]
    hashes = {n: hashlib.sha256() for n in rung_sizes}
    src_bytes = dict.fromkeys(rung_sizes, 0)
    i = 0
    with open(RUNGS_DIR / "order.tsv", "w", encoding="utf-8") as w:

        def emit(row: str) -> None:
            nonlocal i
            pid, _off, ln, _t = row.split("\t")
            w.write(f"{i}\t{row}\n")
            for n in rung_sizes:
                if i < n:
                    hashes[n].update((("\n" if i else "") + pid).encode())
                    src_bytes[n] += int(ln)
            i += 1

        for pid in chosen:
            emit(head[pid])
        with open(sorted_tail, encoding="utf-8") as f:
            for line in f:
                emit(line.rstrip("\n").split("\t", 1)[1])
    sorted_tail.unlink()
    assert i == n_eligible
    rungs = {
        ("all" if n == n_eligible else f"{n:.0e}".replace("+0", "")): {
            "pages": min(n, n_eligible),
            "sha256_ids": hashes[n].hexdigest(),
            "bytes_src": src_bytes[n],
        }
        for n in rung_sizes
    }
    manifest = {
        "source": "http://dl.fbaipublicfiles.com/KILT/kilt_knowledgesource.json",
        "source_md5": "d1dca62aa6ba889d2e842182e3114af5",
        "seed": SEED,
        "index_pages": n_rows,
        "eligible_pages": n_eligible,
        "core_pairs": [
            {"core": a, "core_title": at, "linked": b, "linked_title": bt} for a, at, b, bt in pairs
        ],
        "rungs": rungs,
    }
    (RUNGS_DIR / "rungs.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n")
    return {k: v for k, v in manifest.items() if k != "core_pairs"} | {"core_pairs": len(pairs)}


def _order(upto: int | None = None) -> list[tuple[str, int, int]]:
    out = []
    with open(RUNGS_DIR / "order.tsv", encoding="utf-8") as f:
        for line in f:
            _i, pid, off, ln, _t = line.rstrip("\n").split("\t")
            out.append((pid, int(off), int(ln)))
            if upto is not None and len(out) >= upto:
                break
    return out


def _records(upto: int | None):  # noqa: ANN202
    with open(SRC, "rb") as src:
        for pid, off, ln in _order(upto):
            src.seek(off)
            rec = json.loads(src.read(ln))
            assert str(rec["wikipedia_id"]) == pid, (pid, off)
            yield rec


def build(upto: int | None = None) -> dict[str, Any]:
    """Compact pages in rung order: {id, title, text (markdown), links, revid}."""
    n = 0
    tmp = PAGES.with_suffix(".jsonl.partial")
    with open(tmp, "w", encoding="utf-8") as w:
        for rec in _records(upto):
            links = []
            for a in rec.get("anchors") or []:
                t = a.get("wikipedia_id")
                if t and t not in links:
                    links.append(str(t))
            w.write(
                json.dumps(
                    {
                        "id": str(rec["wikipedia_id"]),
                        "title": rec["wikipedia_title"],
                        "text": page_text(rec),
                        "links": links,
                        "revid": rec.get("history", {}).get("revid"),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            n += 1
    os.replace(tmp, PAGES)
    return {"pages": n, "file": str(PAGES), "bytes": PAGES.stat().st_size}


def safe_name(title: str) -> str:
    return re.sub(r'[\\/:*?"<>|#^\[\]]', "_", title).strip(" .") or "_"


def write_md(rung: int, out: Path) -> dict[str, Any]:
    """Rung N as `<Title>.md` files (a title collision gets ` (<id>)`), from pages.jsonl."""
    out.mkdir(parents=True, exist_ok=False)
    names: set[str] = set()
    n = 0
    with open(PAGES, encoding="utf-8") as f:
        for line in f:
            if n >= rung:
                break
            p = json.loads(line)
            name = safe_name(p["title"])
            if name.lower() in names:
                name = f"{name} ({p['id']})"
            names.add(name.lower())
            (out / f"{name}.md").write_text(p["text"], encoding="utf-8")
            n += 1
    if n < rung:
        raise ValueError(f"pages.jsonl holds only {n} pages; build with --upto >= {rung}")
    return {"rung": rung, "files": n, "dir": str(out)}


def verify() -> dict[str, Any]:
    """Streams order.tsv: no duplicate page, every rung's id-list hash matches the manifest (so each
    rung is exactly the first N pages, hence a subset of the next), core pages inside rung 1e2."""
    man = json.loads((RUNGS_DIR / "rungs.json").read_text())
    sizes = {name: r["pages"] for name, r in man["rungs"].items()}
    hashes = {name: hashlib.sha256() for name in sizes}
    seen: set[str] = set()
    first100: set[str] = set()
    i = 0
    with open(RUNGS_DIR / "order.tsv", encoding="utf-8") as f:
        for line in f:
            pos, pid, _rest = line.split("\t", 2)
            assert int(pos) == i and pid not in seen, f"bad or duplicate row {i}: {pid}"
            seen.add(pid)
            if i < 100:
                first100.add(pid)
            for name, n in sizes.items():
                if i < n:
                    hashes[name].update((("\n" if i else "") + pid).encode())
            i += 1
    names = sorted(sizes, key=lambda k: sizes[k])
    for name in names:
        assert hashes[name].hexdigest() == man["rungs"][name]["sha256_ids"], f"rung {name} hash"
    assert [sizes[k] for k in names] == sorted(sizes.values()) and sizes[names[-1]] == i
    core = {c for p in man["core_pairs"] for c in (p["core"], p["linked"])}
    assert core <= first100, "a core/linked page is outside rung 1e2"
    return {"nested": True, "rungs": {k: sizes[k] for k in names}, "core_pages_in_1e2": len(core)}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="kbio.kilt")
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("index")
    i.add_argument("--workers", type=int, default=6)
    sub.add_parser("plan")
    b = sub.add_parser("build")
    b.add_argument("--upto", type=int, default=None)
    m = sub.add_parser("md")
    m.add_argument("--rung", type=int, required=True)
    m.add_argument("out", type=Path)
    sub.add_parser("verify")
    a = ap.parse_args(argv)
    if a.cmd == "index":
        res = build_index(a.workers)
    elif a.cmd == "plan":
        res = plan()
    elif a.cmd == "build":
        res = build(a.upto)
    elif a.cmd == "md":
        res = write_md(a.rung, a.out)
    else:
        res = verify()
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
