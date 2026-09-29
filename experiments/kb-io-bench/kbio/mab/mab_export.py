"""Export MemoryAgentBench FactConsolidation inputs with the benchmark's OWN code (M13).

Run inside the submodule with its light dependencies (not kbio's environment):

    cd third_party/MemoryAgentBench && uv run --no-project --with datasets --with nltk \
        --with tiktoken --with rouge-score --with editdistance --with numpy --with pyyaml \
        python ../../kbio/mab/mab_export.py OUT_DIR

For each of the 8 Conflict_Resolution sub-datasets this writes OUT_DIR/<sub_dataset>.json with the
exact chunks (`ConversationCreator`, chunk_size from the benchmark's data config), the chunks
wrapped in the benchmark's `memorize` template, the formatted queries for the long-context and
RAG agent types, the answers, and the system message. The one change is a fixed memorize
timestamp: the benchmark stamps wall-clock time, which would make every request unique and
uncacheable. The submodule is not edited."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml
from conversation_creator import ConversationCreator
from utils.templates import get_template

SIZES = ("6k", "32k", "64k", "262k")
AGENTS = {"lc": "Long_context_agent_gpt-oss", "rag": "Simple_rag_bm25"}
STAMP = "2026-09-28 00:00:00"


def main() -> None:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    for hop in ("sh", "mh"):
        for size in SIZES:
            cfg_path = Path(
                f"configs/data_conf/Conflict_Resolution/Factconsolidation_{hop}_{size}.yaml"
            )
            dcfg = yaml.safe_load(cfg_path.read_text())
            sub = dcfg["sub_dataset"]
            rec: dict = {"sub_dataset": sub, "data_config": dcfg, "queries": {}}
            for kind, name in AGENTS.items():
                cc = ConversationCreator({"agent_name": name}, dcfg)
                assert len(cc.contexts) == 1, sub
                chunks = cc.get_chunks()[0]
                qas = cc.get_query_and_answers()[0]
                rec["queries"][kind] = [q for q, _a, _i in qas]
                rec.setdefault("answers", [a for _q, a, _i in qas])
                rec.setdefault("chunks", chunks)
                mem = get_template(sub, "memorize", name)
                rec.setdefault(
                    "memorized", [mem.format(context=c, time_stamp=STAMP) for c in chunks]
                )
                rec["system"] = get_template(sub, "system", name)
            rec["context"] = cc.contexts[0]
            (out / f"{sub}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1) + "\n")
            print(sub, "chunks", len(rec["chunks"]), "queries", len(rec["answers"]), flush=True)


if __name__ == "__main__":
    main()
