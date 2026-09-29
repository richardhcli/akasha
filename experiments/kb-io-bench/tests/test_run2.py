"""v2 driver round trip on a synthetic mini rung: crud-kb over MCP, an oracle fake model.

Template ingest -> READ on the template (read-only tools) -> UPDATE/DELETE/CREATE on per-task
copies with an export diff -> follow-ups; the template is never modified."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from kbio import analyze2, probes, run2

OLD = "In the 1884 Straughan Index, the topic of Alpha is catalogued as entry 55-Q."
NEW = OLD.replace("55-Q", "26-X")
GONE = "The Marlow Prize for work on Beta was first awarded in 1851."
PAGES = [
    {"id": "1", "title": "Alpha", "text": "# Alpha\n\nAlpha is a letter.\n"},
    {"id": "2", "title": "Beta", "text": "# Beta\n\nBeta is a letter.\n"},
    {"id": "3", "title": "Gamma", "text": "# Gamma\n\nGamma rays are energetic.\n"},
]


def world(tmp: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    pages = tmp / "pages.jsonl"
    pages.write_text("".join(json.dumps(p) + "\n" for p in PAGES))
    base = {
        "1": f"# Alpha\n\nAlpha is a letter. {OLD}\n",
        "2": f"# Beta\n\nBeta is a letter. {GONE}\n",
        "3": f"# Gamma\n\nGamma rays are energetic. {OLD}\n",
    }
    (tmp / "patches.json").write_text(json.dumps({"base": base, "control": base}))
    q = "Under which entry is Alpha catalogued in the 1884 Straughan Index?"
    tasks = [
        {"id": "read-fi-01", "family": "read", "kind": "single", "subkind": "fictional",
         "question": q, "answer": "55-Q", "answer_values": ["55-Q"], "gold": []},
        {"id": "update-01", "family": "update", "old_sentence": OLD, "new_sentence": NEW,
         "copies": [{"id": "1"}, {"id": "3"}],
         "instruction": f'Correction: "{OLD}" is wrong: {NEW}',
         "followup": {"id": "update-01-q", "question": q, "answer": "26-X",
                      "answer_values": ["26-X"], "stale_values": ["55-Q"]}},
        {"id": "delete-01", "family": "delete", "sentence": GONE, "copies": [{"id": "2"}],
         "instruction": f'Retraction: "{GONE}" is false.',
         "followup": {"id": "delete-01-q", "question": "When was the Marlow Prize for work on "
                      "Beta first awarded?", "answer": "Not in the knowledge base.",
                      "answer_values": [], "zombie_values": ["1851"]}},
        {"id": "create-01", "family": "create",
         "memo": "Archive memo.\n1. The Ellery Prize for work on Gamma was first awarded in 1822.",
         "facts": [{"sentence": "The Ellery Prize for work on Gamma was first awarded in 1822.",
                    "answer": "1822", "page": "3"}], "followups": [{"id": "create-01-q1", "question": "In which year was the "
                                     "Ellery Prize for work on Gamma first awarded?",
                                     "answer": "1822", "answer_values": ["1822"]}]},
    ]  # fmt: skip
    probes_json = tmp / "probes.json"
    probes_json.write_text(json.dumps({"tasks": tasks, "cb": {}}))
    monkeypatch.setattr(probes, "PAGES", pages)
    monkeypatch.setattr(probes, "PATCHES", tmp / "patches.json")
    monkeypatch.setattr(probes, "PROBES", probes_json)
    for name in ("RESULTS", "CORPORA", "STORES", "WORK", "TRASH"):
        monkeypatch.setattr(run2, name, tmp / name.lower())
    monkeypatch.setattr(analyze2, "RESULTS", tmp / "results")
    return {"tasks": tasks}


def oracle(model: str, messages: list[dict], tools: Any = None, max_tokens: int = 0) -> dict:
    """Search the question / sentence, patch every hit, answer from what the tools showed."""
    user = messages[1]["content"]
    results = [m["content"] for m in messages if m["role"] == "tool"]
    n = len(results)

    def call(name: str, args: dict[str, Any]) -> dict:
        fn = {"name": name, "arguments": json.dumps(args)}
        return {"role": "assistant", "content": "",
                "tool_calls": [{"id": f"c{n}", "type": "function", "function": fn}]}  # fmt: skip

    def reply(text: str) -> dict:
        return {"message": {"role": "assistant", "content": text},
                "usage": {"prompt_tokens": 5, "completion_tokens": 1}, "latency_ms": 1.0}  # fmt: skip

    def wrap(msg: dict) -> dict:
        return {"message": msg, "usage": {"prompt_tokens": 5, "completion_tokens": 1},
                "latency_ms": 1.0}  # fmt: skip

    if tools is None:
        return reply("NOT FOUND IN KNOWLEDGE BASE")
    names = {t["function"]["name"] for t in tools}
    if "update" not in names:  # a reader
        if n == 0:
            q = user.split("QUESTION:")[1].strip()
            return wrap(call("search", {"query": q.split(" in the ")[0].split(" for work on ")[0]}))
        seen = " ".join(results)
        for v in ("26-X", "55-Q", "1822", "1851"):
            if v in seen:
                return reply(f'{v}\nSOURCES:\n- 1: "{v}"')
        return reply("NOT FOUND IN KNOWLEDGE BASE")
    if "NEW INFORMATION" in user:
        if n == 0:
            return wrap(call("create", {"title": "Gamma memo", "body": user.split("1. ")[1]}))
        return reply("DONE created one document")
    old = OLD if "Correction" in user else GONE
    new = NEW if "Correction" in user else ""
    if n == 0:
        return wrap(call("search", {"query": old.split(",")[0]}))
    hits = [h["id"] for h in json.loads(results[0])]
    if n <= len(hits):
        return wrap(call("update", {"id": hits[n - 1], "old": old, "new": new}))
    return reply("DONE")


def test_run2_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    w = world(tmp_path, monkeypatch)
    r = run2.Runner("fake", "crud-kb", 3)
    r.chat = oracle
    r.read([w["tasks"][0]])
    rec = json.loads(r.path("read", "read-fi-01").read_text())
    assert rec["agent"]["status"] == "ok" and rec["quick"]["hits"] == ["55-Q"]
    assert rec["ingest"]["docs"] == 3 and rec["ingest"]["wall_s"] > 0
    for t in w["tasks"][1:]:
        r.write_like(t)
    up = json.loads(r.path("update", "update-01").read_text())
    assert sorted(up["diff"]["changed"]) == ["1", "3"]  # both copies patched
    assert all(NEW in d["body"] for d in up["diff"]["changed"].values())
    assert (
        up["followups"][0]["quick"]["hits"] == ["26-X"] and not up["followups"][0]["quick"]["stale"]
    )
    de = json.loads(r.path("delete", "delete-01").read_text())
    assert list(de["diff"]["changed"]) == ["2"] and GONE not in de["diff"]["changed"]["2"]["body"]
    assert de["followups"][0]["quick"]["abstained"] and not de["followups"][0]["quick"]["zombie"]
    cr = json.loads(r.path("create", "create-01").read_text())
    assert len(cr["diff"]["added"]) == 1 and cr["followups"][0]["quick"]["hits"] == ["1822"]
    # the template is untouched: a second READ still sees the old value
    r.read([{**w["tasks"][0], "id": "read-fi-02"}])
    again = json.loads(r.path("read", "read-fi-02").read_text())
    assert again["quick"]["hits"] == ["55-Q"]
    # control arm: the CREATE follow-up on its own template (this synthetic control has no fact)
    r.read(run2.select(w["tasks"], "control", 0, set()), "control", "control")
    ctl = json.loads(r.path("control", "create-01-q1").read_text())
    assert ctl["quick"]["hits"] == [] and ctl["op"] == "control"
    assert (tmp_path / "stores" / "crud-kb" / "3-control" / "ingest.json").exists()
    archived = sorted(p.name.rsplit("-", 1)[0] for p in (tmp_path / "trash").iterdir())
    assert archived == ["crud-kb-3-create-01", "crud-kb-3-delete-01", "crud-kb-3-update-01"]

    # V5 aggregation over these records (offline, deterministic)
    summ = analyze2.summarize("fake")
    c = summ["cells"]["crud-kb"]["3"]
    assert c["read"]["correct"] == 1.0 and c["read"]["tasks"] == 2
    assert c["update"]["effects"]["copies_updated_rate"] == 1.0 and c["update"]["stale_rate"] == 0
    assert c["delete"]["effects"]["copies_removed_rate"] == 1.0 and c["delete"]["zombie_rate"] == 0
    assert c["create"]["correct"] == 1.0 and c["read"]["cite_recall"] is None
    assert analyze2.summarize("fake") == summ
    assert "## crud-kb" in analyze2.markdown(summ)
    g = summ["gate"]["crud-kb/3"]
    assert g["pass"] and g["failing"] == 0 and g["tasks"] == 6
    # a changed probe set invalidates the built corpus and templates: refused, never reused
    patches = tmp_path / "patches.json"
    patches.write_text(patches.read_text().replace("Alpha is a letter.", "Alpha is a symbol."))
    with pytest.raises(SystemExit, match="built from patches"):
        r.read([{**w["tasks"][0], "id": "read-fi-03"}])


def files_oracle(model: str, messages: list[dict], tools: Any = None, max_tokens: int = 0) -> dict:
    """The same oracle for the plain-files harness (grep / read_file / edit_file / write_file)."""
    user = messages[1]["content"]
    results = [m["content"] for m in messages if m["role"] == "tool"]
    n = len(results)

    def wrap(msg: dict) -> dict:
        return {"message": msg, "usage": {"prompt_tokens": 5, "completion_tokens": 1},
                "latency_ms": 1.0}  # fmt: skip

    def call(name: str, args: dict[str, Any]) -> dict:
        fn = {"name": name, "arguments": json.dumps(args)}
        return wrap({"role": "assistant", "content": "",
                     "tool_calls": [{"id": f"c{n}", "type": "function", "function": fn}]})  # fmt: skip

    def reply(text: str) -> dict:
        return wrap({"role": "assistant", "content": text})

    if tools is None:
        return reply("NOT FOUND IN KNOWLEDGE BASE")
    names = {t["function"]["name"] for t in tools}
    if "edit_file" not in names:
        if n == 0:
            q = user.split("QUESTION:")[1]
            term = "Straughan" if "Straughan" in q else "Ellery" if "Ellery" in q else "Marlow"
            return call("grep", {"pattern": term})
        for v in ("26-X", "55-Q", "1822", "1851"):
            if v in results[0]:
                return reply(f'{v}\nSOURCES:\n- Alpha.md: "{v}"')
        return reply("NOT FOUND IN KNOWLEDGE BASE")
    if "NEW INFORMATION" in user:
        if n == 0:
            return call("write_file", {"path": "Gamma memo.md", "content": user.split("1. ")[1]})
        return reply("DONE")
    old = OLD if "Correction" in user else GONE
    new = NEW if "Correction" in user else ""
    if n == 0:
        return call("grep", {"pattern": "Straughan" if "Correction" in user else "Marlow"})
    paths = sorted({ln.split(":", 1)[0] for ln in results[0].splitlines() if ":" in ln})
    if n <= len(paths):
        return call("edit_file", {"path": paths[n - 1], "old": old, "new": new})
    return reply("DONE")


def test_run2_files_harness_md_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    w = world(tmp_path, monkeypatch)
    r = run2.Runner("fake", "files", 3)
    r.chat = files_oracle
    r.read([w["tasks"][0]])
    assert json.loads(r.path("read", "read-fi-01").read_text())["quick"]["hits"] == ["55-Q"]
    for t in w["tasks"][1:]:
        r.write_like(t)
    up = json.loads(r.path("update", "update-01").read_text())
    assert sorted(up["diff"]["changed"]) == ["1", "3"]  # md names mapped back to page ids
    de = json.loads(r.path("delete", "delete-01").read_text())
    assert list(de["diff"]["changed"]) == ["2"] and de["followups"][0]["quick"]["abstained"]
    cr = json.loads(r.path("create", "create-01").read_text())
    assert list(cr["diff"]["added"]) == ["Gamma memo.md"]
    assert cr["followups"][0]["quick"]["hits"] == ["1822"]
    c = analyze2.summarize("fake")["cells"]["files"]["3"]
    assert c["update"]["effects"]["copies_updated_rate"] == 1.0
    assert c["read"]["cite_recall"] is None  # the synthetic READ task has no gold
