"""V3 rung slicer on a synthetic KILT file: parallel index, nested rungs, core pairs, build, md."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kbio import kilt


def rec(i: int, n_par: int, links: list[int], title: str | None = None) -> dict:
    text = [f"T{i}\n"] + [f"Paragraph {k} of page {i}. " * 5 + "\n" for k in range(n_par)]
    if n_par:
        text.insert(2, "Section::::History:::Early.\n")
        text.append("BULLET::::- an item\n")
    return {
        "_id": str(i),
        "wikipedia_id": str(i),
        "wikipedia_title": title or f"T{i}",
        "text": text,
        "anchors": [{"wikipedia_id": str(t), "text": "x"} for t in links],
        "categories": "",
        "history": {"revid": 1000 + i},
    }


@pytest.fixture
def mini(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    recs = [
        rec(i, 0 if i % 13 == 0 else 1 + i % 4, [(i * 7) % 60, (i + 1) % 60]) for i in range(60)
    ]
    recs[5] = rec(5, 4, [6], "List of things")  # never a core page
    src = tmp_path / "kilt.json"
    src.write_text("".join(json.dumps(r) + "\n" for r in recs))
    monkeypatch.setattr(kilt, "SRC", src)
    monkeypatch.setattr(kilt, "INDEX", tmp_path / "index.tsv")
    monkeypatch.setattr(kilt, "RUNGS_DIR", tmp_path / "rungs")
    monkeypatch.setattr(kilt, "PAGES", tmp_path / "pages.jsonl")
    monkeypatch.setattr(kilt, "RUNGS", [10, 30])
    monkeypatch.setattr(kilt, "CORE", 3)
    monkeypatch.setattr(kilt, "CORE_CHARS", 300)
    monkeypatch.setattr(kilt, "LINK_CHARS", 100)
    return tmp_path


def test_index_plan_verify_build(mini: Path) -> None:
    assert kilt.build_index(workers=3)["pages"] == 60  # ranges split mid-line; nothing lost
    ids = [ln.split("\t")[0] for ln in kilt.INDEX.read_text().splitlines()]
    assert sorted(ids, key=int) == [str(i) for i in range(60)]
    res = kilt.plan(pool=20)
    empty = sum(1 for i in range(60) if i % 13 == 0)
    assert res["eligible_pages"] == 60 - empty and res["core_pairs"] == 3
    man = json.loads((kilt.RUNGS_DIR / "rungs.json").read_text())
    assert list(man["rungs"]) == ["1e1", "3e1", "all"]
    assert all(p["core"] != "5" for p in man["core_pairs"])
    v = kilt.verify()
    assert v["nested"] and v["rungs"] == {"1e1": 10, "3e1": 30, "all": 60 - empty}
    first = [ln.split("\t")[1] for ln in (kilt.RUNGS_DIR / "order.tsv").read_text().splitlines()]
    pairs = [x for p in man["core_pairs"] for x in (p["core"], p["linked"])]
    assert first[:6] == pairs  # core pairs lead the order
    assert kilt.plan(pool=20) == res  # deterministic
    out = kilt.build(upto=30)
    assert out["pages"] == 30
    pages = [json.loads(ln) for ln in kilt.PAGES.read_text().splitlines()]
    assert [p["id"] for p in pages] == first[:30]
    t = pages[0]["text"]
    assert (
        t.startswith(f"# T{pages[0]['id']}\n\n")
        and "\n\n### Early\n\n" in t
        and "\n\n- an item\n" in t
    )
    md = kilt.write_md(10, mini / "md")
    assert md["files"] == 10 and len(list((mini / "md").glob("*.md"))) == 10
    with pytest.raises(ValueError):
        kilt.write_md(31, mini / "md2")


def test_verify_catches_tampering(mini: Path) -> None:
    kilt.build_index(workers=2)
    kilt.plan(pool=20)
    order = kilt.RUNGS_DIR / "order.tsv"
    lines = order.read_text().splitlines()
    lines[3], lines[20] = lines[20], lines[3]
    order.write_text("\n".join(lines) + "\n")
    with pytest.raises(AssertionError):
        kilt.verify()
