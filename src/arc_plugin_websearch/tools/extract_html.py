"""extract_html — CSS-selector extraction from a URL or HTML string."""
from __future__ import annotations

from typing import Any, ClassVar

import httpx
from bs4 import BeautifulSoup

from arc.plugin_api import ToolError, ToolInputSchema

from arc_plugin_websearch import http


class ExtractHTMLTool:
    name: ClassVar[str] = "extract_html"
    description: ClassVar[str] = (
        "Extract elements from a URL or HTML string using a CSS selector. "
        "Returns one match per line. Use this for structured scraping when "
        "read_url's plain-text output isn't precise enough."
    )

    def __init__(
        self,
        *,
        timeout_seconds: int = 30,
        max_results: int = 200,
        user_agent: str = http.DEFAULT_USER_AGENT,
    ) -> None:
        self._timeout = timeout_seconds
        self._max_results = max_results
        self._user_agent = user_agent

    @property
    def input_schema(self) -> ToolInputSchema:
        return ToolInputSchema(
            properties={
                "selector": {
                    "type": "string",
                    "description": "CSS selector (e.g. 'h2.title', 'a[href]').",
                },
                "url": {
                    "type": "string",
                    "description": "URL to fetch. Mutually exclusive with `html`.",
                },
                "html": {
                    "type": "string",
                    "description": "HTML string to parse. Mutually exclusive with `url`.",
                },
                "attribute": {
                    "type": "string",
                    "description": "Return this attribute's value instead of element text.",
                },
                "limit": {
                    "type": "integer",
                    "description": f"Max matches to return (default {self._max_results}).",
                    "minimum": 1,
                },
            },
            required=["selector"],
        )

    def execute(self, input: dict[str, Any]) -> str:
        selector = str(input.get("selector", "")).strip()
        if not selector:
            raise ToolError("`selector` is required")

        url = input.get("url")
        html = input.get("html")
        if (url and html) or (not url and not html):
            raise ToolError("provide exactly one of `url` or `html`")

        if url:
            try:
                with http.client(timeout_seconds=self._timeout, user_agent=self._user_agent) as c:
                    resp = c.get(str(url))
            except httpx.TimeoutException:
                raise ToolError(f"network timeout after {self._timeout}s") from None
            except httpx.HTTPError as exc:
                raise ToolError(f"network error: {exc}") from None
            if resp.status_code >= 400:
                raise ToolError(f"HTTP {resp.status_code} fetching {url}")
            html_text = resp.text
        else:
            html_text = str(html)

        attribute = input.get("attribute") or None
        limit = int(input.get("limit", self._max_results))

        soup = BeautifulSoup(html_text, "html.parser")
        try:
            matches = soup.select(selector)
        except Exception as exc:  # bs4's CSS-selector lib raises on bad syntax
            raise ToolError(f"invalid CSS selector: {exc}") from None

        total = len(matches)
        kept = matches[:limit]
        out_lines: list[str] = []
        for m in kept:
            if attribute:
                val = m.get(attribute)
                if val is None:
                    continue
                # bs4 returns list for multi-value attrs (e.g. class)
                out_lines.append(" ".join(val) if isinstance(val, list) else str(val))
            else:
                out_lines.append(m.get_text(" ", strip=True))

        if total > limit:
            out_lines.append(f"({total - limit} more matches truncated)")
        if not out_lines:
            return f"No matches for selector: {selector}"
        return "\n".join(out_lines)
