"""`kbio run2`: the v2 driver (V2-PLAN §4, §6): any model x any MCP harness x one KILT rung.

`python -m kbio.run2 --model M --harness H --rung N --ops read,create,update,delete,control`

Per (harness, rung, arm) the background is bulk-loaded once into a template store (the harness
manifest's `ingest` step, no LLM; wall time and the step's own JSON stats are kept). READ tasks
run on the template with read-only tools. Every CREATE/UPDATE/DELETE task copies the template
store into a fresh per-task store, runs the writer, exports the store (white-box, never shown to
agents), then fresh readers answer the follow-ups on that store with read-only tools. `control`
asks the CREATE follow-ups on the control arm (the same facts bulk-inserted), which separates
write-path from read-path losses. Results go to data/.../v2/results/<model>/<harness>/<rung>/
<op>/<task>.json, atomically; a finished task is never rerun, an unfinished one restarts.

v2-only manifest keys, read here: `corpus = "jsonl" | "md"` (what `ingest` takes) and
`[export] command` (prints every live document as JSON lines {id, title, body}).
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import shutil
import subprocess
import time
import tomllib
import traceback
from pathlib import Path
from typing import Any

from kbio import llm, probes, score
from kbio.agent import ToolSpec, run_agent
from kbio.harnesses.mcp import McpHarness, load_manifest
from kbio.paths import CONFIG, DATA, EXP
from kbio.prompts2 import CREATE, DELETE, READ, SYSTEM, UPDATE
from kbio.run import Guard, model_dir, save
from kbio.textmode import text_chat

V2 = probes.V2
RESULTS = V2 / "results"
CORPORA = V2 / "corpora"
STORES = V2 / "stores"
WORK = V2 / "work"
TRASH = DATA / "trash" / "v2-work"
STEPS = {"read": 12, "create": 20, "update": 20, "delete": 20}
OPS = ["read", "create", "update", "delete", "control"]


def rung_name(n: int) -> str:
    return f"1e{len(str(n)) - 1}" if str(n).strip("0") == "1" else str(n)


class Harness:
    """A manifest plus the v2 keys; builds templates and per-task stores."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.m = load_manifest(name)
        raw = tomllib.loads((CONFIG / "harnesses" / f"{name}.toml").read_text())
        self.corpus_kind = raw.get("corpus", "jsonl")
        # `export.command` (prints JSON lines) or `export.mode = "vault"` (md files under
        # {store}/vault, named back to page ids through the corpus's `.ids.json`)
        self.export_cmd = raw.get("export", {}).get("command")
        self.export_vault = raw.get("export", {}).get("mode") == "vault"

    def corpus(self, rung: int, arm: str) -> Path:
        """The patched rung; refuses one built from different patches (stale probe set)."""
        ext = "" if self.corpus_kind == "md" else ".jsonl"
        out = CORPORA / f"{rung_name(rung)}-{arm}{ext}"
        if not out.exists():
            probes.corpus(rung, arm, out, as_md=self.corpus_kind == "md")
        meta = out.parent / f"{out.name}.meta.json"
        sha = json.loads(meta.read_text())["patches_sha"] if meta.exists() else None
        if sha != probes.patches_sha():
            raise SystemExit(f"{out}: built from patches {sha}, now {probes.patches_sha()}; "
                             "move it (and its templates) to trash and rerun")  # fmt: skip
        return out

    def _mcp(self, kb: Path, root: Path, write: bool, ingest: bool) -> McpHarness:
        h = McpHarness(self.m, write_tools=write, ingest=ingest)
        h.kb, h.store = kb.resolve(), (root / "store").resolve()
        return h

    def template(self, rung: int, arm: str) -> tuple[Path, Path, dict[str, Any]]:
        """(corpus, template root, ingest stats); the store is built once (atomic via rename).
        The root also keeps `export-index.json` ({id: hash}) for per-task export diffs."""
        kb = self.corpus(rung, arm)
        root = STORES / self.name / f"{rung_name(rung)}-{arm}"
        info = root / "ingest.json"
        if not info.exists():
            tmp = root.with_name(root.name + ".partial")
            if tmp.exists():
                TRASH.mkdir(parents=True, exist_ok=True)
                tmp.rename(TRASH / f"{tmp.name}-{int(time.time())}")
            (tmp / "store").mkdir(parents=True)
            if self.corpus_kind == "md":  # the vault is part of the store: writers edit it
                shutil.copytree(kb, tmp / "store" / "vault")
            h = self._mcp(kb, tmp, write=False, ingest=True)
            if self.m.reset:
                h._step(self.m.reset, "reset")
            stats = h._step(self.m.ingest, "ingest") if self.m.ingest else {}
            stats["store_bytes"] = sum(
                p.stat().st_size for p in (tmp / "store").rglob("*") if p.is_file()
            )
            if self.export_cmd or self.export_vault:
                self.export(kb, tmp, tmp / "export.jsonl")
                idx = {d["id"]: doc_hash(d) for d in iter_jsonl(tmp / "export.jsonl")}
                (tmp / "export-index.json").write_text(json.dumps(idx) + "\n")
                (tmp / "export.jsonl").unlink()
                stats["docs"] = len(idx)
            stats["patches_sha"] = probes.patches_sha()
            (tmp / "ingest.json").write_text(json.dumps(stats, indent=1) + "\n")
            tmp.rename(root)
        stats = json.loads(info.read_text())
        if stats.get("patches_sha") != probes.patches_sha():
            raise SystemExit(f"{root}: template from patches {stats.get('patches_sha')}, now "
                             f"{probes.patches_sha()}; move it to trash and rerun")  # fmt: skip
        return kb, root, stats

    def export(self, kb: Path, root: Path, out: Path) -> None:
        """White-box export (never a tool): every live document as JSON lines, to a file."""
        if self.export_vault:
            vault = root / "store" / "vault"
            ids = json.loads((kb.parent / f"{kb.name}.ids.json").read_text())
            with open(out, "w", encoding="utf-8") as f:
                for p in sorted(vault.rglob("*.md")):
                    rel = str(p.relative_to(vault))
                    if any(part.startswith(".") for part in p.relative_to(vault).parts):
                        continue
                    d = {"id": ids.get(rel, rel), "title": p.stem,
                         "body": p.read_text(encoding="utf-8", errors="replace")}  # fmt: skip
                    f.write(json.dumps(d, ensure_ascii=False) + "\n")
            return
        h = self._mcp(kb, root, write=False, ingest=False)
        with open(out, "w", encoding="utf-8") as f:
            subprocess.run(h._fill(self.export_cmd), cwd=EXP, env=h._env(), stdout=f, check=True)

    def open(self, kb: Path, root: Path, write: bool) -> tuple[McpHarness, list[ToolSpec]]:
        """A live session on an existing store: never reset, never re-ingested."""
        m = dataclasses.replace(self.m, reset=None, ingest=None)
        h = McpHarness(m, write_tools=write, ingest=False)
        tools = h.setup(kb, root)
        return h, tools


def iter_jsonl(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def doc_hash(d: dict[str, Any]) -> str:
    return hashlib.sha1(f"{d['title']}\0{d['body']}".encode()).hexdigest()


def export_diff(index: dict[str, str], after: Path) -> dict[str, Any]:
    """Documents changed / added (full after-text) and removed (ids) against the template index.
    Before-texts are the patched corpus (probes.corpus), so they are not stored again."""
    seen: set[str] = set()
    changed, added = {}, {}
    for d in iter_jsonl(after):
        seen.add(d["id"])
        if d["id"] not in index:
            added[d["id"]] = {"title": d["title"], "body": d["body"]}
        elif index[d["id"]] != doc_hash(d):
            changed[d["id"]] = {"title": d["title"], "body": d["body"]}
    return {"changed": changed, "added": added, "removed": sorted(set(index) - seen),
            "docs_after": len(seen)}  # fmt: skip


def chat_for(model: str) -> tuple[Any, str]:
    mode = "native"
    cfg = tomllib.loads((CONFIG / "models.toml").read_text())
    for m in cfg.get("model", []):
        if m["name"] == model:
            mode = m.get("tool_mode", "native")
    if mode == "auto":
        raise SystemExit(f"{model}: tool_mode=auto; run textmode.pick_tool_mode and set it first")
    return (text_chat(llm.chat) if mode == "text" else llm.chat), mode


def read_tools(h: McpHarness, tools: list[ToolSpec], m: Any) -> tuple[Guard, list[ToolSpec]]:
    allowed = set(m.read)
    return Guard(h, allowed), [t for t in tools if t.name in allowed]


def quick(task: dict[str, Any], final: str) -> dict[str, Any]:
    """Text-only fields at run time (V5 rescoring is authoritative)."""
    body, _c = score.split_answer(final)
    nb = score.norm(body)
    return {
        "abstained": score.norm(score.NOT_FOUND) in nb,
        "hits": [v for v in task.get("answer_values", []) if score.norm(v) in nb],
        "stale": [v for v in task.get("stale_values", []) if score.norm(v) in nb],
        "zombie": [v for v in task.get("zombie_values", []) if score.norm(v) in nb],
    }


def ask(model: str, chat: Any, h: Any, tools: list[ToolSpec], q: dict[str, Any]) -> dict:
    try:
        out = run_agent(model, SYSTEM, READ.format(question=q["question"]), h, tools,
                        STEPS["read"], chat=chat)  # fmt: skip
    except Exception as e:  # a harness crash is a FAILED run, never a crashed job
        out = {"status": "FAILED", "final": "", "error": f"{type(e).__name__}: {e}",
               "traceback": traceback.format_exc()[-2000:]}  # fmt: skip
    return {"task": q, "agent": out, "quick": quick(q, out.get("final") or "")}


class Runner:
    def __init__(
        self, model: str, harness: str, rung: int, prune: bool = False,
        drop: set[str] | None = None, probes_sha: str = "",
    ) -> None:  # fmt: skip
        self.model, self.rung, self.prune = model, rung, prune
        self.drop, self.probes_sha = drop or set(), probes_sha
        self.h = Harness(harness)
        self.chat, self.mode = chat_for(model)
        self.base = RESULTS / model_dir(model) / harness / rung_name(rung)

    def path(self, op: str, tid: str) -> Path:
        return self.base / op / f"{tid}.json"

    def meta(self, op: str, ingest: dict[str, Any]) -> dict[str, Any]:
        return {"op": op, "model": self.model, "harness": self.h.name, "rung": self.rung,
                "tool_mode": self.mode, "ingest": ingest,
                "probes_sha": self.probes_sha}  # fmt: skip

    def read(self, tasks: list[dict], op: str = "read", arm: str = "base") -> None:
        todo = [t for t in tasks if not self.path(op, t["id"]).exists()]
        print(f"== {self.h.name} {rung_name(self.rung)} {op}: {len(todo)}/{len(tasks)} to do",
              flush=True)  # fmt: skip
        if not todo:
            return
        kb, root, ingest = self.h.template(self.rung, arm)
        h, tools = self.h.open(kb, root, write=False)
        try:
            for t in todo:
                t0 = time.monotonic()
                rec = {**self.meta(op, ingest), **ask(self.model, self.chat, h, tools, t)}
                rec["wall_s"] = round(time.monotonic() - t0, 1)
                save(self.path(op, t["id"]), rec)
                a = rec["agent"]
                hits = rec["quick"]["hits"]
                print(f"{op} {t['id']} {a['status']} steps={a.get('steps')} "
                      f"tok={a.get('total_tokens')} hits={hits}", flush=True)  # fmt: skip
        finally:
            h.teardown()

    def write_like(self, t: dict[str, Any]) -> None:
        op = t["family"]
        path = self.path(op, t["id"])
        if path.exists():
            return
        kb, root, ingest = self.h.template(self.rung, "base")
        work = WORK / self.h.name / rung_name(self.rung) / t["id"]
        if work.exists():  # an unfinished earlier attempt: archive, never reuse
            TRASH.mkdir(parents=True, exist_ok=True)
            work.rename(
                TRASH / f"{self.h.name}-{rung_name(self.rung)}-{t['id']}-{int(time.time())}"
            )
        work.parent.mkdir(parents=True, exist_ok=True)
        t0 = time.monotonic()
        shutil.copytree(root / "store", work / "store")
        rec: dict[str, Any] = {**self.meta(op, ingest), "task": t,
                               "copy_s": round(time.monotonic() - t0, 2)}  # fmt: skip
        h, tools = self.h.open(kb, work, write=True)
        try:
            user = {
                "create": lambda: CREATE.format(memo=t["memo"]),
                "update": lambda: UPDATE.format(instruction=t["instruction"]),
                "delete": lambda: DELETE.format(instruction=t["instruction"]),
            }[op]()
            try:
                out = run_agent(self.model, SYSTEM, user, h, tools, STEPS[op], chat=self.chat,
                                warn_left=4)  # fmt: skip
            except Exception as e:
                out = {"status": "FAILED", "final": "", "error": f"{type(e).__name__}: {e}",
                       "traceback": traceback.format_exc()[-2000:]}  # fmt: skip
            rec["writer"] = out
            if self.h.export_cmd or self.h.export_vault:
                self.h.export(kb, work, work / "export.jsonl")
                index = json.loads((root / "export-index.json").read_text())
                rec["diff"] = export_diff(index, work / "export.jsonl")
                (work / "export.jsonl").unlink()
            g, rtools = read_tools(h, tools, self.h.m)
            fus = [q for q in (t.get("followups") or [t["followup"]]) if q["id"] not in self.drop]
            rec["followups"] = [ask(self.model, self.chat, g, rtools, q) for q in fus]
        finally:
            h.teardown()
        rec["wall_s"] = round(time.monotonic() - t0, 1)
        save(path, rec)
        if self.prune:  # large rungs: drop the per-task store (its export diff is in the record)
            for p in sorted((work / "store").rglob("*"), reverse=True):
                p.unlink() if p.is_file() else p.rmdir()
            (work / "store").rmdir()
        else:
            TRASH.mkdir(parents=True, exist_ok=True)
            work.rename(
                TRASH / f"{self.h.name}-{rung_name(self.rung)}-{t['id']}-{int(time.time())}"
            )
        fu = [f["quick"]["hits"] for f in rec["followups"]]
        print(f"{op} {t['id']} writer={rec['writer']['status']} "
              f"calls={rec['writer'].get('tool_calls')} diff={diff_counts(rec.get('diff'))} "
              f"followups={fu}", flush=True)  # fmt: skip


def diff_counts(d: dict[str, Any] | None) -> str:
    return f"{len(d['changed'])}/{len(d['added'])}/{len(d['removed'])}" if d else "-"


def select(tasks: list[dict], op: str, limit: int, drop: set[str]) -> list[dict]:
    fam = "create" if op == "control" else op
    sel = [t for t in tasks if t["family"] == fam]
    if op == "control":
        sel = [q for t in sel for q in t["followups"]]
    sel = [t for t in sel if t["id"] not in drop
           and (t.get("followup") or {}).get("id") not in drop]  # fmt: skip
    if limit:
        if fam == "read":  # stratified: round-robin over kinds, ids in order
            by: dict[str, list[dict]] = {}
            for t in sorted(sel, key=lambda t: t["id"]):
                by.setdefault(t.get("subkind") or t["kind"], []).append(t)
            out: list[dict] = []
            while len(out) < min(limit, len(sel)):
                for k in sorted(by):
                    if by[k] and len(out) < limit:
                        out.append(by[k].pop(0))
            return out
        return sel[:limit]
    return sel


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="kbio run2")
    ap.add_argument("--model", default="gpt-oss:120b")
    ap.add_argument("--harness", default="crud-kb")
    ap.add_argument("--rung", type=int, required=True)
    ap.add_argument("--ops", default="read,create,update,delete,control")
    ap.add_argument("--limit", type=int, default=0, help="tasks per op (READ: stratified)")
    ap.add_argument("--prune", action="store_true", help="delete per-task stores after scoring")
    a = ap.parse_args(argv)
    data = json.loads(probes.PROBES.read_text())
    drop = probes.dropped(data)
    r = Runner(a.model, a.harness, a.rung, a.prune, drop, probes.probes_sha(data))
    t_start = time.time()
    for op in a.ops.split(","):
        assert op in OPS, op
        sel = select(data["tasks"], op, a.limit, drop)
        if op in ("read", "control"):
            r.read(sel, op, "base" if op == "read" else "control")
        else:
            todo = [t for t in sel if not r.path(op, t["id"]).exists()]
            print(f"== {a.harness} {rung_name(a.rung)} {op}: {len(todo)}/{len(sel)} to do",
                  flush=True)  # fmt: skip
            for t in todo:
                r.write_like(t)
    print(f"done in {round(time.time() - t_start)} s; llm {llm.STATS}", flush=True)


if __name__ == "__main__":
    main()
