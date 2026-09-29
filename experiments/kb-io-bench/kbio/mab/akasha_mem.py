"""M13 akasha memory (conditions K0/K1/K2), driven only through the scratch daemon's HTTP API.

Ingest, per fact list (4 lists; each sub-dataset pair sh/mh shares one), in the benchmark's memorize
order (M13-PLAN.md §3, rulings §7a):
- each memorize-wrapped chunk -> one `journal` node (the verbatim record, ruling d);
- each numbered fact in it -> one `claim` node ("<serial>. <fact>"), which `cites` its journal;
- K1: for each capture candidate (`contradiction_candidates`, live claims) that the shared
  conflict rule accepts, a `contradicts` edge new -> old, which flags the old claim for review;
- K2: the same pairs are overridden instead: `POST /nodes/{old}/supersede {"by": new}` (the
  confident-agent override, ruling c).
K0 stores claims and journals only.

Writes use the scratch human token ("benchmark mode: all captures pre-approved", ruling b). Each
(condition, fact list) store is built once in a fresh scratch store, then its store files are
copied to data/.../mab/stores/<cond>-<size>/ and restored for querying; a replaced live store is
moved to trash, never deleted.

Retrieval: `GET /v1/search?q=<question>&mode=any&limit=10&type=claim&status=live`. K1 shows each
hit with the newer claims that contradict it: "<fact>\\n[contradicted by newer: <fact>; ...]"."""

from __future__ import annotations

import json
import re
import shutil
import time
from functools import cache
from typing import Any

from kbio import akasha_ctl as ak
from kbio.mab import conflict
from kbio.paths import DATA, SCRATCH_HOME, TRASH

STORES = DATA / "mab" / "stores"
VAULTS = DATA / "mab" / "vaults"
CFG = SCRATCH_HOME / ".config" / "tm-daemon"
LOADED = DATA / "mab" / "loaded-store.json"
FACT_RE = re.compile(r"^(\d+)\. (.*)$", re.M)


def size_of(sub: str) -> str:
    return sub.rsplit("_", 1)[1]


def store_key(cond: str, sub: str) -> str:
    return f"{cond}-{size_of(sub)}"


def _loaded() -> str | None:
    return json.loads(LOADED.read_text())["key"] if LOADED.exists() else None


def _swap_in(key: str) -> None:
    """Stop the daemon, move the live store to trash, copy the saved store in, restart."""
    ak.stop()
    bin_dir = TRASH / "stores" / (time.strftime("%Y%m%d-%H%M%S-") + f"mab-live-{_loaded()}")
    for f in ak.STORE_FILES:
        if (CFG / f).exists():
            bin_dir.mkdir(parents=True, exist_ok=True)
            (CFG / f).rename(bin_dir / f)
    for f in ak.STORE_FILES:
        if (STORES / key / f).exists():
            shutil.copy2(STORES / key / f, CFG / f)
    ak.start()
    LOADED.write_text(json.dumps({"key": key}) + "\n")


def _save(key: str) -> None:
    """Copy the live store (daemon stopped, so the WAL is checkpointed) to STORES/key."""
    ak.stop()
    dest = STORES / key
    dest.mkdir(parents=True, exist_ok=True)
    for f in ak.STORE_FILES:
        if (CFG / f).exists():
            shutil.copy2(CFG / f, dest / f)
    ak.start()


def _chunk_facts(rec: dict[str, Any]) -> list[list[tuple[int, str]]]:
    """The context's numbered facts, grouped by the memorize chunk where each one starts. The
    benchmark's sentence splitter can break a fact across a chunk boundary ("307." ending one
    chunk, "Jesus Christ ..." opening the next), so facts are located in the chunks joined by a
    space, which is how the splitter joins sentences inside a chunk."""
    facts = [(int(s), t) for s, t in FACT_RE.findall(rec["context"])]
    starts, whole = [], ""
    for c in rec["chunks"]:
        starts.append(len(whole) + (1 if whole else 0))
        whole = f"{whole} {c}" if whole else c
    out: list[list[tuple[int, str]]] = [[] for _ in rec["chunks"]]
    pos = 0
    for serial, text in facts:
        at = whole.find(f"{serial}. {text}", pos)
        if at < 0:
            raise ValueError(f"fact {serial} is in no chunk")  # never silently dropped
        ci = max(k for k, st in enumerate(starts) if st <= at)
        out[ci].append((serial, text))
        pos = at
    return out


def build(cond: str, sub: str) -> dict[str, Any]:
    from kbio.mab.run import load

    key = store_key(cond, sub)
    rec = load(sub)
    vault = VAULTS / key
    vault.mkdir(parents=True, exist_ok=True)
    ak.reset_store(vault, name=f"mab-{key}")
    t0 = time.monotonic()
    stats = {"journals": 0, "claims": 0, "conflicts": 0, "flagged": 0, "superseded": 0}
    for chunk_text, facts in zip(rec["memorized"], _chunk_facts(rec), strict=True):
        j = ak.api("POST", "/nodes", json={"node_type": "journal", "body": chunk_text})["id"]
        stats["journals"] += 1
        for serial, text in facts:
            body = f"{serial}. {text}"
            r = ak.api("POST", "/nodes", json={"node_type": "claim", "body": body})
            c = r["id"]
            stats["claims"] += 1
            cites = {"src": c, "dst": j, "edge_type": "cites", "facet_binding": "*"}
            ak.api("POST", "/edges", json={**cites, "provenance": "human"})
            if cond == "K0":
                continue
            for cand in r.get("contradiction_candidates", []):
                if not conflict.conflicts(body, cand["body"]):
                    continue
                stats["conflicts"] += 1
                if cond == "K1":
                    ak.api(
                        "POST",
                        "/edges",
                        json={
                            "src": c,
                            "dst": cand["node_id"],
                            "edge_type": "contradicts",
                            "facet_binding": "*",
                            "provenance": "human",
                        },
                    )
                    stats["flagged"] += 1
                else:
                    ak.api("POST", f"/nodes/{cand['node_id']}/supersede", json={"by": c})
                    stats["superseded"] += 1
    stats["ingest_seconds"] = round(time.monotonic() - t0, 1)
    stats["review_open"] = len(ak.api("GET", "/review", params={"status": "open"})["reviews"])
    _save(key)
    (STORES / key / "ingest.json").write_text(json.dumps(stats, indent=1) + "\n")
    LOADED.write_text(json.dumps({"key": key}) + "\n")
    return stats


def ensure_store(cond: str, sub: str) -> None:
    key = store_key(cond, sub)
    if not (STORES / key / "store.db").exists():
        print(f"ingest {key}: {build(cond, sub)}", flush=True)
    elif _loaded() != key or not ak.healthy():
        _swap_in(key)
    _neighbors.cache_clear()


@cache
def _neighbors(node_id: str) -> tuple[str, ...]:
    """Bodies of live claims with a `contradicts` edge into node_id (newer contradicting facts)."""
    nb = ak.api("GET", f"/nodes/{node_id}/neighborhood", params={"hops": 1})
    srcs = [
        e["src"] for e in nb["edges"] if e["edge_type"] == "contradicts" and e["dst"] == node_id
    ]
    return tuple(ak.api("GET", f"/nodes/{s}")["body"] for s in srcs)


def retrieve(cond: str, sub: str, query: str, k: int) -> list[str]:
    params = {"q": query, "mode": "any", "limit": k, "type": "claim", "status": "live"}
    hits = ak.api("GET", "/search", params=params)["results"]
    # bodies are canonical text (one trailing newline): strip, so the prompt matches R-facts'
    if cond != "K1":
        return [h["body"].strip() for h in hits]
    out = []
    for h in hits:
        newer = [n.strip() for n in _neighbors(h["id"])]
        note = f"\n[contradicted by newer: {'; '.join(newer)}]" if newer else ""
        out.append(h["body"].strip() + note)
    return out
