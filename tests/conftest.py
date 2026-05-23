"""Shared fixtures: stub bus / build context + HTML fixtures.

Network is mocked via `respx` (httpx test transport) in each backend/tool
test — no live network in the suite.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest


# ── Stub bus + build context ─────────────────────────────────────────────


class StubBus:
    def __init__(self) -> None:
        self.emitted: list[Any] = []

    def emit(self, event: Any) -> None:
        self.emitted.append(event)

    def types(self) -> list[str]:
        return [getattr(e, "type", "?") for e in self.emitted]

    def payloads(self, type_: str) -> list[dict]:
        return [e.payload for e in self.emitted if getattr(e, "type", None) == type_]


@dataclass(frozen=True)
class StubBuildContext:
    sessions_dir: Path = field(default_factory=lambda: Path("/tmp/sessions"))
    session_id: str = "SES_test"
    config_snapshot_yaml: str | None = None
    user_gate: Any = None
    bus: Any = None


@pytest.fixture
def bus() -> StubBus:
    return StubBus()


@pytest.fixture
def build_ctx(bus: StubBus) -> StubBuildContext:
    return StubBuildContext(bus=bus)


# ── HTML fixtures ────────────────────────────────────────────────────────


EXAMPLE_HTML = """<!doctype html>
<html><head><title>Example Domain</title></head>
<body>
  <h1>Example Domain</h1>
  <p>This domain is for use in illustrative examples in documents.</p>
  <p>More information... <a href="https://www.iana.org/domains/example">here</a></p>
  <script>console.log("noise")</script>
  <style>.hidden { display: none }</style>
</body></html>
"""


BRAVE_RESPONSE = {
    "web": {
        "results": [
            {
                "title": "Example Result One",
                "url": "https://one.example.com",
                "description": "First snippet.",
                "age": "2 days ago",
            },
            {
                "title": "Example Result Two",
                "url": "https://two.example.com",
                "description": "Second snippet.",
            },
        ]
    }
}


DDG_HTML_SERP = """<html><body>
  <div class="result">
    <a class="result__a" href="https://a.example.com">First result title</a>
    <a class="result__snippet">First snippet body.</a>
  </div>
  <div class="result">
    <a class="result__a" href="https://b.example.com">Second result title</a>
    <a class="result__snippet">Second snippet body.</a>
  </div>
</body></html>
"""


SEARXNG_RESPONSE = {
    "results": [
        {
            "title": "SearXNG Hit One",
            "url": "https://x.example.com",
            "content": "Content one.",
            "publishedDate": "2026-01-01",
        },
        {
            "title": "SearXNG Hit Two",
            "url": "https://y.example.com",
            "content": "Content two.",
        },
    ]
}


GOOGLE_PSE_RESPONSE = {
    "items": [
        {
            "title": "Google Result 1",
            "link": "https://g1.example.com",
            "snippet": "Snippet 1",
        },
        {
            "title": "Google Result 2",
            "link": "https://g2.example.com",
            "snippet": "Snippet 2",
        },
    ]
}


@pytest.fixture
def example_html() -> str:
    return EXAMPLE_HTML


@pytest.fixture
def brave_response() -> dict:
    return BRAVE_RESPONSE


@pytest.fixture
def ddg_html_serp() -> str:
    return DDG_HTML_SERP


@pytest.fixture
def searxng_response() -> dict:
    return SEARXNG_RESPONSE


@pytest.fixture
def google_pse_response() -> dict:
    return GOOGLE_PSE_RESPONSE
