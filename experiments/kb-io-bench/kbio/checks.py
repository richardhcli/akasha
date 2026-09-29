"""Scripted harness checks (M5, M6): fixed tool sequences against real snapshots, no LLM."""

from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

from kbio import akasha_ctl as ak
from kbio.corpus import CORPORA
from kbio.harnesses.akasha import AkashaHarness, find_atom
from kbio.paths import DATA

NEW_FACT = "In the 1903 Pellew Index, habit is catalogued as entry 55-Q."


def snapshot(tier: str, fmt: str, label: str) -> Path:
    dst = DATA / "snapshots" / f"{label}-{time.strftime('%Y%m%d-%H%M%S')}" / fmt
    shutil.copytree(CORPORA / tier / fmt, dst)
    return dst


def m5(tier: str = "S") -> dict:
    vault = snapshot(tier, "akasha", "m5")
    setup = ak.reset_store(vault, name=f"m5-{vault.parent.name}")
    man = json.loads((CORPORA / tier / "manifest.json").read_text())
    h = AkashaHarness()
    tools = h.setup(vault, DATA / "tmp")
    log: list[dict] = []

    def call(name: str, **args: object) -> str:
        t0 = time.monotonic()
        out = h.call(name, dict(args))
        log.append({"tool": name, "args": args, "ms": round((time.monotonic() - t0) * 1000),
                    "result": out[:400]})  # fmt: skip
        return out

    res = call("search", query="habit routine behavior")
    lead_id = man["lead_ids"][next(p["pageid"] for p in man["core_pages"]
                                   if p["title"] == "Habit").__str__()]  # fmt: skip
    got = call("get_node", id=lead_id)
    call("neighborhood", id=lead_id)
    call("read_note", path="Human (concept)/Habits.md")
    w = call("write_atom", note="Human (concept)/Habits.md",
             text=NEW_FACT,
             after_heading="Reference")  # fmt: skip
    new_id = w.split()[2]
    t = call("transclude", id=new_id, into_note="Wikipedia/Habit.md", after_heading="Habit")
    time.sleep(1.0)
    e = (
        call("edit_node", id=new_id, text=None)
        if False
        else call(
            "edit_node",
            id=new_id,
            new_text="In the 1903 Pellew Index, habit is catalogued as entry 17-B.",
        )
    )
    # edit a lead copy (a concept note's transcluded definition): must reach every copy
    copies = man["copies"][
        str(next(p["pageid"] for p in man["core_pages"] if p["title"] == "Habit"))
    ]
    before = find_atom((vault / copies[0]).read_text(), lead_id)
    assert before is not None
    new_lead = before[2].replace("subconsciously", "automatically")
    e2 = call("edit_node", id=lead_id, new_text=new_lead)
    reached = sum(
        (a := find_atom((vault / f).read_text(), lead_id)) is not None and a[2] == new_lead
        for f in copies
    )
    new_reached = sum(
        (a := find_atom((vault / f).read_text(), new_id)) is not None and "17-B" in a[2]
        for f in ("Human (concept)/Habits.md", "Wikipedia/Habit.md")
    )
    ak.rescan()
    status = ak.status_counts(root_path=vault)
    report = {
        "vault": str(vault),
        "setup": setup,
        "tools": [x.name for x in tools],
        "search_ok": lead_id in res or "habit" in res.lower(),
        "get_ok": "appears in" in got,
        "write_atom": w,
        "transclude": t,
        "edit_new": e,
        "edit_lead": e2,
        "lead_copies": len(copies),
        "lead_copies_reached": reached,
        "new_node_copies_reached": new_reached,
        "status": status,
        "calls": log,
    }
    report["pass"] = (
        report["search_ok"] and report["get_ok"] and reached == len(copies) and new_reached == 2
        and status["conflicts"] == 0 and status["violations"] == 0 and status["reviews_open"] == 0
    )  # fmt: skip
    (DATA / "logs" / "m5-check.json").write_text(json.dumps(report, indent=1) + "\n")
    return report


def file_hashes(root: Path) -> dict[str, str]:
    import hashlib

    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*.md"))
    }


def strip_frontmatter(text: str) -> str:
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end >= 0:
            return text[end + 5 :].lstrip("\n")
    return text


def vault_changes(src: Path, dst: Path) -> dict:
    a, b = file_hashes(src), file_hashes(dst)
    changed = [f for f in a if f in b and a[f] != b[f]]
    only_fm = [
        f for f in changed
        if strip_frontmatter((dst / f).read_text()).rstrip() == (src / f).read_text().rstrip()
    ]  # fmt: skip
    return {
        "files": len(a),
        "changed": len(changed),
        "changed_only_by_frontmatter": len(only_fm),
        "added": sorted(set(b) - set(a)),
        "removed": sorted(set(a) - set(b)),
        "other_changes": sorted(set(changed) - set(only_fm))[:10],
    }


def m6(tier: str = "S") -> dict:
    from kbio.agent import ToolSpec
    from kbio.harnesses.basic_memory import BasicMemoryHarness
    from kbio.harnesses.files import FilesHarness
    from kbio.llm import ntok

    report: dict = {"conditions": {}}
    for cond, fmt in (("C", "regular"), ("B", "akasha")):
        src = CORPORA / tier / fmt
        vault = snapshot(tier, fmt, f"m6-{cond}")
        r: dict = {"vault": str(vault)}
        if cond == "B":
            r["akasha_setup"] = ak.reset_store(vault, name=f"m6-{vault.parent.name}")
        h = BasicMemoryHarness()
        t0 = time.monotonic()
        tools = h.setup(vault, DATA / "tmp")
        r["bm_setup_seconds"] = round(time.monotonic() - t0, 1)
        r["bm_index_seconds"] = h.index_seconds
        r["bm_indexed"] = h.indexed_count()
        time.sleep(3)
        r["after_index"] = vault_changes(src, vault)
        if cond == "B":
            r["akasha_after_index"] = ak.status_counts(root_path=vault)
        w = h.call("write_note", {
            "title": "Pellew Index memo", "directory": "Memos",
            "content": "- In the 1903 Pellew Index, habit is catalogued as entry 55-Q. [[Habits]]",
        })  # fmt: skip
        found = h.call("search_notes", {"query": "Pellew Index", "search_type": "text"})
        hyphen = h.call("search_notes", {"query": "Pellew Index 55-Q", "search_type": "text"})
        hybrid = h.call("search_notes", {"query": "Pellew Index 55-Q"})
        read = h.call("read_note", {"identifier": "Memos/Pellew Index memo"})
        ctx = h.call("build_context", {"url": "memory://memos/pellew-index-memo"})
        man = json.loads((CORPORA / tier / "manifest.json").read_text())
        habit = str(next(p["pageid"] for p in man["core_pages"] if p["title"] == "Habit"))
        copies = man["copies"][habit]
        e = h.call("edit_note", {
            "identifier": copies[1][:-3], "operation": "find_replace",
            "find_text": "tends to occur subconsciously", "content": "tends to occur automatically",
        })  # fmt: skip
        time.sleep(5)
        r["scripted"] = {
            "write_note": w[:200],
            "search_finds_new_note": "Pellew Index memo" in found,
            "text_search_with_hyphenated_code_finds_it": "Pellew Index memo" in hyphen,
            "hybrid_search_finds_it": "Pellew Index memo" in hybrid,
            "read_note_ok": "55-Q" in read,
            "build_context_ok": "pellew" in ctx.lower() and '"results":[]' not in ctx,
            "edit_note": e[:200],
            "edited_copy_files_with_new_text": sum(
                "tends to occur automatically" in (vault / f).read_text() for f in copies
            ),
            "lead_copies": len(copies),
        }
        if cond == "B":
            ak.rescan()
            time.sleep(3)
            h1 = file_hashes(vault)
            ak.rescan()
            time.sleep(3)
            h2 = file_hashes(vault)
            ak.rescan()
            time.sleep(3)
            h3 = file_hashes(vault)
            r["hash_stable_across_rescans"] = h1 == h2 == h3
            r["files_changed_between_rescans"] = sum(h1[f] != h3.get(f) for f in h1)
            r["akasha_after_writes"] = ak.status_counts(root_path=vault)
        r["tools"] = [t.name for t in tools]
        h.teardown()
        report["conditions"][cond] = r

    def schema_tokens(tools: list[ToolSpec]) -> int:
        return ntok(json.dumps([t.openai() for t in tools]))

    a_vault = snapshot(tier, "akasha", "m6-A")
    ak.reset_store(a_vault, name=f"m6-A-{a_vault.parent.name}")
    fs = FilesHarness().setup(a_vault, DATA / "tmp")
    ak_all = AkashaHarness().setup(a_vault, DATA / "tmp")
    ak_read = AkashaHarness(write_tools=False).setup(a_vault, DATA / "tmp")
    bm = BasicMemoryHarness()
    bm_all = bm.setup(snapshot(tier, "regular", "m6-schema"), DATA / "tmp")
    bm.teardown()
    bm_read = [t for t in bm_all if t.name in ("search_notes", "read_note", "build_context")]
    fs_read = [t for t in fs if t.name in ("list_dir", "grep", "read_file")]
    report["schema_tokens"] = {
        "akasha_read": schema_tokens(ak_read), "akasha_all": schema_tokens(ak_all),
        "basic_memory_read": schema_tokens(bm_read), "basic_memory_all": schema_tokens(bm_all),
        "files_read": schema_tokens(fs_read), "files_all": schema_tokens(fs),
    }  # fmt: skip
    b = report["conditions"]["B"]
    report["pass"] = (
        all(c["scripted"]["search_finds_new_note"] and c["scripted"]["read_note_ok"]
            for c in report["conditions"].values())
        and b["hash_stable_across_rescans"]
        and b["akasha_after_writes"]["conflicts"] == 0
    )  # fmt: skip
    (DATA / "logs" / "m6-check.json").write_text(json.dumps(report, indent=1) + "\n")
    return report


BMTPL_QUERIES = (
    {"query": "habit"},  # a plain term
    {"query": "Achterberg Index 97-A", "search_type": "text"},  # a hyphenated planted code
    {"query": "how people form automatic routines"},  # semantic-ish (hybrid default)
)


def db_counts(db: Path) -> tuple[int, int]:
    import sqlite3

    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        e = con.execute("SELECT count(*) FROM entity").fetchone()[0]
        v = con.execute("SELECT count(*) FROM search_vector_chunks").fetchone()[0]
        return e, v
    finally:
        con.close()


def bmtpl(tier: str = "M") -> dict:
    """Verify --bm-template: a restored snapshot needs no re-embed, leaves the files alone, and
    searches exactly like a freshly indexed one."""
    import re

    from kbio.run import CONDITIONS, Env, bm_template

    def permalinks(out: str) -> list[str]:
        return re.findall(r'"permalink":\s*"([^"]+)"', out) or re.findall(r"permalink: (\S+)", out)

    def probe(env: Env) -> dict:
        outs = [env.h.call("search_notes", dict(q)) for q in BMTPL_QUERIES]
        return {"outputs": outs, "permalinks": [permalinks(o) for o in outs]}

    report: dict = {"tier": tier, "conditions": {}}
    for cond in ("B", "C"):
        fmt = CONDITIONS[cond]["fmt"]
        r: dict = {}
        t0 = time.monotonic()
        tpl = bm_template(tier, cond)
        r["template_seconds"] = round(time.monotonic() - t0, 1)
        r["template_setup"] = json.loads((tpl / "marker.json").read_text())["setup"]
        r["template_counts"] = db_counts(tpl / "memory.db")
        with Env(tier, cond, "bmtpl-restored", tpl) as env:
            assert env.vault is not None
            r["restored_setup"] = dict(env.setup_info)
            r["restored_counts"] = env.h.indexed_count()
            r["restored_files_unchanged"] = file_hashes(env.vault) == file_hashes(tpl / fmt)
            restored = probe(env)
            if cond == "B":
                r["akasha_status"] = env.status()
        with Env(tier, cond, "bmtpl-fresh") as env:
            r["fresh_setup"] = dict(env.setup_info)
            r["fresh_counts"] = env.h.indexed_count()
            fresh = probe(env)
        r["search_identical"] = restored["outputs"] == fresh["outputs"]
        r["search_same_permalinks"] = restored["permalinks"] == fresh["permalinks"]
        r["search_hits"] = [len(x) for x in restored["permalinks"]]
        if not r["search_identical"]:
            r["search_diff"] = [
                {"restored": a[:600], "fresh": b[:600]}
                for a, b in zip(restored["outputs"], fresh["outputs"], strict=True) if a != b
            ]  # fmt: skip
        st = r.get("akasha_status")
        r["pass"] = (
            r["restored_setup"]["bm_index_seconds"] < 15
            and r["restored_files_unchanged"]
            and tuple(r["restored_counts"]) == tuple(r["template_counts"])
            and r["search_same_permalinks"]
            and (st is None or (st["violations"] == 0 and st["conflicts"] == 0
                                and st["reviews_open"] == 0))
        )  # fmt: skip
        report["conditions"][cond] = r
    report["pass"] = all(c["pass"] for c in report["conditions"].values())
    (DATA / "logs" / f"bmtpl-check-{tier}.json").write_text(json.dumps(report, indent=1) + "\n")
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("check", choices=["m5", "m6", "bmtpl"])
    ap.add_argument("--tier", default="S")
    args = ap.parse_args()
    r = {"m5": m5, "m6": m6, "bmtpl": bmtpl}[args.check](args.tier)
    print(json.dumps({k: v for k, v in r.items() if k != "calls"}, indent=1))
    for c in r.get("calls", []):
        print(c["tool"], c["ms"], "ms |", c["result"][:250].replace("\n", " / "))


if __name__ == "__main__":
    main()
