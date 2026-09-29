"""Corpus source plugin interface: anything that yields Documents can feed a tier."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class Document:
    id: str
    title: str
    text: str
    meta: dict = field(default_factory=dict)


class Source(Protocol):
    name: str

    def iter_documents(self) -> Iterator[Document]: ...
