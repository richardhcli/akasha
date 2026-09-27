"""Build the clean and akasha datasets from datasets/regular, then the retrieval units of
every condition (units/<condition>.json). Asserts every gold span survives in every condition."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass, field

from common import AKASHA, CLEAN, DATA, REGULAR, WIKILINK_RE, clean_note, note_title

A = "abcdefghijklmnopqrstuvwxyz234567"  # akasha id alphabet (mvp-spec §4.1)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
LIST_START_RE = re.compile(r"^([-*+]|\d+[.)])\s")
MAX_SECTION_TOKENS = 400
SPAN_SYNTAX_RE = re.compile(r"\}\{tm-[a-z2-7]{8}\}")


def tm_id(seed: str) -> str:
    """7 hash-derived core chars + the spec's weighted checksum char (deterministic)."""
    digest = hashlib.sha256(seed.encode()).digest()
    core = "".join(A[b % 32] for b in digest[:7])
    return core + A[sum((i + 1) * A.index(c) for i, c in enumerate(core)) % 32]


@dataclass
class Block:
    kind: str  # heading | atom
    lines: list[str]
    heading_path: list[str] = field(default_factory=list)
    id: str = ""
    start: int = 0  # index of the first source line


def parse_blocks(text: str) -> list[Block]:
    """Top-level list item + its indented children | run of indented lines | paragraph."""
    blocks: list[Block] = []
    path: list[tuple[int, str]] = []
    cur: Block | None = None

    def flush() -> None:
        nonlocal cur
        if cur is not None:
            blocks.append(cur)
        cur = None

    for idx, line in enumerate(text.split("\n")):
        h = HEADING_RE.match(line)
        if h:
            flush()
            level = len(h.group(1))
            path = [p for p in path if p[0] < level] + [(level, h.group(2).strip())]
            blocks.append(Block("heading", [line], [p[1] for p in path], start=idx))
            continue
        if not line.strip():
            flush()
            continue
        hp = [p[1] for p in path]
        indented = line[0] in " \t"
        if LIST_START_RE.match(line):
            flush()
            cur = Block("atom", [line], hp, start=idx)
        elif indented:
            if cur is None:
                cur = Block("atom", [line], hp, start=idx)
            else:
                cur.lines.append(line)
        else:  # paragraph line
            if cur is not None and (LIST_START_RE.match(cur.lines[0]) or cur.lines[0][0] in " \t"):
                flush()
            if cur is None:
                cur = Block("atom", [line], hp, start=idx)
            else:
                cur.lines.append(line)
    flush()
    # a block with no letters or digits (a `---` rule, a lone `-`) is layout, not an atom
    for b in blocks:
        if b.kind == "atom" and not re.search(r"[A-Za-z0-9]", "\n".join(b.lines)):
            b.kind = "layout"
    return blocks


def balanced(s: str) -> bool:
    depth = 0
    for ch in s:
        depth += (ch == "{") - (ch == "}")
        if depth < 0:
            return False
    return depth == 0


def akasha_source(block: Block) -> str:
    """The block in contract grammar v1: `text ^tm-id` for one plain line, else a span."""
    body = "\n".join(block.lines)
    if len(block.lines) == 1 and block.lines[0][0] not in " \t":
        return f"{body} ^tm-{block.id}"
    if not balanced(body):
        raise ValueError(f"unbalanced braces cannot form a span: {body[:80]!r}")
    first = block.lines[0]
    m = re.match(r"^(\s*(?:[-*+]|\d+[.)]|>)\s+)", first)
    cut = m.end() if m else len(first) - len(first.lstrip())
    lines = [first[:cut] + "{" + first[cut:]] + block.lines[1:]
    lines[-1] += "}{tm-" + block.id + "}"
    return "\n".join(lines)


def strip_akasha(s: str) -> str:
    s = re.sub(r"\s*\^tm-[a-z2-7]{8}\s*$", "", s, flags=re.M)
    s = SPAN_SYNTAX_RE.sub("", s)
    return s


def definition_atom(blocks: list[Block]) -> Block | None:
    for b in blocks:
        if b.kind == "atom" and re.match(r"^\s*(-\s*)?\**Definition", b.lines[0], re.I):
            content = re.sub(r"(?i)^\s*(-\s*)?\**Definition[^:]*:\**\s*", "", b.lines[0]).strip()
            if len(content.strip('"').strip()) >= 15:
                return b
    return None


def section_units(rel: str, text: str) -> list[dict]:
    """Regular chunking: one chunk per heading section, split at blank lines past the cap."""
    title = note_title(rel)
    units, path, buf = [], [], []

    def emit() -> None:
        body = "\n".join(buf).strip()
        if not body:
            return
        pieces, cur = [], []
        for para in re.split(r"\n\s*\n", body):
            if cur and approx_tokens("\n\n".join(cur + [para])) > MAX_SECTION_TOKENS:
                pieces.append("\n\n".join(cur))
                cur = []
            cur.append(para)
        pieces.append("\n\n".join(cur))
        for p in pieces:
            units.append(
                {"source": rel, "title": title, "heading_path": [h for _, h in path], "body": p}
            )

    for line in text.split("\n"):
        h = HEADING_RE.match(line)
        if h:
            emit()
            buf = [line]
            level = len(h.group(1))
            path = [p for p in path if p[0] < level] + [(level, h.group(2).strip())]
        else:
            buf.append(line)
    emit()
    return units


def approx_tokens(s: str) -> int:
    return len(s) // 4 + 1


def main() -> None:
    notes = json.loads((DATA / "datasets" / "subset-manifest.json").read_text())["notes"]
    raw = {n: (REGULAR / n).read_text(encoding="utf-8").replace("\r\n", "\n") for n in notes}
    clean = {n: clean_note(raw[n]) for n in notes}
    by_name = {note_title(n).lower(): n for n in notes}
    for d in (CLEAN, AKASHA):
        if d.exists():
            shutil.rmtree(d)

    parsed: dict[str, list[Block]] = {}
    for n in notes:
        blocks = parse_blocks(clean[n])
        for i, b in enumerate(blocks):
            if b.kind == "atom":
                b.id = tm_id(f"{n}\x00{i}\x00" + "\n".join(b.lines))
        parsed[n] = blocks
    defs = {n: d for n in notes if (d := definition_atom(parsed[n]))}

    units: dict[str, list[dict]] = {"raw": [], "clean": [], "clean-atoms": [], "akasha": []}
    stats = {
        "atoms": 0,
        "span_atoms": 0,
        "line_atoms": 0,
        "mirrored_definitions": 0,
        "notes_with_definition": len(defs),
        "embeds_transcluded": 0,
    }
    for n in notes:
        (CLEAN / n).parent.mkdir(parents=True, exist_ok=True)
        (CLEAN / n).write_text(clean[n], encoding="utf-8")
        units["raw"] += section_units(n, raw[n])
        units["clean"] += section_units(n, clean[n])

        out = clean[n].split("\n")
        linked: list[str] = []
        for b in parsed[n]:
            if b.kind != "atom":
                continue
            src = akasha_source(b)
            stats["atoms"] += 1
            stats["span_atoms" if src.endswith("}") else "line_atoms"] += 1
            out[b.start] = src
            for k in range(b.start + 1, b.start + len(b.lines)):
                out[k] = None  # type: ignore[call-overload]  # folded into the span above
            body = "\n".join(b.lines)
            links = []
            for m in WIKILINK_RE.finditer(body):
                t = by_name.get(m.group(1).strip().lower())
                if t and t != n and t in defs and t not in links:
                    links.append(t)
                    if t not in linked:
                        linked.append(t)
            common = {
                "source": n,
                "title": note_title(n),
                "heading_path": b.heading_path,
                "body": body,
            }
            units["clean-atoms"].append(dict(common))
            units["akasha"].append(dict(common, id=b.id, links=[defs[t].id for t in links]))
        text = "\n".join(line for line in out if line is not None)
        if linked:
            mirrored = ["## Linked definitions"]
            for t in linked:
                mirrored += [f"### [[{note_title(t)}]]", akasha_source(defs[t])]
                stats["mirrored_definitions"] += 1
            text += "\n\n" + "\n\n".join(mirrored)
        (AKASHA / n).parent.mkdir(parents=True, exist_ok=True)
        (AKASHA / n).write_text(text.rstrip() + "\n", encoding="utf-8")

    # gold spans must survive every condition, inside a single unit
    questions = json.loads((DATA / "questions.json").read_text())
    for q in questions:
        for g in q["gold"]:
            for cond, us in units.items():
                hits = [u for u in us if g["span"] in u["body"] and u["source"] == g["source"]]
                assert hits, (cond, q["id"], g["span"])
            assert g["span"] in strip_akasha((AKASHA / g["source"]).read_text()), (
                q["id"],
                g["span"],
            )
    out_dir = DATA / "units"
    out_dir.mkdir(exist_ok=True)
    for cond, us in units.items():
        (out_dir / f"{cond}.json").write_text(json.dumps(us, indent=1, ensure_ascii=False) + "\n")
    stats.update({f"units_{c}": len(u) for c, u in units.items()})
    stats["bytes"] = {
        "regular": sum(len(raw[n].encode()) for n in notes),
        "clean": sum(len(clean[n].encode()) for n in notes),
        "akasha": sum(len((AKASHA / n).read_bytes()) for n in notes),
    }
    (DATA / "datasets" / "build-stats.json").write_text(json.dumps(stats, indent=2) + "\n")
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
