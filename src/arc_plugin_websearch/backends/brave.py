"""Brave Search API backend.

Docs: https://api.search.brave.com/app/documentation/web-search
Endpoint: https://api.search.brave.com/res/v1/web/search
Auth:     X-Subscription-Token: <API_KEY>
"""
from __future__ import annotations

import os
from typing import Any, ClassVar

import httpx

from arc.plugin_api import ToolError
from arc_plugin_websearch import http
from arc_plugin_websearch.backends.base import SearchQuery, SearchResult


_DEFAULT_BASE_URL = "https://api.search.brave.com/res/v1/web/search"


class BraveBackend:
    name: ClassVar[str] = "brave"

    def __init__(
        self,
        *,
        api_key_env: str = "BRAVE_API_KEY",
        base_url: str | None = None,
        timeout_seconds: int = 15,
        user_agent: str = http.DEFAULT_USER_AGENT,
    ) -> None:
        self._api_key_env = api_key_env
        self._base_url = base_url or _DEFAULT_BASE_URL
        self._timeout = timeout_seconds
        self._user_agent = user_agent

    def search(self, query: SearchQuery) -> list[SearchResult]:
        api_key = os.environ.get(self._api_key_env)
        if not api_key:
            raise ToolError(
                f"{self._api_key_env} not set — export it before using web_search "
                f"with the Brave backend."
            )

        params: dict[str, Any] = {
            "q": query.query,
            "count": min(max(query.count, 1), 20),
            "safesearch": query.safe_search,
        }
        if query.country:
            params["country"] = query.country
        if query.freshness:
            params["freshness"] = query.freshness
        # Backend-specific extras
        goggles_id = query.extras.get("goggles_id")
        if goggles_id:
            params["goggles_id"] = goggles_id

        headers = {
            "X-Subscription-Token": api_key,
            "Accept": "application/json",
        }

        try:
            with http.client(timeout_seconds=self._timeout, user_agent=self._user_agent) as c:
                resp = c.get(self._base_url, params=params, headers=headers)
        except httpx.TimeoutException:
            raise ToolError(f"network timeout after {self._timeout}s") from None
        except httpx.HTTPError as exc:
            raise ToolError(f"network error: {exc}") from None

        if resp.status_code == 401 or resp.status_code == 403:
            raise ToolError(
                f"Brave search: {resp.status_code} unauthorized — check {self._api_key_env}"
            )
        if resp.status_code == 429:
            raise ToolError("Brave search: rate-limited; wait and retry")
        if resp.status_code >= 500:
            raise ToolError(f"Brave search: server error {resp.status_code}")
        if resp.status_code != 200:
            raise ToolError(f"Brave search: HTTP {resp.status_code}: {resp.text[:200]}")

        return _parse_brave(resp.json())


def _parse_brave(body: dict[str, Any]) -> list[SearchResult]:
    """Pull the web results out of Brave's response envelope.

    Shape: { "web": { "results": [ { title, url, description, age }, ... ] } }
    Missing keys default to safe empty values rather than raising — APIs
    occasionally return partial data and we'd rather hand the model what we
    got than nothing.
    """
    web = (body or {}).get("web") or {}
    raw_results = web.get("results") or []
    out: list[SearchResult] = []
    for r in raw_results:
        if not isinstance(r, dict):
            continue
        out.append(SearchResult(
            title=str(r.get("title", "")),
            url=str(r.get("url", "")),
            description=str(r.get("description", "")),
            age=r.get("age"),
        ))
    return out
