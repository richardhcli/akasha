"""V5 scoring: verdicts per question kind, id citations, CREATE/UPDATE/DELETE effects."""

from __future__ import annotations

from kbio import score2

NF = "NOT FOUND IN KNOWLEDGE BASE"


def test_verdicts() -> None:
    single = {"answer_values": ["55-Q"]}
    assert score2.verdict(single, "It is 55‑Q.")["correct"]  # U+2011 folds to "-"
    assert not score2.verdict(single, NF)["correct"]
    agg = {"answer_values": ["A1", "B2"], "answer_mode": "all"}
    v = score2.verdict(agg, "A1 only")
    assert not v["correct"] and v["partial"] == 0.5
    assert score2.verdict(agg, "A1 and B2")["correct"]
    un = {"answer_values": [], "kind": "unanswerable"}
    assert score2.verdict(un, NF)["correct"] and not score2.verdict(un, "1850")["correct"]
    upd = {"answer_values": ["26-X"], "stale_values": ["55-Q"]}
    assert score2.verdict(upd, "26-X")["correct"]
    v = score2.verdict(upd, "55-Q (was 26-X?)")
    assert v["stale"] and not v["correct"]
    dele = {"answer_values": [], "zombie_values": ["1851"]}
    assert score2.verdict(dele, NF)["correct"]
    v = score2.verdict(dele, "1851")
    assert v["zombie"] and not v["correct"]
    assert score2.verdict({"answer_values": []}, "free text")["correct"] is None


def test_citations_by_id_md_title() -> None:
    ids = score2.Ids({"1": "Alpha", "2": "Beta", "3": "Beta"}, {"1": "Alpha.md", "2": "Beta.md",
                                                                "3": "Beta (3).md"})  # fmt: skip
    assert ids.resolve("1") == "1" and ids.resolve("`Alpha.md`") == "1"
    # "Beta" is an ambiguous title, but the md name `Beta.md` is unique (write_md's rule)
    assert ids.resolve("Alpha") == "1" and ids.resolve("Beta") == "2"
    assert ids.resolve("Epsilon") is None
    assert ids.resolve("Beta (3).md") == "3" and ids.resolve("id: 2") == "2"
    gold = [{"span": "s", "id": "1"}, {"span": "s", "id": "2"}, {"span": "h", "id": "3",
                                                                 "role": "hop"}]  # fmt: skip
    c = score2.citations({}, 'x\nSOURCES:\n- 2: "s"\n- 9: "t"', ids, gold)
    # "9" is an id-shaped citation to a non-gold document: it counts against precision
    assert c["recall"] == 1.0 and c["precision"] == 0.5 and c["n_citations"] == 2


def test_effects() -> None:
    s = "The Marlow Prize for work on Beta was first awarded in 1851."
    body = " ".join(["Some sentence of page text."] * 5)
    base = {"1": f"Alpha text. {s}", "2": f"{body} {s}", "3": "Gamma text."}
    task = {"sentence": s, "copies": [{"id": "1"}, {"id": "2"}]}
    diff = {"changed": {"1": {"title": "Alpha", "body": "Alpha text."}}, "added": {},
            "removed": ["2"]}  # fmt: skip
    e = score2.delete_effects(task, diff, base)
    assert e["copies_removed"] == 2 and e["docs_deleted"] == 1 and e["collateral_docs"] == ["2"]
    new = s.replace("1851", "1799")
    ut = {"old_sentence": s, "new_sentence": new, "copies": [{"id": "1"}, {"id": "2"}]}
    diff = {"changed": {"1": {"title": "Alpha", "body": f"Alpha text. {new}"}}, "added": {},
            "removed": []}  # fmt: skip
    u = score2.update_effects(ut, diff, base)
    assert u["copies_updated"] == 1 and u["copies_stale"] == 1
    fact = "The Ellery Prize for work on Gamma was first awarded in 1822."
    ct = {"facts": [{"sentence": fact, "answer": "1822", "page": "3"}]}
    diff = {"changed": {}, "added": {"n1": {"title": "Ellery Prize", "body": "- Gamma: 1822"}},
            "removed": []}  # fmt: skip
    c = score2.create_effects(ct, diff)
    assert c["landed_rate"] == 0 and c["landed_relaxed_rate"] == 1
