"""Condition CB: closed book. No knowledge base and no tools; measures what the model knows."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from kbio.agent import ToolSpec


class ClosedBookHarness:
    name = "closedbook"

    def setup(self, kb_dir: Path, scratch: Path) -> list[ToolSpec]:
        return []

    def call(self, name: str, args: dict[str, Any]) -> str:
        raise ValueError("no tools in the closed-book condition")

    def teardown(self) -> None:
        pass
