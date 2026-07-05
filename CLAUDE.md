# arc-plugin-websearch

Out-of-tree arc plugin: four web tools with pluggable backends and
extractors. Forked from [`arc-plugin-template`](../arc-plugin-template).

## What's here

```
src/arc_plugin_websearch/
  plugin.py                  WebSearchPlugin + build()
  http.py                    Short-lived httpx.Client factory
  backends/
    base.py                  SearchBackend Protocol + SearchQuery/Result
    brave.py                 Default backend
    ddg_html.py              DuckDuckGo HTML scrape (no API key)
    searxng.py               Self-hosted meta-search
    google_pse.py            Google Custom Search
  extractors/
    base.py                  Extractor Protocol + ExtractedContent
    chain.py                 Primary → fallback wrapper
    trafilatura_extractor.py Default (article-text purpose-built)
    raw_extractor.py         Strip tags + collapse whitespace
    bs4_extractor.py         BeautifulSoup get_text
  tools/
    web_search.py            Delegates to a SearchBackend
    read_url.py              Delegates to an Extractor
    http_request.py          Raw HTTP for APIs
    extract_html.py          CSS-selector extraction (no event emission)
tests/                        respx-mocked httpx, no live network
_design/
  0001-web-search-and-fetch.md
```

## arc plugin API contract

Targets **`arc.plugin_api` v0.1**. Only imports from there:

```python
from arc.plugin_api import (
    PluginBuildContext, RuntimeEvent, Tool, ToolError, ToolInputSchema,
)
```

Plugin shape: **stateless tool pack** (vs. briefbot's session-scoped
shape). No `on_session_start` / `on_session_end`. `build()` reads each
tool's config block and instantiates the four tools with their backend /
extractor already wired.

## Backends (under `web_search.backend`)

| Name         | Class               | Env var(s)             | Needs |
|---|---|---|---|
| `brave` (default) | `BraveBackend`      | `BRAVE_API_KEY`        | API key |
| `ddg-html`        | `DDGHTMLBackend`    | none                   | nothing |
| `searxng`         | `SearXNGBackend`    | optional bearer        | `base_url` |
| `google-pse`      | `GooglePSEBackend`  | `GOOGLE_CSE_API_KEY`   | `cx` |

Adding a backend: implement `SearchBackend`, register in `_BACKEND_BUILDERS`
in `plugin.py`, write a parsing test using a fixture in `tests/conftest.py`.

## Extractors (under `read_url.extractor`)

| Name           | Class                   | Wrapped in `ExtractorChain`? |
|---|---|---|
| `trafilatura` (default) | `TrafilaturaExtractor`  | yes — falls back to `raw` on empty |
| `raw`                   | `RawExtractor`          | no — used as fallback target |
| `bs4-text`              | `BS4Extractor`          | no |

Adding an extractor: implement `Extractor`, register in
`_EXTRACTOR_BUILDERS`. Wrap in `ExtractorChain` if empty-output fallback
makes sense for it.

## Events emitted

```
web_search.requested        backend, query, count, freshness, country, result_count, took_ms
web_fetch.requested         extractor, url, http_status, bytes_fetched,
                            extracted_chars, truncated_at, fallback_used
http_request.completed      method, url, request_headers (redacted), status,
                            content_type, bytes
```

`extract_html` deliberately emits no events — it's pure CSS-selector
parsing with no backend-level state worth surfacing.

Auth-bearing headers (`Authorization`, `X-Api-Key`, `X-Subscription-Token`,
`Cookie`, `Proxy-Authorization`) are redacted in observability payloads
but NOT from the wire.

## SSRF defense — the one seam (`http.py`)

The 2026-07 audit flagged this as the highest-risk plugin; it is now hardened
(`agent-runtime/_mitigation/06`). **Every fetch MUST go through
`http.safe_request()`** — do not call `http.client()` directly from a tool.
`safe_request`:
- validates the scheme (http/https) and **resolves the host, rejecting any
  loopback / private / link-local (`169.254.169.254`) / reserved / multicast IP**
  (`validate_url`) — catches octal/decimal/mapped IPs + names resolving to loopback;
- follows redirects **manually** and re-validates **every hop** (a `302 → private`
  can't smuggle through);
- caps the decoded body at 10 MiB (gzip-bomb defense).

Tools translate `http.BlockedURLError` → `ToolError`. Search **backends**
(brave/google/searxng) still use `http.client()` directly — deliberate: they hit
fixed, user-configured API endpoints, not agent-controlled URLs. Residual: not
full DNS-rebind IP-pinning (a narrow resolve→connect TOCTOU window remains). Tests:
`tests/test_ssrf.py` + an autouse resolver stub in `conftest.py` keeps the suite
hermetic.

## Safety knobs (in tool config)

- `read_url.allow_schemes`: `[http, https]` by default; `file`, `data`
  rejected. `localhost`, `127.0.0.1`, `0.0.0.0`, `::1` are always blocked
  at the tool layer.
- `http_request`: no built-in escalation today. The design doc flags an
  upstream arc change (per-tool `escalation_check_field` for the `guard`
  plugin) that would let users pin escalation patterns to `url` / `method`.

## Testing

```bash
pip install -e ".[dev]"   # pulls in respx for httpx mocking
pip install -e ../v2       # arc.plugin_api shim
pytest                       # 67 tests, ~1s, no live network
```

Backend tests use `respx.mock` to capture and stub httpx requests.
Extractor tests run against the inline `EXAMPLE_HTML` fixture in
`conftest.py`. Tool tests stub backends and extractors directly.

## Gotchas

- `requires-python = ">=3.10"` to match arc. ruamel.yaml + httpx work on 3.10.
- httpx clients are short-lived (per-call via `http.client()` context
  manager) — don't hoist into a module-level singleton; arc's TUI tear-down
  ordering can dangle keep-alive sockets.
- DDG-HTML scrapes the no-JS endpoint at `html.duckduckgo.com`. Layout
  changes upstream will break it; the fix is in `_parse_ddg_html`'s
  selectors. Document any selector changes so future you knows what
  shifted.
- Google PSE caps `num` at 10 per request. We clamp at the backend layer
  and document it; pagination is deferred.
- Trafilatura's `extract_metadata` is a separate pass from `extract`. We
  do both, cheaply. Removing one to "optimize" loses title + author info.

## Common operations

- **Test against real Brave:** `BRAVE_API_KEY=... python3 -c "from
  arc_plugin_websearch.backends.brave import BraveBackend; from
  arc_plugin_websearch.backends.base import SearchQuery; print(
  BraveBackend(api_key_env='BRAVE_API_KEY').search(SearchQuery(query='ghidra')))"`
- **Smoke an extractor:** feed it the HTML from `tests/conftest.py`'s
  `EXAMPLE_HTML` and check the output. The chain logic is in
  `extractors/chain.py:18`.
- **Add a request param to web_search:** add to `SearchQuery` (base.py),
  thread it through each backend's `search()` body, surface it in
  `WebSearchTool.input_schema`, capture in `_emit`.
