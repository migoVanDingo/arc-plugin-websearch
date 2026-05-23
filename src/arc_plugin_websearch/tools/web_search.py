"""web_search — search the web through a pluggable backend."""
from __future__ import annotations

import time
from typing import Any, ClassVar

from arc.plugin_api import RuntimeEvent, ToolError, ToolInputSchema

from arc_plugin_websearch.backends.base import SearchBackend, SearchQuery


_VALID_SAFE = ("off", "moderate", "strict")
_VALID_FRESH = ("pd", "pw", "pm", "py")


class WebSearchTool:
    name: ClassVar[str] = "web_search"
    description: ClassVar[str] = (
        "Search the web. Returns ranked results with title, URL, and snippet. "
        "Use read_url to fetch the full text of any result."
    )

    def __init__(
        self,
        *,
        backend: SearchBackend,
        default_count: int = 10,
        max_count: int = 20,
        backend_params: dict[str, Any] | None = None,
    ) -> None:
        self._backend = backend
        self._default_count = default_count
        self._max_count = max_count
        self._backend_params = backend_params or {}
        self._bus: Any = None

    def bind_bus(self, bus: Any) -> None:
        self._bus = bus

    @property
    def input_schema(self) -> ToolInputSchema:
        return ToolInputSchema(
            properties={
                "query": {"type": "string", "description": "Search query."},
                "count": {
                    "type": "integer",
                    "description": f"Number of results (default {self._default_count}, max {self._max_count}).",
                    "minimum": 1, "maximum": self._max_count,
                },
                "country": {
                    "type": "string",
                    "description": "ISO 3166-1 alpha-2 country code (e.g. 'us').",
                },
                "freshness": {
                    "type": "string",
                    "description": "pd=past day, pw=past week, pm=past month, py=past year.",
                    "enum": list(_VALID_FRESH),
                },
                "safe_search": {
                    "type": "string",
                    "description": "off | moderate (default) | strict",
                    "enum": list(_VALID_SAFE),
                },
            },
            required=["query"],
        )

    def execute(self, input: dict[str, Any]) -> str:
        query = str(input.get("query", "")).strip()
        if not query:
            raise ToolError("`query` is required and must be non-empty")

        count = min(int(input.get("count", self._default_count)), self._max_count)
        country = input.get("country") or None
        freshness = input.get("freshness") or None
        if freshness and freshness not in _VALID_FRESH:
            raise ToolError(f"`freshness` must be one of {list(_VALID_FRESH)}")
        safe = input.get("safe_search", "moderate")
        if safe not in _VALID_SAFE:
            safe = "moderate"

        sq = SearchQuery(
            query=query, count=count, country=country,
            freshness=freshness, safe_search=safe,
            extras=dict(self._backend_params),
        )

        t0 = time.perf_counter()
        try:
            results = self._backend.search(sq)
        except ToolError:
            raise
        except Exception as exc:
            # Backends should already map their errors; this catches anything
            # they missed so the model sees something actionable rather than
            # a stack trace.
            raise ToolError(f"web_search backend {self._backend.name!r} raised: {exc!r}") from exc
        took_ms = int((time.perf_counter() - t0) * 1000)

        self._emit(query, sq, results, took_ms)

        if not results:
            return f"No results for: {query}"

        lines = [f"web_search via {self._backend.name}: {query!r}  ({len(results)} results)"]
        lines.append("")
        for i, r in enumerate(results, 1):
            age = f"  ({r.age})" if r.age else ""
            lines.append(f"[{i}] {r.title}{age}")
            lines.append(f"    {r.url}")
            if r.description:
                desc = " ".join(r.description.split())
                if len(desc) > 240:
                    desc = desc[:240].rstrip() + "…"
                lines.append(f"    {desc}")
            lines.append("")
        return "\n".join(lines).rstrip()

    def _emit(self, query: str, sq: SearchQuery, results: list, took_ms: int) -> None:
        if self._bus is None:
            return
        self._bus.emit(RuntimeEvent(
            type="web_search.requested",
            stage="tool",
            payload={
                "backend": self._backend.name,
                "query": query,
                "count": sq.count,
                "country": sq.country,
                "freshness": sq.freshness,
                "safe_search": sq.safe_search,
                "result_count": len(results),
                "took_ms": took_ms,
            },
        ))
