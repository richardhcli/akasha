"""`kbio run`: run tasks for a tier x conditions x model; resumable at task granularity.

Conditions (PLAN.md): A = akasha harness on the akasha vault + daemon; B = basic-memory on the
akasha vault + daemon; C = basic-memory on the regular vault; Cp (C′) = plain file tools on the
regular vault; CB = closed book. READ tasks share one snapshot per (tier, condition) and get
read-only tools. Every WRITE/UPDATE task gets a fresh snapshot (fresh akasha store, fresh
basic-memory index) at a fixed per-condition working path, which is archived (mv) afterwards.
With --bm-template (tiers M/L), B/C WRITE/UPDATE snapshots restore a basic-memory index built once
per (tier, condition) instead of re-indexing (and re-embedding) the whole tier for every task.
Each finished task is written atomically to data/.../results/<model>/<tier>/<cond>/<task>.json
with all the evidence `kbio analyze` needs; an unfinished task restarts from a fresh snapshot."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import shutil
import time
import traceback
from pathlib import Path
from typing import Any

from kbio import akasha_ctl as ak
from kbio import llm, prompts, score
from kbio.agent import STEPS, ToolSpec, run_agent
from kbio.corpus import CORPORA
from kbio.paths import DATA

RESULTS = DATA / "results"
WORK = DATA / "work"
CONDITIONS: dict[str, dict[str, Any]] = {
    "A": {"fmt": "akasha", "daemon": True, "harness": "akasha"},
    "B": {"fmt": "akasha", "daemon": True, "harness": "basic-memory"},
    "C": {"fmt": "regular", "daemon": False, "harness": "basic-memory"},
    "Cp": {"fmt": "regular", "daemon": False, "harness": "files"},
    # M12: the same neutral file tools on the akasha vault + daemon (KB effect with Cp)
    "Ap": {"fmt": "akasha", "daemon": True, "harness": "files"},
    "CB": {"fmt": None, "daemon": False, "harness": "closedbook"},
}
READ_TOOLS = {
    "akasha": {"search", "get_node", "neighborhood", "read_note"},
    "basic-memory": {"search_notes", "read_note", "build_context"},
    "files": {"list_dir", "grep", "read_file"},
    "closedbook": set(),
}
PILOT_READ = {"perturbed": 3, "perturbed-multihop": 2, "unperturbed": 1, "personal": 2,
              "aggregate": 1, "unanswerable": 1}  # fmt: skip
PILOT_OTHER = 3


def pilot_subset(tasks: list[dict], fam: str) -> list[dict]:
    """The M7 pilot: 10 READ (stratified by kind, first by id) + 3 WRITE + 3 UPDATE."""
    sel = sorted((t for t in tasks if t["family"] == fam), key=lambda t: t["id"])
    if fam != "read":
        return sel[:PILOT_OTHER]
    out = []
    for kind, n in PILOT_READ.items():
        out += [t for t in sel if t["kind"] == kind][:n]
    return out


def model_dir(model: str) -> str:
    return model.replace(":", "_").replace("/", "_")


TASKSET = ""  # M12: `--taskset kb` reads tasks/S-kb.json and writes results under S-kb/


def result_path(model: str, tier: str, cond: str, task_id: str) -> Path:
    label = f"{tier}-{TASKSET}" if TASKSET else tier
    return RESULTS / model_dir(model) / label / cond / f"{task_id}.json"


def save(path: Path, rec: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, indent=1, ensure_ascii=False) + "\n")
    tmp.rename(path)


def read_texts(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): p.read_text(errors="replace")
        for p in sorted(root.rglob("*.md"))
        if not any(part.startswith(".") for part in p.relative_to(root).parts)
    }


def hashes(root: Path) -> dict[str, str]:
    return {f: hashlib.sha256(t.encode()).hexdigest() for f, t in read_texts(root).items()}


def vault_diff(before: dict[str, str], after: dict[str, str]) -> dict:
    """Changed files as unified diffs, added files in full, removed file names."""
    changed = {}
    for f in sorted(set(before) & set(after)):
        if before[f] != after[f]:
            changed[f] = "".join(
                difflib.unified_diff(before[f].splitlines(True), after[f].splitlines(True), n=1)
            )
    return {
        "changed": changed,
        "added": {f: after[f] for f in sorted(set(after) - set(before))},
        "removed": sorted(set(before) - set(after)),
    }


class Guard:
    """Restricts a harness to a tool subset (READ tasks get read-only tools)."""

    def __init__(self, h: Any, allowed: set[str]) -> None:
        self.h, self.allowed, self.name = h, allowed, h.name

    def call(self, name: str, args: dict[str, Any]) -> str:
        if name not in self.allowed:
            raise ValueError(f"unknown tool {name}")
        return self.h.call(name, args)


class Env:
    """One condition's live knowledge base: vault copy + akasha store + harness process."""

    def __init__(self, tier: str, cond: str, label: str, template: Path | None = None) -> None:
        self.tier, self.cond, self.label = tier, cond, label
        self.template = template
        self.spec = CONDITIONS[cond]
        self.vault: Path | None = None
        self.h: Any = None
        self.tools: list[ToolSpec] = []
        self.setup_info: dict[str, Any] = {}

    def __enter__(self) -> Env:
        t0 = time.monotonic()
        fmt = self.spec["fmt"]
        if fmt is not None:
            live = WORK / self.tier / self.cond / "live"
            if live.exists():
                arch = WORK / self.tier / self.cond / "archive"
                arch.mkdir(parents=True, exist_ok=True)
                prev = (
                    json.loads((live / "label.json").read_text())["label"]
                    if (live / "label.json").exists()
                    else "unknown"
                )
                live.rename(arch / f"{time.strftime('%Y%m%d-%H%M%S')}-{prev}")
            live.mkdir(parents=True)
            (live / "label.json").write_text(json.dumps({"label": self.label}) + "\n")
            self.vault = live / fmt
            src = self.template / fmt if self.template else CORPORA / self.tier / fmt
            shutil.copytree(src, self.vault)
        if self.spec["daemon"]:
            assert self.vault is not None
            self.setup_info["akasha"] = ak.reset_store(
                self.vault, name=f"{self.tier}-{self.cond}-{self.label}"
            )
        else:
            ak.stop()  # nothing else may watch a regular vault
        name = self.spec["harness"]
        if name == "akasha":
            from kbio.harnesses.akasha import AkashaHarness

            self.h = AkashaHarness(id_seed=f"{self.tier}:{self.cond}:{self.label}")
        elif name == "basic-memory":
            from kbio.harnesses.basic_memory import BasicMemoryHarness

            self.h = BasicMemoryHarness(
                restore_db=self.template / "memory.db" if self.template else None
            )
            self.setup_info["bm_template"] = self.template is not None
        elif name == "files":
            from kbio.harnesses.files import FilesHarness

            self.h = FilesHarness()
        else:
            from kbio.harnesses.closedbook import ClosedBookHarness

            self.h = ClosedBookHarness()
        self.tools = self.h.setup(self.vault or DATA / "tmp", DATA / "tmp")
        if name == "basic-memory":
            self.setup_info["bm_index_seconds"] = self.h.index_seconds
            self.setup_info["bm_indexed"] = self.h.indexed_count()
        self.settle()
        self.setup_info["setup_seconds"] = round(time.monotonic() - t0, 1)
        return self

    def __exit__(self, *exc: object) -> None:
        if self.h is not None:
            self.h.teardown()

    def tools_for(self, family: str) -> tuple[Guard, list[ToolSpec]]:
        allowed = {t.name for t in self.tools}
        if family == "read":
            allowed &= READ_TOOLS[self.spec["harness"]]
        return Guard(self.h, allowed), [t for t in self.tools if t.name in allowed]

    def settle(self) -> dict:
        """Wait until the vault stops changing: akasha rescan + stable hashes (A, B), and
        basic-memory's index matching every file (B, C)."""
        info: dict[str, Any] = {}
        if self.vault is None:
            return info
        t0 = time.monotonic()
        if self.spec["daemon"]:
            ak.rescan()
            prev = hashes(self.vault)
            for _ in range(30):
                time.sleep(1.0)
                cur = hashes(self.vault)
                if cur == prev:
                    break
                prev = cur
        if self.spec["harness"] == "basic-memory":
            info["bm_sync_seconds"] = self.h.wait_synced()
        info["settle_seconds"] = round(time.monotonic() - t0, 1)
        return info

    def status(self) -> dict | None:
        if not self.spec["daemon"] or self.vault is None:
            return None
        ak.rescan()
        return ak.status_counts(root_path=self.vault)

    def ids(self) -> set[str]:
        if self.vault is None:
            return set()
        out: set[str] = set()
        for t in read_texts(self.vault).values():
            out |= set(score.ANCHOR_RE.findall(t))
        return out


def corpus_key(tier: str, fmt: str) -> str:
    snap = json.dumps(sorted(hashes(CORPORA / tier / fmt).items()))
    return hashlib.sha256(snap.encode()).hexdigest()


def bm_template(tier: str, cond: str) -> Path:
    """Index the tier once for a basic-memory condition and keep the post-index files (bm adds
    frontmatter on first index) plus a consistent copy of its SQLite index. The template is built
    at the condition's fixed live path, because bm's project row stores the absolute vault path,
    and snapshots are restored at that same path. Keyed to the corpus hashes; a stale template
    (corpus rebuilt) is archived and rebuilt."""
    from kbio.harnesses.basic_memory import backup_db

    fmt = CONDITIONS[cond]["fmt"]
    tpl = WORK / tier / cond / "bm-template"
    key = corpus_key(tier, fmt)
    marker = tpl / "marker.json"
    if marker.exists() and json.loads(marker.read_text())["corpus"] == key:
        return tpl
    if tpl.exists():
        arch = WORK / tier / cond / "archive"
        arch.mkdir(parents=True, exist_ok=True)
        tpl.rename(arch / f"{time.strftime('%Y%m%d-%H%M%S')}-bm-template")
    with Env(tier, cond, "bm-template") as env:
        info = dict(env.setup_info)
        vault = env.vault
    assert vault is not None
    # the MCP server has exited (teardown); the index still sits in BM_DIR until the next reset
    tpl.mkdir(parents=True)
    backup_db(tpl / "memory.db")
    shutil.copytree(vault, tpl / fmt)
    marker.write_text(json.dumps({"corpus": key, "live": str(vault), "setup": info}) + "\n")
    print(f"bm template {tier}/{cond}: {info}", flush=True)
    return tpl


def answer(env: Env, model: str, question: str) -> dict:
    """A fresh agent answers one question with read-only tools."""
    if env.spec["harness"] == "closedbook":
        out = run_agent(
            model, prompts.SYSTEM_CLOSED_BOOK, prompts.READ_CLOSED_BOOK.format(question=question),
            None, [], STEPS["read"],
        )  # fmt: skip
    else:
        h, tools = env.tools_for("read")
        out = run_agent(
            model, prompts.SYSTEM, prompts.READ.format(question=question), h, tools, STEPS["read"]
        )
    return out


def evaluate(task: dict, out: dict, texts: dict[str, str] | None, ids: set[str]) -> dict:
    sc = score.score_read(task, out["final"], texts)
    cand = score.neutral(sc["answer_text"], ids)
    j = score.judge(task["question"], task["answer"], cand) if out["status"] == "ok" else None
    return {"score": sc, "judge": j, "judge_candidate": cand}


def run_read(env: Env, model: str, tasks: list[dict]) -> None:
    texts = read_texts(env.vault) if env.vault else None
    ids = env.ids()
    for t in tasks:
        path = result_path(model, env.tier, env.cond, t["id"])
        if path.exists():
            continue
        t0 = time.monotonic()
        try:
            out = answer(env, model, t["question"])
        except Exception as e:  # a harness crash is a FAILED task, never a crashed run
            out = {"status": "FAILED", "final": "", "error": f"{type(e).__name__}: {e}",
                   "traceback": traceback.format_exc()[-2000:]}  # fmt: skip
        rec = {
            "task": t,
            "family": "read",
            "tier": env.tier,
            "condition": env.cond,
            "model": model,
            "agent": out,
            "setup": env.setup_info,
            "wall_s": round(time.monotonic() - t0, 1),
        }
        if out["status"] == "ok":
            rec.update(evaluate(t, out, texts, ids))
        save(path, rec)
        print(f"{env.cond} {t['id']} {out['status']} steps={out.get('steps')} "
              f"tok={out.get('total_tokens')}", flush=True)  # fmt: skip


def run_write_like(
    tier: str, cond: str, model: str, task: dict, template: Path | None = None
) -> None:
    path = result_path(model, tier, cond, task["id"])
    if path.exists():
        return
    family = task["family"]
    t0 = time.monotonic()
    rec: dict[str, Any] = {"task": task, "family": family, "tier": tier, "condition": cond,
                           "model": model}  # fmt: skip
    with Env(tier, cond, task["id"], template) as env:
        assert env.vault is not None
        rec["setup"] = env.setup_info
        before = read_texts(env.vault)
        rec["status_before"] = env.status()
        h, tools = env.tools_for(family)
        user = (
            prompts.WRITE.format(memo=task["memo"])
            if family == "write"
            else prompts.UPDATE.format(instruction=task["instruction"])
        )
        try:
            out = run_agent(model, prompts.SYSTEM, user, h, tools, STEPS[family], warn_left=4)
        except Exception as e:
            out = {"status": "FAILED", "final": "", "error": f"{type(e).__name__}: {e}",
                   "traceback": traceback.format_exc()[-2000:]}  # fmt: skip
        rec["writer"] = out
        rec["settle"] = env.settle()
        rec["status_after"] = env.status()
        after = read_texts(env.vault)
        rec["diff"] = vault_diff(before, after)
        rec["hashes_after"] = {f: hashlib.sha256(t.encode()).hexdigest() for f, t in after.items()}
        if family == "write":
            rec["write_score"] = score.score_write(task, before, after)
            followups = task["followups"]
        else:
            man = json.loads((CORPORA / tier / "manifest.json").read_text())
            # M12 single-copy controls carry their own copy list (a planted sentence)
            copies = task.get("copies_scored") or man["copies"][str(task["pageid"])]
            rec["update_score"] = score.score_update(task, copies, after)
            followups = []
            for f in task.get("update_followups") or [task["followup"]]:
                fu = dict(f)
                fu.update({k: task[k] for k in ("old_sentence", "new_sentence", "old_phrase",
                                                "new_phrase")})  # fmt: skip
                followups.append(fu)
        ids = env.ids()
        rec["followups"] = []
        for fu in followups:
            try:
                a = answer(env, model, fu["question"])
            except Exception as e:
                a = {"status": "FAILED", "final": "", "error": f"{type(e).__name__}: {e}"}
            r = {"task": fu, "agent": a}
            if a["status"] == "ok":
                r.update(evaluate(fu, a, after, ids))
            rec["followups"].append(r)
    rec["wall_s"] = round(time.monotonic() - t0, 1)
    save(path, rec)
    print(f"{cond} {task['id']} writer={rec['writer']['status']} "
          f"followups={[f['agent']['status'] for f in rec['followups']]}", flush=True)  # fmt: skip


def load_tasks(tier: str) -> list[dict]:
    # tasks are generated on tier S; every larger tier contains S (nested tiers)
    name = f"S-{TASKSET}.json" if TASKSET else "S.json"
    return json.loads((DATA / "tasks" / name).read_text())["tasks"]


def main() -> None:
    ap = argparse.ArgumentParser(prog="kbio run")
    ap.add_argument("--tier", default="S")
    ap.add_argument("--conditions", default="A,B,C,Cp,CB")
    ap.add_argument("--model", default="gpt-oss:120b")
    ap.add_argument("--tasks", default="read,write,update")
    ap.add_argument("--pilot", action="store_true", help="the M7 pilot subset")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--bm-template", action="store_true",
                    help="B/C WRITE/UPDATE: restore one index per (tier, condition)")  # fmt: skip
    ap.add_argument("--taskset", default="", help="M12: tasks/S-<name>.json, results S-<name>/")
    args = ap.parse_args()
    global TASKSET
    TASKSET = args.taskset
    tasks = load_tasks(args.tier)
    fams = args.tasks.split(",")
    t_start = time.time()
    for cond in args.conditions.split(","):
        for fam in fams:
            sel = [t for t in tasks if t["family"] == fam]
            if args.pilot:
                sel = pilot_subset(tasks, fam)
            if args.limit:
                sel = sel[: args.limit]
            if fam != "read" and cond == "CB":
                continue  # closed book has no knowledge base to write into
            todo = [
                t for t in sel if not result_path(args.model, args.tier, cond, t["id"]).exists()
            ]
            print(f"== {cond} {fam}: {len(todo)}/{len(sel)} to do", flush=True)
            if not todo:
                continue
            if fam == "read":
                with Env(args.tier, cond, "read") as env:
                    run_read(env, args.model, todo)
            else:
                tpl = None
                if args.bm_template and CONDITIONS[cond]["harness"] == "basic-memory":
                    tpl = bm_template(args.tier, cond)
                for t in todo:
                    run_write_like(args.tier, cond, args.model, t, tpl)
    print(f"done in {round(time.time() - t_start)} s; llm {llm.STATS}", flush=True)


if __name__ == "__main__":
    main()
