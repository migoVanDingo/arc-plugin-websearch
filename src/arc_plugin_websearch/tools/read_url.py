"""read_url — fetch a web page, extract readable text."""
from __future__ import annotations

import time
from typing import Any, ClassVar
from urllib.parse import urlparse

import httpx

from arc.plugin_api import RuntimeEvent, ToolError, ToolInputSchema

from arc_plugin_websearch import http
from arc_plugin_websearch.extractors.base import Extractor


_BLOCKED_HOSTS = frozenset({
    "localhost", "127.0.0.1", "0.0.0.0", "::1",
})


class ReadURLTool:
    name: ClassVar[str] = "read_url"
    description: ClassVar[str] = (
        "Fetch a web page and return its primary text content. HTML is stripped "
        "to readable prose. Use http_request for JSON/API responses; use "
        "extract_html if you need a specific element by CSS selector."
    )

    def __init__(
        self,
        *,
        extractor: Extractor,
        default_max_chars: int = 50_000,
        timeout_seconds: int = 30,
        user_agent: str = http.DEFAULT_USER_AGENT,
        allow_schemes: list[str] | None = None,
    ) -> None:
        self._extractor = extractor
        self._default_max_chars = default_max_chars
        self._timeout = timeout_seconds
        self._user_agent = user_agent
        self._allow_schemes = frozenset(allow_schemes or ["http", "https"])
        self._bus: Any = None

    def bind_bus(self, bus: Any) -> None:
        self._bus = bus

    @property
    def input_schema(self) -> ToolInputSchema:
        return ToolInputSchema(
            properties={
                "url": {"type": "string", "description": "URL to fetch (http(s) only)."},
                "max_chars": {
                    "type": "integer",
                    "description": f"Truncate extracted text at this many chars (default {self._default_max_chars}).",
                    "minimum": 100,
                },
            },
            required=["url"],
        )

    def execute(self, input: dict[str, Any]) -> str:
        url = str(input.get("url", "")).strip()
        if not url:
            raise ToolError("`url` is required and must be non-empty")
        max_chars = int(input.get("max_chars", self._default_max_chars))

        parsed = urlparse(url)
        if parsed.scheme not in self._allow_schemes:
            raise ToolError(
                f"scheme {parsed.scheme!r} not allowed (allow: {sorted(self._allow_schemes)})"
            )
        if (parsed.hostname or "").lower() in _BLOCKED_HOSTS:
            raise ToolError(f"host {parsed.hostname!r} is blocked")

        t0 = time.perf_counter()
        try:
            with http.client(timeout_seconds=self._timeout, user_agent=self._user_agent) as c:
                resp = c.get(url)
        except httpx.TimeoutException:
            raise ToolError(f"network timeout after {self._timeout}s") from None
        except httpx.HTTPError as exc:
            raise ToolError(f"network error: {exc}") from None

        if resp.status_code >= 400:
            raise ToolError(f"HTTP {resp.status_code} fetching {url}")

        html = resp.text
        content_type = resp.headers.get("content-type", "")

        extracted = self._extractor.extract(html, url=url)
        took_ms = int((time.perf_counter() - t0) * 1000)

        body = extracted.text or ""
        truncated_at = None
        if len(body) > max_chars:
            truncated_at = max_chars
            body = body[:max_chars].rstrip() + f"\n\n… [+{len(extracted.text) - max_chars} chars truncated]"

        self._emit(
            url=url,
            took_ms=took_ms,
            bytes_fetched=len(html.encode("utf-8", errors="ignore")),
            content_type=content_type,
            http_status=resp.status_code,
            extracted_chars=len(extracted.text or ""),
            truncated_at=truncated_at,
            fallback_used=extracted.fallback_used,
        )

        header = extracted.title or url
        return f"{header}\n\n{body}".rstrip()

    def _emit(self, **payload: Any) -> None:
        if self._bus is None:
            return
        self._bus.emit(RuntimeEvent(
            type="web_fetch.requested",
            stage="tool",
            payload={"extractor": self._extractor.name, **payload},
        ))
