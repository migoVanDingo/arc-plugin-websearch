"""WebSearchPlugin — the stateless tool-pack shape.

Unlike arc-plugin-briefbot (session-scoped state + DB lifecycle), this
plugin owns no state. Each tool builds short-lived httpx.Clients per call.
The plugin object exists only to:

  - read per-tool config and instantiate the four tools at build time
  - hand the event bus down to each tool (via bind_bus)
  - contribute the tools via provides_tools()

No on_session_start / on_session_end needed.
"""
from __future__ import annotations

from typing import Any

from arc.plugin_api import PluginBuildContext, Tool

from arc_plugin_websearch import http
from arc_plugin_websearch.backends.base import SearchBackend
from arc_plugin_websearch.backends.brave import BraveBackend
from arc_plugin_websearch.backends.ddg_html import DDGHTMLBackend
from arc_plugin_websearch.backends.google_pse import GooglePSEBackend
from arc_plugin_websearch.backends.searxng import SearXNGBackend
from arc_plugin_websearch.extractors.base import Extractor
from arc_plugin_websearch.extractors.bs4_extractor import BS4Extractor
from arc_plugin_websearch.extractors.chain import ExtractorChain
from arc_plugin_websearch.extractors.raw_extractor import RawExtractor
from arc_plugin_websearch.extractors.trafilatura_extractor import TrafilaturaExtractor
from arc_plugin_websearch.tools.extract_html import ExtractHTMLTool
from arc_plugin_websearch.tools.http_request import HTTPRequestTool
from arc_plugin_websearch.tools.read_url import ReadURLTool
from arc_plugin_websearch.tools.web_search import WebSearchTool


_BACKEND_BUILDERS = {
    "brave": BraveBackend,
    "ddg-html": DDGHTMLBackend,
    "searxng": SearXNGBackend,
    "google-pse": GooglePSEBackend,
}

_EXTRACTOR_BUILDERS = {
    "trafilatura": TrafilaturaExtractor,
    "raw": RawExtractor,
    "bs4-text": BS4Extractor,
}


class WebSearchPlugin:
    """Holds the four tools and a reference to the bus."""

    name = "websearch"

    def __init__(self, tools: list[Tool]) -> None:
        self._tools = tools
        self._bus: Any = None

    def bind_bus(self, bus: Any) -> None:
        self._bus = bus
        # Propagate to every tool that defines bind_bus. arc's runtime also
        # does this generically (via _bind_bus_to_tools after the merge), but
        # doing it eagerly here is harmless and keeps the plugin self-contained
        # for direct testing.
        for t in self._tools:
            binder = getattr(t, "bind_bus", None)
            if callable(binder):
                binder(bus)

    def provides_tools(self) -> list[Tool]:
        return list(self._tools)


# ── Entry point ────────────────────────────────────────────────────────────


def build(config: dict, build_ctx: PluginBuildContext) -> WebSearchPlugin:
    """Construct the four tools from per-tool config blocks.

    Config shape (all keys optional, defaults shown):

        web_search:
          backend: brave                  # 'brave'|'ddg-html'|'searxng'|'google-pse'
          api_key_env: BRAVE_API_KEY
          base_url: null                  # required for searxng
          default_count: 10
          max_count: 20
          timeout_seconds: 15
          backend_params: {}              # e.g. {cx: "..."} for google-pse
        read_url:
          extractor: trafilatura          # 'trafilatura'|'raw'|'bs4-text'
          default_max_chars: 50000
          timeout_seconds: 30
          user_agent: "arc/2 ..."
          allow_schemes: [http, https]
        http_request:
          default_timeout_seconds: 30
          max_response_chars: 50000
          user_agent: "arc/2 ..."
        extract_html:
          timeout_seconds: 30
          max_results: 200
    """
    web_search_cfg = dict(config.get("web_search") or {})
    read_url_cfg = dict(config.get("read_url") or {})
    http_request_cfg = dict(config.get("http_request") or {})
    extract_html_cfg = dict(config.get("extract_html") or {})

    user_agent = (
        web_search_cfg.get("user_agent")
        or read_url_cfg.get("user_agent")
        or http_request_cfg.get("user_agent")
        or http.DEFAULT_USER_AGENT
    )

    backend = _build_backend(web_search_cfg, user_agent=user_agent)
    extractor = _build_extractor(read_url_cfg)

    tools: list[Tool] = [
        WebSearchTool(
            backend=backend,
            default_count=int(web_search_cfg.get("default_count", 10)),
            max_count=int(web_search_cfg.get("max_count", 20)),
            backend_params=dict(web_search_cfg.get("backend_params") or {}),
        ),
        ReadURLTool(
            extractor=extractor,
            default_max_chars=int(read_url_cfg.get("default_max_chars", 50_000)),
            timeout_seconds=int(read_url_cfg.get("timeout_seconds", 30)),
            user_agent=str(read_url_cfg.get("user_agent", user_agent)),
            allow_schemes=list(read_url_cfg.get("allow_schemes", ["http", "https"])),
        ),
        HTTPRequestTool(
            default_timeout_seconds=int(http_request_cfg.get("default_timeout_seconds", 30)),
            max_response_chars=int(http_request_cfg.get("max_response_chars", 50_000)),
            user_agent=str(http_request_cfg.get("user_agent", user_agent)),
        ),
        ExtractHTMLTool(
            timeout_seconds=int(extract_html_cfg.get("timeout_seconds", 30)),
            max_results=int(extract_html_cfg.get("max_results", 200)),
            user_agent=str(extract_html_cfg.get("user_agent", user_agent)),
        ),
    ]
    plugin = WebSearchPlugin(tools=tools)
    if build_ctx.bus is not None:
        plugin.bind_bus(build_ctx.bus)
    return plugin


def _build_backend(cfg: dict[str, Any], *, user_agent: str) -> SearchBackend:
    name = str(cfg.get("backend", "brave"))
    if name not in _BACKEND_BUILDERS:
        raise ValueError(
            f"unknown web_search.backend {name!r}; known: {sorted(_BACKEND_BUILDERS)}"
        )
    timeout = int(cfg.get("timeout_seconds", 15))
    base_url = cfg.get("base_url") or None
    extras = dict(cfg.get("backend_params") or {})

    if name == "brave":
        return BraveBackend(
            api_key_env=str(cfg.get("api_key_env", "BRAVE_API_KEY")),
            base_url=base_url,
            timeout_seconds=timeout,
            user_agent=user_agent,
        )
    if name == "ddg-html":
        return DDGHTMLBackend(
            base_url=base_url,
            timeout_seconds=timeout,
            user_agent=user_agent,
        )
    if name == "searxng":
        return SearXNGBackend(
            base_url=base_url,
            api_key_env=cfg.get("api_key_env") or None,
            timeout_seconds=timeout,
            user_agent=user_agent,
        )
    if name == "google-pse":
        return GooglePSEBackend(
            api_key_env=str(cfg.get("api_key_env", "GOOGLE_CSE_API_KEY")),
            cx=extras.get("cx"),
            base_url=base_url,
            timeout_seconds=timeout,
            user_agent=user_agent,
        )
    raise AssertionError("unreachable")  # _BACKEND_BUILDERS gate above


def _build_extractor(cfg: dict[str, Any]) -> Extractor:
    name = str(cfg.get("extractor", "trafilatura"))
    if name not in _EXTRACTOR_BUILDERS:
        raise ValueError(
            f"unknown read_url.extractor {name!r}; known: {sorted(_EXTRACTOR_BUILDERS)}"
        )
    primary = _EXTRACTOR_BUILDERS[name]()
    # Trafilatura is the only extractor where "empty output" is common
    # enough that auto-fallback matters. For `raw` and `bs4-text`, empty
    # output usually means the page genuinely had no content.
    if name == "trafilatura":
        return ExtractorChain(primary=primary, fallback=RawExtractor())
    return primary
