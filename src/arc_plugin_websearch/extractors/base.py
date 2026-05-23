"""Extractor protocol — pluggable HTML → text strategy for read_url."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Protocol


@dataclass(frozen=True)
class ExtractedContent:
    text: str
    title: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    fallback_used: bool = False    # True if an upstream extractor returned empty


class Extractor(Protocol):
    """Every extractor implements this. `extract` MUST be deterministic for
    a given (html, url) pair — replay relies on it."""
    name: ClassVar[str]

    def extract(self, html: str, *, url: str) -> ExtractedContent: ...
