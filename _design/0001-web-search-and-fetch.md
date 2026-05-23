# 0001 — Web search and fetch plugin

## Motivation

v1 of arc shipped a useful little family of web tools — `web_search`,
`read_url`, `http_request`, `extract_html` — that turned the agent from a
closed-world thing into one that could answer questions past its training
cutoff or pull facts from documentation. v2 currently has neither search
nor fetch. For reverse-engineering work (looking up CVE writeups,
decompiler tips, library docs) and general research, this is the single
most valuable capability v2 is missing.

The implementation needs to be backend-pluggable. Brave is the
recommended default, but a user with a Google Programmable Search
subscription, a self-hosted SearXNG, or just DuckDuckGo-via-HTML scrape
shouldn't have to fork the tool. Same for content extraction —
trafilatura is great, but `raw` and `bs4-text` are useful fallbacks.

The phase is deliberately narrow: search + fetch + extract + raw HTTP.
Image/news search, prompt-injection scanning, response caching, and an
artifact store for large pages are explicit follow-ups.

This plugin lives outside arc's main repo because it's optional, brings
network deps, and has personal-config concerns (API keys, backend
choice) that don't belong in arc's defaults.

## Scope

In:
- Four tools — `web_search`, `read_url`, `http_request`, `extract_html`
- Pluggable `SearchBackend` Protocol (`brave` | `ddg-html` | `searxng` | `google-pse`)
- Pluggable `Extractor` Protocol (`trafilatura` | `raw` | `bs4-text`)
- Headless safety: non-GET verbs on `http_request` and `file://` /
  `localhost` URLs on `read_url` route through arc's existing guard
  escalation flow (per-tool input-field config — see §"Safety integration")
- Backend / extractor selection via `tools.config.<tool>.{backend,extractor}`
- New event types `web_search.requested` / `web_fetch.requested` for
  backend-level observability (the tool input alone doesn't capture
  *which* backend or *how long*)

Out (deferred):
- Image search, news search (mechanical Brave wrappers; trivial to add later)
- Prompt-injection scanning of fetched content. v1 ran a heuristic scan
  before returning; in v2 this belongs in a sibling `after_tool_call`
  plugin operating on `read_url`'s output, not in the tool itself.
- Artifact store / paging of large content. v2 has no artifact layer.
  For now: truncate at the tool with a `… [+N chars truncated]` marker.
- Response caching by URL. A `web_cache` plugin around
  `before_tool_call` / `after_tool_call` could memoize within a session.
- Robots.txt honoring. Polite but not required for a personal agent;
  revisit if we ever expose this to multi-tenant use.

## The plugin API contract

This plugin targets **`arc.plugin_api` v0.1**.

Unlike `arc-plugin-briefbot` (the session-scoped shape), this is the
**stateless tool pack** shape:

- No session-scoped state. Each tool invocation builds its own
  short-lived `httpx.Client` or reuses a module-level one. No DB, no
  connection pool.
- Implements `provides_tools()` only. No `on_session_start` /
  `on_session_end`. The plugin object is a tool-bundle.
- Implements `bind_bus(bus)` (and hands the bus down to each tool) so
  tools can emit `web_search.requested` / `web_fetch.requested` events
  with backend-level detail.
- Imports public types **only** from `arc.plugin_api`.

The plugin's `build()` reads each tool's config block and constructs the
tool with its backend / extractor already wired. The tool itself stays
oblivious to which backend it got — same contract as v1's tools, with
the wiring moved out of the tool body.

## Architecture

```
src/arc_plugin_websearch/
  __init__.py
  plugin.py              ← build() entry point; assembles the four tools
  tools/
    __init__.py
    web_search.py        ← Tool — delegates to a SearchBackend
    read_url.py          ← Tool — delegates to an Extractor
    http_request.py      ← Tool — uses httpx directly (no backend abstraction)
    extract_html.py      ← Tool — uses bs4 directly (no backend abstraction)
  backends/
    __init__.py
    base.py              ← SearchBackend Protocol + SearchQuery/SearchResult dataclasses
    brave.py
    ddg_html.py
    searxng.py
    google_pse.py
  extractors/
    __init__.py
    base.py              ← Extractor Protocol + ExtractedContent
    trafilatura_extractor.py
    raw_extractor.py
    bs4_extractor.py
  http.py                ← shared httpx client (timeout, UA, redirect policy)
tests/
  conftest.py            ← StubBus / VCR-style HTTP fixtures
  test_plugin.py
  test_web_search.py
  test_read_url.py
  test_http_request.py
  test_extract_html.py
  test_search_backends.py
  test_extractors.py
  fixtures/
    brave_response.json
    searxng_response.json
    ddg_response.html
    example_page.html
_design/
  0001-web-search-and-fetch.md
```

### `SearchBackend` Protocol

```python
class SearchBackend(Protocol):
    name: ClassVar[str]        # "brave" | "searxng" | "ddg-html" | "google-pse"

    def search(self, query: SearchQuery) -> list[SearchResult]: ...
```

`SearchQuery` and `SearchResult` are small frozen dataclasses capturing the
lowest common denominator across providers:

```python
@dataclass(frozen=True)
class SearchQuery:
    query: str
    count: int = 10
    country: str | None = None
    freshness: str | None = None              # "pd"|"pw"|"pm"|"py"
    safe_search: str = "moderate"             # "off"|"moderate"|"strict"
    extras: dict[str, Any] = field(default_factory=dict)   # backend-specific

@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    description: str
    age: str | None = None
```

Backend-specific extras (Brave's `goggles_id`, Google's `cx`) live in
`extras` and the backend picks them out.

Backends are tiny:
- Brave: ~60 lines (one JSON endpoint, straightforward auth)
- SearXNG: ~40 lines (JSON output mode of an open-source meta-engine)
- DDG-HTML: ~80 lines (scrape the HTML SERP — no auth, fragile but useful)
- Google PSE: ~50 lines (Custom Search JSON API; needs `cx` + key)

Each handles its own auth (env var name from config), error mapping
(401/429 → `ToolError` with a model-actionable message), and JSON / HTML
shape. Network errors map to a generic "transient network error" — but
the tool itself does not retry. arc's existing tool-cycle detector covers
infinite retry storms; per-tool retry would obscure that signal.

### `Extractor` Protocol

```python
class Extractor(Protocol):
    name: ClassVar[str]        # "trafilatura" | "raw" | "bs4-text"

    def extract(self, html: str, *, url: str) -> ExtractedContent: ...

@dataclass(frozen=True)
class ExtractedContent:
    text: str
    title: str | None
    metadata: dict[str, Any]   # language, byline, sitename when available
```

Default `trafilatura`. Falls back to `raw` (basic strip-tags + collapse
whitespace) if trafilatura returns empty text — flagged in the event as
`extractor_fallback: true`. `bs4-text` is the third option for users who
want soup-level control; it's the same parser `extract_html` uses,
re-exposed for content extraction.

### Shared HTTP client

`http.py` owns one `httpx.Client` configured with:
- Timeout from config (default 30s)
- User-Agent from config (default `"arc/2 (+https://github.com/.../arc)"`)
- Redirect policy: `follow_redirects=True`, max 5
- HTTP/2 enabled

Tools call `http.get(url, **kwargs)` / `http.request(...)`. Centralizing
this keeps backend code simple and gives us one place to add a future
response-caching layer.

## Tool surface (for the model)

### `web_search`

```
name:        web_search
description: Search the web. Returns ranked results with title, URL, and
             a snippet. Use read_url to fetch the full text of any result.
input:       query        (string, required)
             count        (int, default 10, max 20)
             country      (string, optional 2-letter ISO)
             freshness    ('pd'|'pw'|'pm'|'py', optional)
             safe_search  ('off'|'moderate'|'strict', default 'moderate')
output:      multi-line text:
               [1] Title  (age)
                   https://url
                   description...
```

### `read_url`

```
name:        read_url
description: Fetch a web page and return its primary text content. HTML
             is stripped to readable prose. Use http_request for non-HTML.
input:       url         (string, required, http(s) only by default)
             max_chars   (int, default 50000)
output:      title\n\nextracted body... [+N chars truncated]
```

Truncation is at the tool layer — returning a giant page costs the user
real money. The model can re-call with `max_chars` larger if it really
needs more. Long-term we'd page this through an artifact store.

### `http_request`

```
name:        http_request
description: Make an HTTP request. Use for APIs, not HTML pages (use
             read_url for those).
input:       method   ('GET'|'POST'|'PUT'|'PATCH'|'DELETE'|'HEAD')
             url      (string, required)
             headers  (dict, optional)
             params   (dict, optional)
             body     (string, optional; auto-JSONed if it parses as JSON)
             timeout  (int seconds, default 30)
output:      status + select response headers + body
             (JSON pretty-printed if Content-Type matches, otherwise raw,
              truncated at 50k chars)
```

Non-GET verbs go through arc's guard escalation by default — see
§"Safety integration".

### `extract_html`

```
name:        extract_html
description: Extract elements from a URL or HTML string using a CSS
             selector.
input:       selector    (string CSS, required)
             url         (string, mutually exclusive with html)
             html        (string, mutually exclusive with url)
             attribute   (string, optional — return this attribute
                          instead of text content)
             limit       (int, default 200)
output:      one match per line; "(N more matches truncated)" footer
             when N > limit
```

## Config

User-facing config block (lives in `~/.arc/config.yml` under the plugin
entry):

```yaml
plugins:
  enabled:
    - name: websearch
      enabled: true
      config:
        web_search:
          backend: brave                    # 'brave'|'ddg-html'|'searxng'|'google-pse'
          api_key_env: BRAVE_API_KEY        # backend-specific; ignored by ddg-html
          base_url: null                    # null = backend default
          default_count: 10
          max_count: 20
          timeout_seconds: 15
          backend_params: {}                # e.g. {cx: "..."} for google-pse
        read_url:
          extractor: trafilatura            # 'trafilatura'|'raw'|'bs4-text'
          default_max_chars: 50000
          timeout_seconds: 30
          user_agent: "arc/2 (+https://github.com/.../arc)"
          allow_schemes: [http, https]      # 'file' / 'data' rejected
        http_request:
          default_timeout_seconds: 30
          max_response_chars: 50000
          user_agent: "arc/2 (+https://github.com/.../arc)"
        extract_html:
          timeout_seconds: 30
          max_results: 200
```

Backend selection is one config key. Switching from Brave to SearXNG is a
two-line change: `backend: searxng` + `base_url: http://localhost:8888`.

API keys are referenced by env-var name only; the plugin reads the
environment at construction time and never logs the value.

## Install UX

```
$ pip install arc-plugin-websearch
$ export BRAVE_API_KEY="..."        # or your chosen backend's key
$ arc
[+] new arc plugin discovered: websearch (from arc-plugin-websearch v0.1.0)
    enable it for this and future sessions? [Y/n]
Y
```

If the user has no `BRAVE_API_KEY` exported when the first `web_search`
call fires, the tool raises `ToolError("BRAVE_API_KEY not set — add it
to your .env")`. The model sees the message and stops; the user sees the
error in `session.log` and knows what to fix.

## Observability

```
web_search.requested
  { backend, query, count, freshness, country,
    took_ms, result_count, http_status }

web_fetch.requested
  { extractor, url, took_ms, bytes_fetched, content_type, http_status,
    extracted_chars, truncated_at, extractor_fallback }
```

Both are emitted via `self._bus.emit(...)` from inside the tool. arc's
standard `tool.call.started` / `completed` already covers the input dict
and final string; these events add backend-level detail that doesn't
naturally fit there.

API keys never appear in events. Query strings do — that's the user's
own input. URLs are recorded verbatim.

The generic-fallback formatter in arc's `log_writer` renders these as:

```
web_search.requested  backend=brave, query=ghidra script API, result_count=12, took_ms=410
web_fetch.requested   extractor=trafilatura, url=https://..., extracted_chars=18400, took_ms=1180
```

Sufficient for v0.1. Pretty per-event renders are a future formatter
entry-point group on arc's side.

## Recovery and failure modes

| Failure | Behavior |
|---|---|
| Missing API key env var | `ToolError("BRAVE_API_KEY not set — add it to your .env")`. Model sees, stops. |
| Backend 401 / 403 | `ToolError("Brave search: 401 unauthorized — check BRAVE_API_KEY")` |
| Backend 429 (rate limit) | `ToolError("Brave search: rate-limited; wait and retry")`. arc's cycle detector handles retry storms. |
| Network timeout | `ToolError("network timeout after 15s")` |
| Empty results | Returns `"No results for: <query>"` as a *success* string (model can change query) |
| Trafilatura returns empty | Auto-falls back to `raw` extractor; event records `extractor_fallback: true` |
| URL scheme not in `allow_schemes` | `ToolError("scheme 'file' not allowed")` |
| HTTP 4xx/5xx on `read_url` | `ToolError("HTTP 404 fetching ...")` |
| Content > `max_chars` | Truncated at the tool with `… [+N chars truncated]` marker; not an error |

Replay works for free: arc records tool inputs and outputs verbatim in
`events.jsonl`. Replaying never re-hits the network — it just replays
the recorded `tool_result` strings.

## Safety integration

Two patterns need to live in `guard.escalation_required_patterns` in the
user's arc config:

```yaml
plugins:
  enabled:
    - name: guard
      enabled: true
      config:
        escalation_required_patterns:
          # ... existing ...
          - '^http_request:.*\b(POST|PUT|PATCH|DELETE)\b'
          - '^read_url:.*\b(file|localhost|127\.0\.0\.1|0\.0\.0\.0)\b'
```

Arc's guard today inspects `call.input["command"]`. Two options to make
this work for non-shell tools:

1. **Extend guard to take a configurable per-tool input field**, defaulting
   to `command`. New tools declare the field via `escalation_check_field`
   in their config (`url` for `read_url`, a concatenation of `method` +
   `url` for `http_request`). Cleanest; one mental model for users.
2. **A tiny sibling `web_safety` plugin** that mirrors `safety_gate` but
   inspects `call.input["url"]` / `call.input["method"]` for tools whose
   names start with `web_` or `http_`.

Option 1 is preferred — it's a small change to guard, and it scales to
future tools (database queries, S3 ops). The change is in **arc itself**,
not in this plugin. Flag as a follow-up that this plugin depends on.

Until that change lands: this plugin ships without escalation hooks and
documents the gap. `read_url`'s `allow_schemes: [http, https]` still
blocks `file://` at the tool layer, so the most critical exposure is
covered.

## Test plan

### Unit — backends

For each of `brave`, `ddg-html`, `searxng`, `google-pse`:

1. Request shape (URL, headers, params) against a stubbed `httpx.Client`
2. Response parsing against a fixture (`fixtures/brave_response.json`, …)
3. Error mapping: 401 → `ToolError`, 429 → `ToolError`, network → `ToolError`

### Unit — extractors

For each of `trafilatura`, `raw`, `bs4-text`:

1. Well-formed HTML → text + title + metadata
2. Malformed / empty HTML → fallback chain
3. Encoding edge cases (UTF-8 mojibake, missing charset)

### Unit — tools

1. `web_search`: backend selection from config, default values, count
   clamping, empty-results message
2. `read_url`: scheme allowlist enforced, truncation marker, extractor
   fallback chain emits `extractor_fallback: true`
3. `http_request`: each verb, header pass-through, JSON body auto-detect,
   response truncation, auth headers redacted in event payload
4. `extract_html`: URL vs HTML mode, `attribute` extraction, `limit`
   truncation footer
5. Each tool emits its `*_requested` event with the right keys

### Unit — plugin

1. `build()` constructs all four tools with their configured backends
2. Tools missing from config get sane defaults (not a hard error)
3. `bind_bus()` propagates the bus to every tool that defines it
4. `provides_tools()` returns four tools with stable names

### Integration

- `test_web_search_live.py`: real Brave call, asserts result structure;
  skips without `BRAVE_API_KEY`
- `test_read_url_live.py`: fetches `https://example.com`, asserts the
  "Example Domain" title + a known body fragment

### Smoke (manual, not in CI)

- `pip install -e .`
- `export BRAVE_API_KEY=...`
- `arc` → first-run prompt → Y → `web_search "ghidra script API"`
- Switch backend to `searxng` in config.yml, restart, repeat
- `read_url https://example.com` end-to-end

## State

Planned.
