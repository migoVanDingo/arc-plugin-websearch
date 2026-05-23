"""Plugin assembly tests — build() wires the right backend / extractor /
tools from the config dict."""
from __future__ import annotations

import pytest

from arc_plugin_websearch.backends.brave import BraveBackend
from arc_plugin_websearch.backends.ddg_html import DDGHTMLBackend
from arc_plugin_websearch.backends.google_pse import GooglePSEBackend
from arc_plugin_websearch.backends.searxng import SearXNGBackend
from arc_plugin_websearch.extractors.bs4_extractor import BS4Extractor
from arc_plugin_websearch.extractors.chain import ExtractorChain
from arc_plugin_websearch.extractors.raw_extractor import RawExtractor
from arc_plugin_websearch.extractors.trafilatura_extractor import TrafilaturaExtractor
from arc_plugin_websearch.plugin import WebSearchPlugin, build


def test_build_default_config_assembles_4_tools(build_ctx):
    plugin = build({}, build_ctx)
    assert isinstance(plugin, WebSearchPlugin)
    assert plugin.name == "websearch"
    names = {t.name for t in plugin.provides_tools()}
    assert names == {"web_search", "read_url", "http_request", "extract_html"}


def test_build_picks_brave_by_default(build_ctx):
    plugin = build({}, build_ctx)
    web_search = next(t for t in plugin.provides_tools() if t.name == "web_search")
    assert isinstance(web_search._backend, BraveBackend)


def test_build_selects_ddg_html(build_ctx):
    plugin = build({"web_search": {"backend": "ddg-html"}}, build_ctx)
    web_search = next(t for t in plugin.provides_tools() if t.name == "web_search")
    assert isinstance(web_search._backend, DDGHTMLBackend)


def test_build_selects_searxng_with_base_url(build_ctx):
    plugin = build({
        "web_search": {"backend": "searxng", "base_url": "http://localhost:8888"},
    }, build_ctx)
    web_search = next(t for t in plugin.provides_tools() if t.name == "web_search")
    assert isinstance(web_search._backend, SearXNGBackend)


def test_build_selects_google_pse_with_cx(build_ctx):
    plugin = build({
        "web_search": {
            "backend": "google-pse",
            "backend_params": {"cx": "abc123"},
        },
    }, build_ctx)
    web_search = next(t for t in plugin.provides_tools() if t.name == "web_search")
    assert isinstance(web_search._backend, GooglePSEBackend)


def test_build_rejects_unknown_backend(build_ctx):
    with pytest.raises(ValueError, match="backend"):
        build({"web_search": {"backend": "made-up"}}, build_ctx)


def test_build_trafilatura_is_wrapped_in_chain(build_ctx):
    plugin = build({}, build_ctx)
    read_url = next(t for t in plugin.provides_tools() if t.name == "read_url")
    assert isinstance(read_url._extractor, ExtractorChain)
    assert read_url._extractor.name == "trafilatura"


def test_build_raw_extractor_is_direct(build_ctx):
    """raw and bs4-text don't get the fallback wrapper — empty output for
    those usually means the page really had no content."""
    plugin = build({"read_url": {"extractor": "raw"}}, build_ctx)
    read_url = next(t for t in plugin.provides_tools() if t.name == "read_url")
    assert isinstance(read_url._extractor, RawExtractor)


def test_build_bs4_extractor_is_direct(build_ctx):
    plugin = build({"read_url": {"extractor": "bs4-text"}}, build_ctx)
    read_url = next(t for t in plugin.provides_tools() if t.name == "read_url")
    assert isinstance(read_url._extractor, BS4Extractor)


def test_build_rejects_unknown_extractor(build_ctx):
    with pytest.raises(ValueError, match="extractor"):
        build({"read_url": {"extractor": "voodoo"}}, build_ctx)


def test_build_propagates_bus_to_tools_that_define_bind_bus(build_ctx):
    """web_search, read_url, http_request all emit events and define
    bind_bus; extract_html is a simple selector tool and doesn't."""
    plugin = build({}, build_ctx)
    by_name = {t.name: t for t in plugin.provides_tools()}
    assert by_name["web_search"]._bus is build_ctx.bus
    assert by_name["read_url"]._bus is build_ctx.bus
    assert by_name["http_request"]._bus is build_ctx.bus
    # extract_html intentionally has no bind_bus / _bus
    assert not hasattr(by_name["extract_html"], "_bus")


def test_build_applies_per_tool_config(build_ctx):
    plugin = build({
        "web_search": {"default_count": 5, "max_count": 7},
        "read_url": {"default_max_chars": 1000, "timeout_seconds": 5},
        "http_request": {"default_timeout_seconds": 5, "max_response_chars": 2000},
        "extract_html": {"max_results": 50},
    }, build_ctx)
    by_name = {t.name: t for t in plugin.provides_tools()}
    assert by_name["web_search"]._default_count == 5
    assert by_name["web_search"]._max_count == 7
    assert by_name["read_url"]._default_max_chars == 1000
    assert by_name["read_url"]._timeout == 5
    assert by_name["http_request"]._default_timeout == 5
    assert by_name["http_request"]._max_chars == 2000
    assert by_name["extract_html"]._max_results == 50
