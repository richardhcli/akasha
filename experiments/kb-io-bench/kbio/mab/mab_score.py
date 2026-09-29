"""Score M13 records with MemoryAgentBench's OWN metric code (`default_post_process`, which is what
Conflict_Resolution uses: substring_exact_match on the raw and the parsed output, maximum taken).

Run inside the submodule (see mab_export.py for the environment):

    cd third_party/MemoryAgentBench && PYTHONPATH=. uv run --no-project --python 3.10 ... \
        python ../../kbio/mab/mab_score.py RESULTS_DIR OUT.jsonl

Writes one line per record: cond, sub, qi, status, substring_exact_match, exact_match, f1,
parsed_output. A FAILED record scores 0 (the benchmark would skip it; we count it)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from utils.eval_other_utils import default_post_process


def main() -> None:
    root, out = Path(sys.argv[1]), Path(sys.argv[2])
    with out.open("w") as f:
        for p in sorted(root.glob("*/*/*.json")):
            r = json.loads(p.read_text())
            row = {k: r[k] for k in ("cond", "sub", "qi", "status")}
            if r["status"] == "ok":
                m, extra = default_post_process({"output": r["output"] or ""}, r["answer"])
                row.update({k: float(m[k]) for k in ("substring_exact_match", "exact_match", "f1")})
                row["parsed_output"] = extra.get("parsed_output")
            else:
                row.update(substring_exact_match=0.0, exact_match=0.0, f1=0.0, parsed_output=None)
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
