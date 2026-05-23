"""DuckDuckGo HTML scraper backend — no API key required.

Endpoint: https://html.duckduckgo.com/html/
Parses the HTML SERP. Fragile by nature — a layout change upstream breaks
this; we use defensive selectors and degrade to "no results" rather than
crash.
"""
from __future__ import annotations

from typing import Any, ClassVar

import httpx
from bs4 import BeautifulSoup

from arc.plugin_api import ToolError
from arc_plugin_websearch import http
from arc_plugin_websearch.backends.base import SearchBackend, SearchQuery, SearchResult


_DEFAULT_BASE_URL = "https://html.duckduckgo.com/html/"


class DDGHTMLBackend:
    name: ClassVar[str] = "ddg-html"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout_seconds: int = 15,
        user_agent: str = http.DEFAULT_USER_AGENT,
    ) -> None:
        self._base_url = base_url or _DEFAULT_BASE_URL
        self._timeout = timeout_seconds
        self._user_agent = user_agent

    def search(self, query: SearchQuery) -> list[SearchResult]:
        params: dict[str, Any] = {"q": query.query}
        # DDG's "kp" parameter: -2=off, -1=moderate, 1=strict
        kp_map = {"off": "-2", "moderate": "-1", "strict": "1"}
        params["kp"] = kp_map.get(query.safe_search, "-1")
        if query.country:
            params["kl"] = query.country  # DDG uses kl=us-en style; we pass through

        try:
            with http.client(timeout_seconds=self._timeout, user_agent=self._user_agent) as c:
                # DDG HTML endpoint accepts POST; GET also works and is cacheable
                resp = c.get(self._base_url, params=params)
        except httpx.TimeoutException:
            raise ToolError(f"network timeout after {self._timeout}s") from None
        except httpx.HTTPError as exc:
            raise ToolError(f"network error: {exc}") from None

        if resp.status_code != 200:
            raise ToolError(f"DDG-HTML: HTTP {resp.status_code}")

        return _parse_ddg_html(resp.text, max_count=query.count)


def _parse_ddg_html(html: str, *, max_count: int) -> list[SearchResult]:
    """Pull (title, url, snippet) triples out of DDG's HTML SERP.

    The selectors target the no-JS endpoint at html.duckduckgo.com:
      div.result          — wrapper for each result
      a.result__a         — title + href
      a.result__snippet   — snippet text
    """
    soup = BeautifulSoup(html, "html.parser")
    out: list[SearchResult] = []
    for div in soup.select("div.result")[: max_count * 2]:  # over-fetch a bit
        a = div.select_one("a.result__a")
        snip = div.select_one("a.result__snippet") or div.select_one(".result__snippet")
        if not a:
            continue
        url = a.get("href") or ""
        title = a.get_text(strip=True)
        description = snip.get_text(" ", strip=True) if snip else ""
        if url and title:
            out.append(SearchResult(title=title, url=url, description=description, age=None))
        if len(out) >= max_count:
            break
    return out
