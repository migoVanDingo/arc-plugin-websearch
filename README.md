# arc-plugin-websearch

Four web tools for arc: search, fetch, raw HTTP, and CSS-selector
extraction — with pluggable backends and extractors so you can swap
implementations without forking.

| Tool | What it does |
|---|---|
| `web_search`   | Search the web. Pluggable backend: Brave (default), DuckDuckGo (no API key), SearXNG (self-hosted), or Google PSE. |
| `read_url`     | Fetch a page and return its primary text content. Pluggable extractor: trafilatura (default), raw, or bs4-text. |
| `http_request` | Raw HTTP for APIs (GET/POST/PUT/PATCH/DELETE/HEAD). JSON-aware. |
| `extract_html` | CSS-selector extraction from a URL or HTML string. |

## Install

```bash
pip install arc-plugin-websearch
```

Set the API key for your chosen backend. Brave is the recommended default:

```bash
export BRAVE_API_KEY="..."     # https://brave.com/search/api/
```

(DuckDuckGo needs no key. SearXNG needs your instance URL. Google PSE needs
both a key and a search-engine ID — see Config below.)

On the next `arc` launch:

```
[+] new arc plugin discovered: websearch (from arc-plugin-websearch v0.1.0)
    enable it for this and future sessions? [Y/n] Y
```

After that, the four tools appear in every session. Toggle via
`arc plugins` whenever.

## Config

All keys optional; reasonable defaults shipped. Lives under the plugin's
`config:` block in `~/.arc/config.yml`.

```yaml
plugins:
  enabled:
    - name: websearch
      enabled: true
      config:
        web_search:
          backend: brave                # 'brave'|'ddg-html'|'searxng'|'google-pse'
          api_key_env: BRAVE_API_KEY    # backend-specific
          base_url: null                # required for searxng (e.g. http://localhost:8888)
          default_count: 10
          max_count: 20
          timeout_seconds: 15
          backend_params: {}            # e.g. {cx: "..."} for google-pse
        read_url:
          extractor: trafilatura        # 'trafilatura'|'raw'|'bs4-text'
          default_max_chars: 50000
          timeout_seconds: 30
          allow_schemes: [http, https]  # 'file', 'data' rejected
        http_request:
          default_timeout_seconds: 30
          max_response_chars: 50000
        extract_html:
          timeout_seconds: 30
          max_results: 200
```

### Backends

| Backend | Needs | Notes |
|---|---|---|
| `brave` (default)  | `BRAVE_API_KEY` env var | Free tier exists. Recommended. |
| `ddg-html`         | nothing | Scrapes DDG's HTML SERP. Fragile (layout-dependent) but key-free. |
| `searxng`          | `base_url` set to your instance | Self-hosted meta-search. Optional bearer via `api_key_env`. |
| `google-pse`       | `GOOGLE_CSE_API_KEY` + `backend_params.cx` | Google Custom Search. `num` capped at 10/request by the API. |

Switching backends is a two-line config edit; no code change.

### Extractors

| Extractor | Notes |
|---|---|
| `trafilatura` (default) | Purpose-built for article text. Falls back to `raw` if it returns empty (event records `fallback_used: true`). |
| `raw`        | Strip tags + collapse whitespace. The fallback target above. |
| `bs4-text`   | BeautifulSoup `get_text()` after removing script/style/noscript. |

## Safety

- `read_url`: `localhost`, `127.0.0.1`, `0.0.0.0`, `::1` are hard-blocked
  at the tool layer. Non-`http`/`https` schemes (`file://`, `data:`)
  rejected unless explicitly allowed in `allow_schemes`.
- `http_request`: makes whatever request you tell it to. arc's existing
  `guard` plugin can pattern-match on the tool name + `command` field;
  per-tool escalation on `url` / `method` is a small upstream arc change
  documented in `_design/0001` — until then, treat `http_request` like
  bash and pin patterns in the guard config that you'd want to escalate.
- `http_request` events redact `Authorization`, `X-Api-Key`,
  `X-Subscription-Token`, `Cookie`, `Proxy-Authorization` from the
  emitted observability payload (NOT from the wire — the request still
  sends them).

## Observability

Every tool call emits a structured event:

```
web_search.requested      backend, query, count, freshness, country, result_count, took_ms
web_fetch.requested       extractor, url, http_status, bytes_fetched, extracted_chars,
                          truncated_at, fallback_used
http_request.completed    method, url, request_headers (redacted), status, content_type, bytes
```

These land in `events.jsonl` alongside arc's standard tool envelope
events, and render in `session.log` via arc's generic-fallback formatter.

Replay is automatic: arc captures the tool input + output string. Replays
never re-hit the network.

## Failure modes

| What happens | Behavior |
|---|---|
| Missing API key env var | `ToolError("BRAVE_API_KEY not set ...")`; model adapts. |
| Backend 401/403         | `ToolError("Brave search: 401 unauthorized — check BRAVE_API_KEY")`. |
| Backend 429             | `ToolError("Brave search: rate-limited; wait and retry")`. arc's cycle detector catches loops. |
| Network timeout         | `ToolError("network timeout after Ns")`. |
| Empty search results    | Tool returns `"No results for: <query>"` as success — model can change query. |
| Trafilatura returns empty | Auto-falls back to `raw`; event records `fallback_used: true`. |
| Blocked URL scheme      | `ToolError("scheme 'file' not allowed")`. |
| Content > `max_chars`   | Truncated at the tool with `… [+N chars truncated]`; not an error. |

## Development

```bash
git clone https://github.com/.../arc-plugin-websearch
cd arc-plugin-websearch
pip install -e ".[dev]"
pip install -e /path/to/arc/v2     # for `from arc.plugin_api import ...`
pytest
```

The suite uses `respx` to mock httpx, so no live network is hit. 67 tests,
~1s. The optional `tests/integration/` directory (not in CI by default)
exercises real Brave + example.com calls when `BRAVE_API_KEY` is set.

## Design

See [`_design/0001-web-search-and-fetch.md`](_design/0001-web-search-and-fetch.md)
for the full design: why pluggable backends, why the stateless tool-pack
shape (vs. briefbot's session-scoped shape), the extractor fallback
chain, and what's deferred to future phases (image/news search, prompt-
injection scanning, response caching, robots.txt).

## License

MIT.
