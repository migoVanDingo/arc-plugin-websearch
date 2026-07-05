"""SearXNG backend — self-hosted meta-search.

Endpoint: <your-instance>/search?format=json
Auth:     None by default (private instance may add a token via headers).

Set base_url to your instance, e.g. http://localhost:8888.
"""
from __future__ import annotations

import os
from typing import Any, ClassVar

import httpx

from arc.plugin_api import ToolError
from arc_plugin_websearch import http
from arc_plugin_websearch.backends.base import SearchQuery, SearchResult


class SearXNGBackend:
    name: ClassVar[str] = "searxng"

    def __init__(
        self,
        *,
        base_url: str | None,
        api_key_env: str | None = None,    # optional bearer token
        timeout_seconds: int = 15,
        user_agent: str = http.DEFAULT_USER_AGENT,
    ) -> None:
        if not base_url:
            raise ValueError(
                "SearXNG backend requires `base_url` (your instance URL, e.g. "
                "http://localhost:8888) — set it under "
                "tools.config.web_search.base_url"
            )
        self._base_url = base_url.rstrip("/")
        self._api_key_env = api_key_env
        self._timeout = timeout_seconds
        self._user_agent = user_agent

    def search(self, query: SearchQuery) -> list[SearchResult]:
        params: dict[str, Any] = {
            "q": query.query,
            "format": "json",
        }
        if query.safe_search == "off":
            params["safesearch"] = 0
        elif query.safe_search == "strict":
            params["safesearch"] = 2
        else:
            params["safesearch"] = 1
        if query.freshness:
            params["time_range"] = _freshness_to_time_range(query.freshness)
        # Backend-specific extras pass through (e.g. engines=duckduckgo,brave)
        for k in ("engines", "categories"):
            if k in query.extras:
                params[k] = query.extras[k]

        headers: dict[str, str] = {"Accept": "application/json"}
        if self._api_key_env:
            token = os.environ.get(self._api_key_env)
            if token:
                headers["Authorization"] = f"Bearer {token}"

        try:
            with http.client(timeout_seconds=self._timeout, user_agent=self._user_agent) as c:
                resp = c.get(f"{self._base_url}/search", params=params, headers=headers)
        except httpx.TimeoutException:
            raise ToolError(f"network timeout after {self._timeout}s") from None
        except httpx.HTTPError as exc:
            raise ToolError(f"network error: {exc}") from None

        if resp.status_code != 200:
            raise ToolError(f"SearXNG: HTTP {resp.status_code}: {resp.text[:200]}")

        return _parse_searxng(resp.json(), max_count=query.count)


def _freshness_to_time_range(freshness: str) -> str:
    return {"pd": "day", "pw": "week", "pm": "month", "py": "year"}.get(freshness, "")


def _parse_searxng(body: dict[str, Any], *, max_count: int) -> list[SearchResult]:
    """SearXNG JSON shape: { "results": [ { title, url, content, ... } ] }"""
    raw_results = (body or {}).get("results") or []
    out: list[SearchResult] = []
    for r in raw_results[:max_count]:
        if not isinstance(r, dict):
            continue
        out.append(SearchResult(
            title=str(r.get("title", "")),
            url=str(r.get("url", "")),
            description=str(r.get("content", "")),
            age=r.get("publishedDate"),
        ))
    return out
