"""Tracked vs local paths. EXP is tracked in git (code, config, aggregate results with no note
text). DATA sits under the git-ignored data/ because it quotes the personal vault."""

from __future__ import annotations

from pathlib import Path

EXP = Path(__file__).resolve().parents[1]
REPO = EXP.parents[1]
DATA = REPO / "data" / "experiments" / "kb-io-bench"
SOURCE_VAULT = REPO / "data" / "(10) Concepts"
CONCEPTS_RETRIEVAL = REPO / "data" / "experiments" / "concepts-retrieval"
SCRATCH_HOME = DATA / "scratch-home"
TRASH = DATA / "trash"
PORT = 7534
BASE_URL = f"http://127.0.0.1:{PORT}"
CONFIG = EXP / "config"
RESULTS = EXP / "results"
PURDUE_ENV = REPO.parent / "akasha-wikipedia-codegraph" / ".env"
