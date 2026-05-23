"""http_request — raw HTTP for APIs. Distinct from read_url (which strips HTML)."""
from __future__ import annotations

import json
from typing import Any, ClassVar

import httpx

from arc.plugin_api import RuntimeEvent, ToolError, ToolInputSchema

from arc_plugin_websearch import http


_VALID_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD")

# Response headers that are useful to surface; the rest are dropped to keep
# tool output tight. (Content-Type tells the model how to interpret the body;
# Content-Length / Server are mild diagnostics.)
_KEEP_HEADERS = ("content-type", "content-length", "server", "x-ratelimit-remaining",
                 "retry-after", "location", "etag")

# Auth-bearing headers redacted from observability events (NOT from the wire).
_REDACT_HEADER_NAMES = frozenset({"authorization", "x-api-key", "x-subscription-token",
                                   "cookie", "proxy-authorization"})


class HTTPRequestTool:
    name: ClassVar[str] = "http_request"
    description: ClassVar[str] = (
        "Make an HTTP request. Use for APIs, not HTML pages (use read_url for those). "
        "Returns the status, key response headers, and body (pretty-printed if JSON)."
    )

    def __init__(
        self,
        *,
        default_timeout_seconds: int = 30,
        max_response_chars: int = 50_000,
        user_agent: str = http.DEFAULT_USER_AGENT,
    ) -> None:
        self._default_timeout = default_timeout_seconds
        self._max_chars = max_response_chars
        self._user_agent = user_agent
        self._bus: Any = None

    def bind_bus(self, bus: Any) -> None:
        self._bus = bus

    @property
    def input_schema(self) -> ToolInputSchema:
        return ToolInputSchema(
            properties={
                "method": {
                    "type": "string", "enum": list(_VALID_METHODS),
                    "description": "HTTP verb.",
                },
                "url": {"type": "string", "description": "Target URL."},
                "headers": {"type": "object", "description": "Optional request headers."},
                "params": {"type": "object", "description": "Optional query-string params."},
                "body": {
                    "type": "string",
                    "description": "Optional request body (auto-JSONed if it parses).",
                },
                "timeout": {
                    "type": "integer",
                    "description": f"Seconds (default {self._default_timeout}).",
                    "minimum": 1, "maximum": 300,
                },
            },
            required=["method", "url"],
        )

    def execute(self, input: dict[str, Any]) -> str:
        method = str(input.get("method", "")).upper()
        if method not in _VALID_METHODS:
            raise ToolError(f"`method` must be one of {list(_VALID_METHODS)}")
        url = str(input.get("url", "")).strip()
        if not url:
            raise ToolError("`url` is required")

        headers = dict(input.get("headers") or {})
        params = dict(input.get("params") or {})
        raw_body = input.get("body")
        timeout = int(input.get("timeout", self._default_timeout))

        # Auto-JSON: if the body parses as JSON, send it as JSON. Otherwise
        # send as plain text and let the user/headers govern Content-Type.
        json_body = None
        text_body: str | None = None
        if isinstance(raw_body, str) and raw_body.strip():
            try:
                json_body = json.loads(raw_body)
            except (TypeError, ValueError):
                text_body = raw_body

        try:
            with http.client(timeout_seconds=timeout, user_agent=self._user_agent) as c:
                resp = c.request(
                    method, url,
                    params=params or None,
                    headers=headers or None,
                    json=json_body,
                    content=text_body,
                )
        except httpx.TimeoutException:
            raise ToolError(f"network timeout after {timeout}s") from None
        except httpx.HTTPError as exc:
            raise ToolError(f"network error: {exc}") from None

        body_text = self._format_body(resp)
        self._emit(method, url, headers, resp)

        # Render: status + select headers + body
        lines = [f"HTTP/{resp.http_version} {resp.status_code} {resp.reason_phrase or ''}".rstrip()]
        for k in _KEEP_HEADERS:
            if k in resp.headers:
                lines.append(f"{k}: {resp.headers[k]}")
        lines.append("")
        lines.append(body_text)
        return "\n".join(lines).rstrip()

    def _format_body(self, resp: httpx.Response) -> str:
        ctype = resp.headers.get("content-type", "").lower()
        text = resp.text
        if "json" in ctype:
            try:
                pretty = json.dumps(resp.json(), indent=2, sort_keys=False)
            except (TypeError, ValueError):
                pretty = text
            text = pretty
        if len(text) > self._max_chars:
            text = text[: self._max_chars].rstrip() + f"\n… [+{len(resp.text) - self._max_chars} chars truncated]"
        return text

    def _emit(
        self,
        method: str,
        url: str,
        headers: dict[str, Any],
        resp: httpx.Response,
    ) -> None:
        if self._bus is None:
            return
        redacted = {
            k: ("<redacted>" if k.lower() in _REDACT_HEADER_NAMES else v)
            for k, v in headers.items()
        }
        self._bus.emit(RuntimeEvent(
            type="http_request.completed",
            stage="tool",
            payload={
                "method": method,
                "url": url,
                "request_headers": redacted,
                "status": resp.status_code,
                "content_type": resp.headers.get("content-type"),
                "bytes": len(resp.content),
            },
        ))
