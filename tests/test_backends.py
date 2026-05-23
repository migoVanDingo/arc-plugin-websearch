"""Backend tests — request shape + response parsing + error mapping.

Each test uses respx to mock httpx so nothing hits the real network.
"""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from arc.plugin_api import ToolError

from arc_plugin_websearch.backends.base import SearchQuery
from arc_plugin_websearch.backends.brave import BraveBackend
from arc_plugin_websearch.backends.ddg_html import DDGHTMLBackend
from arc_plugin_websearch.backends.google_pse import GooglePSEBackend
from arc_plugin_websearch.backends.searxng import SearXNGBackend


# ── Brave ────────────────────────────────────────────────────────────────


def test_brave_parses_response(monkeypatch, brave_response):
    monkeypatch.setenv("BRAVE_API_KEY", "fake-key")
    with respx.mock:
        respx.get("https://api.search.brave.com/res/v1/web/search").mock(
            return_value=httpx.Response(200, json=brave_response)
        )
        backend = BraveBackend(api_key_env="BRAVE_API_KEY")
        results = backend.search(SearchQuery(query="anything"))
    assert len(results) == 2
    assert results[0].title == "Example Result One"
    assert results[0].url == "https://one.example.com"
    assert results[0].age == "2 days ago"


def test_brave_sends_subscription_token(monkeypatch, brave_response):
    monkeypatch.setenv("BRAVE_API_KEY", "secret-token-here")
    with respx.mock:
        route = respx.get("https://api.search.brave.com/res/v1/web/search").mock(
            return_value=httpx.Response(200, json=brave_response)
        )
        BraveBackend(api_key_env="BRAVE_API_KEY").search(SearchQuery(query="x"))
    call = route.calls[0]
    assert call.request.headers["X-Subscription-Token"] == "secret-token-here"


def test_brave_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    with pytest.raises(ToolError, match="BRAVE_API_KEY"):
        BraveBackend(api_key_env="BRAVE_API_KEY").search(SearchQuery(query="x"))


def test_brave_maps_401(monkeypatch):
    monkeypatch.setenv("BRAVE_API_KEY", "bad-key")
    with respx.mock:
        respx.get("https://api.search.brave.com/res/v1/web/search").mock(
            return_value=httpx.Response(401, json={"error": "unauthorized"})
        )
        with pytest.raises(ToolError, match="401"):
            BraveBackend(api_key_env="BRAVE_API_KEY").search(SearchQuery(query="x"))


def test_brave_maps_429(monkeypatch):
    monkeypatch.setenv("BRAVE_API_KEY", "k")
    with respx.mock:
        respx.get("https://api.search.brave.com/res/v1/web/search").mock(
            return_value=httpx.Response(429)
        )
        with pytest.raises(ToolError, match="rate-limited"):
            BraveBackend(api_key_env="BRAVE_API_KEY").search(SearchQuery(query="x"))


def test_brave_passes_through_extras(monkeypatch, brave_response):
    monkeypatch.setenv("BRAVE_API_KEY", "k")
    with respx.mock:
        route = respx.get("https://api.search.brave.com/res/v1/web/search").mock(
            return_value=httpx.Response(200, json=brave_response)
        )
        BraveBackend(api_key_env="BRAVE_API_KEY").search(
            SearchQuery(query="x", extras={"goggles_id": "abc"})
        )
    url = str(route.calls[0].request.url)
    assert "goggles_id=abc" in url


def test_brave_clamps_count(monkeypatch, brave_response):
    monkeypatch.setenv("BRAVE_API_KEY", "k")
    with respx.mock:
        route = respx.get("https://api.search.brave.com/res/v1/web/search").mock(
            return_value=httpx.Response(200, json=brave_response)
        )
        BraveBackend(api_key_env="BRAVE_API_KEY").search(SearchQuery(query="x", count=999))
    url = str(route.calls[0].request.url)
    assert "count=20" in url


# ── DDG-HTML ─────────────────────────────────────────────────────────────


def test_ddg_html_parses_serp(ddg_html_serp):
    with respx.mock:
        respx.get("https://html.duckduckgo.com/html/").mock(
            return_value=httpx.Response(200, text=ddg_html_serp)
        )
        results = DDGHTMLBackend().search(SearchQuery(query="x"))
    assert len(results) == 2
    assert results[0].title == "First result title"
    assert results[0].url == "https://a.example.com"
    assert "First snippet" in results[0].description


def test_ddg_html_safe_search_maps_kp(ddg_html_serp):
    with respx.mock:
        route = respx.get("https://html.duckduckgo.com/html/").mock(
            return_value=httpx.Response(200, text=ddg_html_serp)
        )
        DDGHTMLBackend().search(SearchQuery(query="x", safe_search="strict"))
    url = str(route.calls[0].request.url)
    assert "kp=1" in url


def test_ddg_html_500_raises():
    with respx.mock:
        respx.get("https://html.duckduckgo.com/html/").mock(
            return_value=httpx.Response(500)
        )
        with pytest.raises(ToolError, match="500"):
            DDGHTMLBackend().search(SearchQuery(query="x"))


# ── SearXNG ──────────────────────────────────────────────────────────────


def test_searxng_requires_base_url():
    with pytest.raises(ValueError, match="base_url"):
        SearXNGBackend(base_url=None)


def test_searxng_parses_response(searxng_response):
    with respx.mock:
        respx.get("http://localhost:8888/search").mock(
            return_value=httpx.Response(200, json=searxng_response)
        )
        results = SearXNGBackend(base_url="http://localhost:8888/").search(
            SearchQuery(query="x")
        )
    assert len(results) == 2
    assert results[0].url == "https://x.example.com"


def test_searxng_freshness_maps_time_range(searxng_response):
    with respx.mock:
        route = respx.get("http://localhost:8888/search").mock(
            return_value=httpx.Response(200, json=searxng_response)
        )
        SearXNGBackend(base_url="http://localhost:8888").search(
            SearchQuery(query="x", freshness="pw")
        )
    url = str(route.calls[0].request.url)
    assert "time_range=week" in url


def test_searxng_sends_bearer_when_configured(monkeypatch, searxng_response):
    monkeypatch.setenv("SEARXNG_TOKEN", "tok123")
    with respx.mock:
        route = respx.get("http://localhost:8888/search").mock(
            return_value=httpx.Response(200, json=searxng_response)
        )
        SearXNGBackend(base_url="http://localhost:8888", api_key_env="SEARXNG_TOKEN").search(
            SearchQuery(query="x")
        )
    assert route.calls[0].request.headers["Authorization"] == "Bearer tok123"


# ── Google PSE ───────────────────────────────────────────────────────────


def test_google_pse_requires_cx():
    with pytest.raises(ValueError, match="cx"):
        GooglePSEBackend(cx=None)


def test_google_pse_parses_response(monkeypatch, google_pse_response):
    monkeypatch.setenv("GOOGLE_CSE_API_KEY", "k")
    with respx.mock:
        respx.get("https://customsearch.googleapis.com/customsearch/v1").mock(
            return_value=httpx.Response(200, json=google_pse_response)
        )
        results = GooglePSEBackend(cx="abc123").search(SearchQuery(query="x"))
    assert len(results) == 2
    assert results[0].url == "https://g1.example.com"


def test_google_pse_caps_num_at_10(monkeypatch, google_pse_response):
    monkeypatch.setenv("GOOGLE_CSE_API_KEY", "k")
    with respx.mock:
        route = respx.get("https://customsearch.googleapis.com/customsearch/v1").mock(
            return_value=httpx.Response(200, json=google_pse_response)
        )
        GooglePSEBackend(cx="abc").search(SearchQuery(query="x", count=50))
    url = str(route.calls[0].request.url)
    assert "num=10" in url


def test_google_pse_403_includes_env_var_name(monkeypatch):
    monkeypatch.setenv("GOOGLE_CSE_API_KEY", "k")
    with respx.mock:
        respx.get("https://customsearch.googleapis.com/customsearch/v1").mock(
            return_value=httpx.Response(403)
        )
        with pytest.raises(ToolError, match="GOOGLE_CSE_API_KEY"):
            GooglePSEBackend(cx="abc").search(SearchQuery(query="x"))


# ── Network error mapping (shared across backends) ───────────────────────


def test_brave_maps_timeout(monkeypatch):
    monkeypatch.setenv("BRAVE_API_KEY", "k")
    with respx.mock:
        respx.get("https://api.search.brave.com/res/v1/web/search").mock(
            side_effect=httpx.TimeoutException("slow")
        )
        with pytest.raises(ToolError, match="timeout"):
            BraveBackend(api_key_env="BRAVE_API_KEY", timeout_seconds=5).search(
                SearchQuery(query="x")
            )
