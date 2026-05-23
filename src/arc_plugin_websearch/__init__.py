"""arc-plugin-websearch — web search + fetch tools with pluggable backends.

Layout:
  plugin.py           — build() entry point; assembles the four tools
  http.py             — shared httpx client
  backends/           — SearchBackend protocol + Brave/DDG/SearXNG/Google PSE
  extractors/         — Extractor protocol + trafilatura/raw/bs4 + fallback chain
  tools/              — web_search, read_url, http_request, extract_html

See _design/0001-web-search-and-fetch.md for design rationale.
"""
__version__ = "0.1.0"
