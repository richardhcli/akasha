"""Enforces build-plan rule 0.5: pickle, eval, exec are forbidden everywhere in src/."""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
FORBIDDEN = re.compile(r"\b(import pickle|pickle\.|eval\(|exec\()")


def test_src_contains_no_banned_tokens():
    offenders = []
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if FORBIDDEN.search(line):
                offenders.append(f"{path}:{lineno}: {line.strip()}")
    assert not offenders, "banned tokens (pickle/eval/exec) found:\n" + "\n".join(offenders)
