"""Pick the subset by link closure: seed folders + their resolved 1-hop link targets,
dropping near-empty notes; copy verbatim into datasets/regular (the regular dataset)."""

from __future__ import annotations

import json
import shutil
from collections import Counter

from common import REGULAR, SOURCE_VAULT, WIKILINK_RE, content_chars

SEEDS = ("Techniques/", "Personal Workflow/")
MIN_CONTENT = 250  # non-whitespace chars after the boilerplate strip
CAP = 60


def main() -> None:
    files = sorted(str(p.relative_to(SOURCE_VAULT)) for p in SOURCE_VAULT.rglob("*.md"))
    by_name = {}
    for f in files:
        by_name.setdefault(f.rsplit("/", 1)[-1][:-3].lower(), f)
    text = {f: (SOURCE_VAULT / f).read_text(encoding="utf-8", errors="replace") for f in files}
    size = {f: content_chars(text[f]) for f in files}

    def links(f: str) -> list[str]:
        out = []
        for m in WIKILINK_RE.finditer(text[f]):
            t = by_name.get(m.group(1).strip().lower())
            if t and t != f:
                out.append(t)
        return out

    seeds = [f for f in files if f.startswith(SEEDS) and size[f] >= MIN_CONTENT]
    # 1-hop targets ranked by how many seeds link to them
    votes = Counter(
        t for s in seeds for t in set(links(s)) if t not in seeds and size[t] >= MIN_CONTENT
    )
    chosen = seeds + [t for t, _ in votes.most_common(max(0, CAP - len(seeds)))]
    chosen = sorted(chosen)
    if REGULAR.exists():
        shutil.rmtree(REGULAR)
    for f in chosen:
        dst = REGULAR / f
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SOURCE_VAULT / f, dst)
    internal = sum(1 for f in chosen for t in links(f) if t in chosen)
    total = sum(len(links(f)) for f in chosen)
    manifest = {
        "source": str(SOURCE_VAULT),
        "rule": f"seed folders {SEEDS} with >= {MIN_CONTENT} content chars, "
        f"plus top 1-hop link targets, cap {CAP}",
        "notes": chosen,
        "resolved_links": total,
        "links_inside_subset": internal,
        "raw_bytes": sum(len(text[f].encode()) for f in chosen),
    }
    (REGULAR.parent / "subset-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({k: v for k, v in manifest.items() if k != "notes"}, indent=2), len(chosen))
    for f in chosen:
        print(f"{size[f]:6d}  {f}")


if __name__ == "__main__":
    main()
