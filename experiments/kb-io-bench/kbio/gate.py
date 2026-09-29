"""M2 gate for an akasha tier, through the real CLI on the scratch daemon (HOME=scratch, :7534):
`akasha setup` on a fresh copy gives 0 conflicts / 0 reviews, `diff -r` against the built vault
is clean (no id rewritten or repaired), and an edit to one transcluded copy reaches all copies."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from pathlib import Path

from kbio import akasha_ctl as ak
from kbio.corpus import CORPORA
from kbio.paths import DATA


def fresh_copy(src: Path, label: str) -> Path:
    dst = DATA / "snapshots" / f"{label}-{time.strftime('%Y%m%d-%H%M%S')}" / src.name
    shutil.copytree(src, dst)
    return dst


def wait_for(pred, timeout: float = 30.0) -> bool:  # noqa: ANN001
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.25)
    return pred()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tier")
    args = ap.parse_args()
    src = CORPORA / args.tier / "akasha"
    manifest = json.loads((CORPORA / args.tier / "manifest.json").read_text())
    vault = fresh_copy(src, f"gate-{args.tier}")
    t0 = time.monotonic()
    summary = ak.setup(vault, name=f"gate-{args.tier}-{vault.parent.name}")
    setup_s = time.monotonic() - t0
    counts = ak.status_counts(root_path=vault)
    diff = subprocess.run(["diff", "-rq", str(src), str(vault)], capture_output=True, text=True)

    # edit one transcluded copy (a concept note's pasted lead) and check every copy follows
    pid, files = next((p, f) for p, f in manifest["copies"].items() if len(f) >= 3)
    lead_id = manifest["lead_ids"][pid]
    anchor = f" ^tm-{lead_id}"
    target = vault / files[1]
    text = target.read_text()
    line = next(ln for ln in text.split("\n") if ln.endswith(anchor))
    edited = line[: -len(anchor)] + " [edited by the kb-io-bench gate]" + anchor
    target.write_text(text.replace(line, edited))

    def propagated() -> bool:
        return all(edited in (vault / f).read_text() for f in files)

    live = wait_for(propagated, 20)
    if not live:  # the watcher may be slow on a fresh root; a manual reconcile must converge
        ak.rescan()
        wait_for(propagated, 10)
    reached = sum(edited in (vault / f).read_text() for f in files)
    after = ak.status_counts(root_path=vault)
    report = {
        "tier": args.tier,
        "vault": str(vault),
        "setup_seconds": round(setup_s, 2),
        "setup": summary.get("rescan"),
        "status": counts,
        "diff_r_changed_files": len([ln for ln in diff.stdout.splitlines() if ln.strip()]),
        "edit": {
            "page": pid,
            "copies": len(files),
            "reached": reached,
            "via": "watcher" if live else "rescan",
        },
        "status_after_edit": after,
    }
    out = DATA / "logs" / f"gate-{args.tier}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps(report, indent=1))
    ok = (
        counts["conflicts"] == 0
        and counts["reviews_open"] == 0
        and counts["violations"] == 0
        and report["diff_r_changed_files"] == 0
        and reached == len(files)
    )
    print("GATE", "PASS" if ok else "FAIL")


if __name__ == "__main__":
    main()
