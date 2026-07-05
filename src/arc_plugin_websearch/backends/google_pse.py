"""Google Programmable Search Engine backend.

Docs: https://developers.google.com/custom-search/v1/overview
Endpoint: https://customsearch.googleapis.com/customsearch/v1
Auth:     key=<API_KEY>  + cx=<SEARCH_ENGINE_ID>

The `cx` (search engine ID) is mandatory and lives in
`tools.config.web_search.backend_params.cx`.
"""
from __future__ import annotations

import os
from typing import Any, ClassVar

import httpx

from arc.plugin_api import ToolError
from arc_plugin_websearch import http
from arc_plugin_websearch.backends.base import SearchQuery, SearchResult


_DEFAULT_BASE_URL = "https://customsearch.googleapis.com/customsearch/v1"


class GooglePSEBackend:
    name: ClassVar[str] = "google-pse"

    def __init__(
        self,
        *,
        api_key_env: str = "GOOGLE_CSE_API_KEY",
        cx: str | None = None,
        base_url: str | None = None,
        timeout_seconds: int = 15,
        user_agent: str = http.DEFAULT_USER_AGENT,
    ) -> None:
        if not cx:
            raise ValueError(
                "Google PSE backend requires `cx` (search-engine ID) in "
                "tools.config.web_search.backend_params.cx"
            )
        self._api_key_env = api_key_env
        self._cx = cx
        self._base_url = base_url or _DEFAULT_BASE_URL
        self._timeout = timeout_seconds
        self._user_agent = user_agent

    def search(self, query: SearchQuery) -> list[SearchResult]:
        api_key = os.environ.get(self._api_key_env)
        if not api_key:
            raise ToolError(
                f"{self._api_key_env} not set — export it before using web_search "
                f"with the Google PSE backend."
            )

        # Google CSE caps per-request `num` at 10. Larger counts must paginate;
        # we keep this simple — clamp to 10 and let the user re-call if needed.
        params: dict[str, Any] = {
            "key": api_key,
            "cx": self._cx,
            "q": query.query,
            "num": min(max(query.count, 1), 10),
        }
        if query.country:
            params["cr"] = f"country{query.country.upper()}"
        if query.safe_search in ("strict", "moderate"):
            params["safe"] = "active"
        # Freshness via dateRestrict: pd=d1, pw=w1, pm=m1, py=y1
        if query.freshness:
            params["dateRestrict"] = {
                "pd": "d1", "pw": "w1", "pm": "m1", "py": "y1",
            }.get(query.freshness, "")

        try:
            with http.client(timeout_seconds=self._timeout, user_agent=self._user_agent) as c:
                resp = c.get(self._base_url, params=params)
        except httpx.TimeoutException:
            raise ToolError(f"network timeout after {self._timeout}s") from None
        except httpx.HTTPError as exc:
            raise ToolError(f"network error: {exc}") from None

        if resp.status_code == 403:
            raise ToolError(
                f"Google PSE: 403 — check {self._api_key_env} and CSE quota"
            )
        if resp.status_code == 429:
            raise ToolError("Google PSE: rate-limited; wait and retry")
        if resp.status_code != 200:
            raise ToolError(f"Google PSE: HTTP {resp.status_code}: {resp.text[:200]}")

        return _parse_google_pse(resp.json())


def _parse_google_pse(body: dict[str, Any]) -> list[SearchResult]:
    """Google CSE shape: { "items": [ { title, link, snippet, ... } ] }"""
    raw_results = (body or {}).get("items") or []
    out: list[SearchResult] = []
    for r in raw_results:
        if not isinstance(r, dict):
            continue
        out.append(SearchResult(
            title=str(r.get("title", "")),
            url=str(r.get("link", "")),
            description=str(r.get("snippet", "")),
            age=None,
        ))
    return out
