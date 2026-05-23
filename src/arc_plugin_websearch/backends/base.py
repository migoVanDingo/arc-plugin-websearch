"""SearchBackend Protocol + the common dataclasses every backend speaks.

Backends are tiny adapters. Each one takes a `SearchQuery` (LCD across
providers), hits the wire, and returns a `list[SearchResult]`. Errors map
to `ToolError` with a message the model can act on.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Protocol


@dataclass(frozen=True)
class SearchQuery:
    """Lowest common denominator across Brave / DDG / SearXNG / Google PSE.

    Provider-specific knobs (Google's `cx`, Brave's `goggles_id`, etc.) go
    in `extras`. Backends pick out the keys they recognize and ignore the rest.
    """
    query: str
    count: int = 10
    country: str | None = None
    freshness: str | None = None         # 'pd' | 'pw' | 'pm' | 'py'
    safe_search: str = "moderate"        # 'off' | 'moderate' | 'strict'
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    description: str
    age: str | None = None


class SearchBackend(Protocol):
    """Every backend implements this."""
    name: ClassVar[str]

    def search(self, query: SearchQuery) -> list[SearchResult]: ...
