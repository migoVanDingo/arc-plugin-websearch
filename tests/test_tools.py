"""Tool tests — input validation, output rendering, event emission."""
from __future__ import annotations

import httpx
import pytest
import respx

from arc.plugin_api import ToolError

from arc_plugin_websearch.backends.base import SearchResult
from arc_plugin_websearch.extractors.base import ExtractedContent
from arc_plugin_websearch.extractors.raw_extractor import RawExtractor
from arc_plugin_websearch.tools.extract_html import ExtractHTMLTool
from arc_plugin_websearch.tools.http_request import HTTPRequestTool
from arc_plugin_websearch.tools.read_url import ReadURLTool
from arc_plugin_websearch.tools.web_search import WebSearchTool


# ── Stub backend / extractor ─────────────────────────────────────────────


class _StubBackend:
    name = "stub"
    def __init__(self, results=None, exc=None):
        self._results = results if results is not None else [
            SearchResult(title="T1", url="https://u1.test", description="d1", age="1d"),
            SearchResult(title="T2", url="https://u2.test", description="d2"),
        ]
        self._exc = exc
    def search(self, query):
        if self._exc:
            raise self._exc
        return list(self._results)


# ── web_search ───────────────────────────────────────────────────────────


def test_web_search_renders_results(bus):
    tool = WebSearchTool(backend=_StubBackend())
    tool.bind_bus(bus)
    out = tool.execute({"query": "anything"})
    assert "T1" in out and "T2" in out
    assert "https://u1.test" in out
    assert "(1d)" in out
    assert "via stub" in out


def test_web_search_empty_results_returns_success_string(bus):
    tool = WebSearchTool(backend=_StubBackend(results=[]))
    tool.bind_bus(bus)
    out = tool.execute({"query": "nothing"})
    assert out.startswith("No results")


def test_web_search_empty_query_raises():
    with pytest.raises(ToolError, match="required"):
        WebSearchTool(backend=_StubBackend()).execute({"query": "   "})


def test_web_search_invalid_freshness_raises():
    with pytest.raises(ToolError, match="freshness"):
        WebSearchTool(backend=_StubBackend()).execute(
            {"query": "x", "freshness": "bogus"}
        )


def test_web_search_clamps_to_max_count(bus):
    tool = WebSearchTool(backend=_StubBackend(), max_count=20)
    tool.bind_bus(bus)
    tool.execute({"query": "x", "count": 999})
    payload = bus.payloads("web_search.requested")[0]
    assert payload["count"] == 20


def test_web_search_emits_event(bus):
    tool = WebSearchTool(backend=_StubBackend())
    tool.bind_bus(bus)
    tool.execute({"query": "ghidra"})
    payloads = bus.payloads("web_search.requested")
    assert len(payloads) == 1
    p = payloads[0]
    assert p["backend"] == "stub"
    assert p["query"] == "ghidra"
    assert p["result_count"] == 2
    assert "took_ms" in p


def test_web_search_wraps_backend_exception_as_toolerror():
    tool = WebSearchTool(backend=_StubBackend(exc=RuntimeError("oops")))
    with pytest.raises(ToolError, match="oops"):
        tool.execute({"query": "x"})


def test_web_search_passes_through_existing_toolerror():
    """ToolError from the backend should propagate without double-wrapping."""
    tool = WebSearchTool(backend=_StubBackend(exc=ToolError("BRAVE_API_KEY not set")))
    with pytest.raises(ToolError, match="BRAVE_API_KEY"):
        tool.execute({"query": "x"})


# ── read_url ─────────────────────────────────────────────────────────────


class _StubExtractor:
    name = "stub-extractor"
    def __init__(self, text="Hello world.", title="Stubbed Title", fallback=False):
        self._text = text
        self._title = title
        self._fallback = fallback
    def extract(self, html, *, url):
        return ExtractedContent(text=self._text, title=self._title,
                                fallback_used=self._fallback)


def test_read_url_fetches_and_extracts(bus, example_html):
    tool = ReadURLTool(extractor=_StubExtractor())
    tool.bind_bus(bus)
    with respx.mock:
        respx.get("https://example.com/").mock(
            return_value=httpx.Response(200, text=example_html,
                                         headers={"content-type": "text/html"})
        )
        out = tool.execute({"url": "https://example.com/"})
    assert "Stubbed Title" in out
    assert "Hello world." in out


def test_read_url_rejects_disallowed_scheme():
    tool = ReadURLTool(extractor=_StubExtractor(), allow_schemes=["https"])
    with pytest.raises(ToolError, match="scheme"):
        tool.execute({"url": "http://example.com/"})


def test_read_url_blocks_localhost():
    tool = ReadURLTool(extractor=_StubExtractor())
    with pytest.raises(ToolError, match="blocked"):
        tool.execute({"url": "http://localhost/admin"})
    with pytest.raises(ToolError, match="blocked"):
        tool.execute({"url": "http://127.0.0.1:8080/"})


def test_read_url_truncates_output(bus):
    tool = ReadURLTool(extractor=_StubExtractor(text="x" * 200), default_max_chars=50)
    tool.bind_bus(bus)
    with respx.mock:
        respx.get("https://example.com/").mock(
            return_value=httpx.Response(200, text="<html></html>")
        )
        out = tool.execute({"url": "https://example.com/"})
    assert "truncated" in out
    payload = bus.payloads("web_fetch.requested")[0]
    assert payload["truncated_at"] == 50


def test_read_url_emits_fallback_flag(bus):
    tool = ReadURLTool(extractor=_StubExtractor(fallback=True))
    tool.bind_bus(bus)
    with respx.mock:
        respx.get("https://example.com/").mock(
            return_value=httpx.Response(200, text="<html></html>")
        )
        tool.execute({"url": "https://example.com/"})
    assert bus.payloads("web_fetch.requested")[0]["fallback_used"] is True


def test_read_url_maps_4xx_to_toolerror():
    tool = ReadURLTool(extractor=_StubExtractor())
    with respx.mock:
        respx.get("https://example.com/").mock(return_value=httpx.Response(404))
        with pytest.raises(ToolError, match="404"):
            tool.execute({"url": "https://example.com/"})


# ── http_request ─────────────────────────────────────────────────────────


def test_http_request_get_renders_status_and_body(bus):
    tool = HTTPRequestTool()
    tool.bind_bus(bus)
    with respx.mock:
        respx.get("https://api.example.com/foo").mock(
            return_value=httpx.Response(200, json={"ok": True},
                                         headers={"content-type": "application/json"})
        )
        out = tool.execute({"method": "GET", "url": "https://api.example.com/foo"})
    assert "200" in out
    assert "content-type: application/json" in out
    # JSON gets pretty-printed
    assert '"ok": true' in out


def test_http_request_auto_json_body(bus):
    tool = HTTPRequestTool()
    tool.bind_bus(bus)
    with respx.mock:
        route = respx.post("https://api.example.com/x").mock(
            return_value=httpx.Response(201, text="created")
        )
        tool.execute({
            "method": "POST", "url": "https://api.example.com/x",
            "body": '{"a": 1}',
        })
    body_bytes = route.calls[0].request.content
    assert b'"a": 1' in body_bytes or b'"a":1' in body_bytes
    # Content-Type should be json (httpx sets it for `json=` kwarg)
    assert route.calls[0].request.headers["content-type"] == "application/json"


def test_http_request_text_body_when_not_json():
    tool = HTTPRequestTool()
    with respx.mock:
        route = respx.post("https://x.test/").mock(return_value=httpx.Response(200))
        tool.execute({"method": "POST", "url": "https://x.test/", "body": "plain text"})
    assert route.calls[0].request.content == b"plain text"


def test_http_request_redacts_auth_headers_in_events(bus):
    tool = HTTPRequestTool()
    tool.bind_bus(bus)
    with respx.mock:
        respx.get("https://x.test/").mock(return_value=httpx.Response(200))
        tool.execute({
            "method": "GET", "url": "https://x.test/",
            "headers": {"Authorization": "Bearer secret", "X-Trace": "abc"},
        })
    payload = bus.payloads("http_request.completed")[0]
    assert payload["request_headers"]["Authorization"] == "<redacted>"
    assert payload["request_headers"]["X-Trace"] == "abc"


def test_http_request_invalid_method_raises():
    with pytest.raises(ToolError, match="method"):
        HTTPRequestTool().execute({"method": "BREW", "url": "https://x"})


def test_http_request_caps_response_chars():
    tool = HTTPRequestTool(max_response_chars=10)
    with respx.mock:
        respx.get("https://x.test/").mock(
            return_value=httpx.Response(200, text="x" * 1000,
                                         headers={"content-type": "text/plain"})
        )
        out = tool.execute({"method": "GET", "url": "https://x.test/"})
    assert "truncated" in out


# ── extract_html ─────────────────────────────────────────────────────────


def test_extract_html_from_html_string():
    out = ExtractHTMLTool().execute({
        "selector": "h1",
        "html": "<html><body><h1>One</h1><h1>Two</h1></body></html>",
    })
    assert "One" in out and "Two" in out


def test_extract_html_attribute_mode():
    out = ExtractHTMLTool().execute({
        "selector": "a",
        "html": '<a href="https://a/">x</a><a href="https://b/">y</a>',
        "attribute": "href",
    })
    assert "https://a/" in out
    assert "https://b/" in out


def test_extract_html_from_url():
    with respx.mock:
        respx.get("https://example.com/").mock(
            return_value=httpx.Response(200, text="<p>hello</p>")
        )
        out = ExtractHTMLTool().execute({"selector": "p", "url": "https://example.com/"})
    assert "hello" in out


def test_extract_html_requires_one_of_url_or_html():
    with pytest.raises(ToolError, match="url.*html"):
        ExtractHTMLTool().execute({"selector": "p"})
    with pytest.raises(ToolError, match="url.*html"):
        ExtractHTMLTool().execute({
            "selector": "p", "url": "https://x", "html": "<p>x</p>",
        })


def test_extract_html_limit_truncates():
    out = ExtractHTMLTool().execute({
        "selector": "li",
        "html": "<ul>" + "".join(f"<li>{i}</li>" for i in range(50)) + "</ul>",
        "limit": 5,
    })
    assert "45 more matches truncated" in out


def test_extract_html_no_matches_returns_string():
    out = ExtractHTMLTool().execute({
        "selector": "div.nonexistent", "html": "<p>x</p>",
    })
    assert "No matches" in out
