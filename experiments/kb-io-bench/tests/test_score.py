"""Code scoring: citation parsing/resolution, WRITE landings, UPDATE copies, judge blinding."""

from __future__ import annotations

from kbio import score

TEXTS = {
    "Wikipedia/Habit.md": "# Habit\n\nA habit is a routine. ^tm-ynmoqitt\n\nEntry 41-C here. ^tm-abcdefgh\n",
    "Human (concept)/Habits.md": "# Habits\n\nA habit is a routine. ^tm-ynmoqitt\n",
    "Engineering/Artificial Intelligence.md": "x\n",
    "Wikipedia/Artificial intelligence.md": "y\n",
}


def test_split_answer_curly_quotes() -> None:
    final = 'It is **41-C**.\n\nSOURCES:\n- Wikipedia/Habit.md: “Entry 41-C here.”\n- ynmoqitt: "A habit"'
    body, cites = score.split_answer(final)
    assert body == "It is **41-C**."
    assert cites == [("Wikipedia/Habit.md", "Entry 41-C here."), ("ynmoqitt", "A habit")]


def test_resolver() -> None:
    r = score.Resolver(TEXTS)
    assert r.resolve("Wikipedia/Habit.md") == ({"Wikipedia/Habit.md"}, "path")
    assert r.resolve("ynmoqitt")[0] == {"Wikipedia/Habit.md", "Human (concept)/Habits.md"}
    assert r.resolve("memory://kb/wikipedia/habit") == ({"Wikipedia/Habit.md"}, "permalink")
    assert r.resolve("Artificial intelligence")[1] == "title"
    assert r.resolve("artificial INTELLIGENCE")[1] == "ambiguous"


def test_score_read_citation_metrics() -> None:
    task = {
        "question": "q",
        "answer_values": ["41-C"],
        "gold": [{"span": "Entry 41-C here.", "source": "Wikipedia/Habit.md"}],
    }
    sc = score.score_read(
        task, 'It is 41-C.\nSOURCES:\n- Wikipedia/Habit.md: "Entry 41-C here."', TEXTS
    )
    assert sc["exact_hit"] and not sc["abstained"]
    assert sc["cited_gold_span_recall"] == 1.0 and sc["cited_source_precision"] == 1.0


def test_fact_marker_and_write_scoring() -> None:
    sent = "In the 1882 Pemberton Index, the topic of Pattern is catalogued as entry 80-M."
    assert score.fact_marker(sent) == ("Pemberton Index", "80-M")
    person = (
        "The earliest surviving field notes on Habit are credited to the surveyor Edith Marlow."
    )
    assert score.fact_marker(person) == ("surveyor", "Edith Marlow")
    task = {"facts": [{"sentence": sent, "wiki_file": "Wikipedia/Pattern.md",
                       "concept_notes": ["(1) Universal/Pattern (concept).md"]}]}  # fmt: skip
    before = {"(1) Universal/Pattern (concept).md": "# Pattern\n\n80-M appears alone.\n"}
    after = {
        "(1) Universal/Pattern (concept).md": before["(1) Universal/Pattern (concept).md"]
        + "\nIn the 1882 Pemberton Index, Pattern is entry 80-M. ^tm-aaaaaaaa\n",
        "Memo.md": "Pemberton Index: 80-M ^tm-aaaaaaaa\n\nPemberton Index lists 80-M again\n",
    }
    ws = score.score_write(task, before, after)
    f = ws["facts"][0]
    assert f["landed"] and f["linked"]
    assert f["copies"] == 3 and f["distinct"] == 2 and f["duplicates"] == 1


def test_update_scoring() -> None:
    task = {
        "old_sentence": "Truth is conformity to reality or fact.",
        "new_sentence": "Truth is opposition to reality or fact.",
        "old_phrase": "conformity to reality",
        "new_phrase": "opposition to reality",
    }
    after = {
        "a.md": "Truth is opposition to reality or fact. ^tm-x",
        "b.md": "Truth is conformity to reality or fact.",
    }
    us = score.score_update(task, ["a.md", "b.md"], after)
    assert us["copies_updated"] == 1 and us["copies_stale"] == 1
    assert score.update_answer(task, "Truth is opposition to reality or fact.")["update_correct"]
    assert score.update_answer(task, "It says: Truth is conformity to reality or fact")[
        "update_stale"
    ]


def test_neutral_strips_ids_and_paths() -> None:
    s = score.neutral(
        "See ynmoqitt and Wikipedia/Habit.md and memory://kb/x (tm-abcdefgh).", {"ynmoqitt"}
    )
    assert "ynmoqitt" not in s and ".md" not in s and "memory" not in s and "tm-" not in s
