"""SSRF guard tests for the shared http.safe_request seam."""
from __future__ import annotations

import httpx
import pytest
import respx

from arc_plugin_websearch import http


def _resolves_to(monkeypatch, ip):
    monkeypatch.setattr(http, "_resolve_ips", lambda host: [ip])


@pytest.mark.parametrize("ip", [
    "169.254.169.254",  # cloud metadata
    "127.0.0.1",        # loopback
    "10.0.0.5",         # RFC1918
    "192.168.1.1",      # RFC1918
    "172.16.5.5",       # RFC1918
    "0.0.0.0",          # unspecified
])
def test_validate_url_blocks_nonpublic(monkeypatch, ip):
    _resolves_to(monkeypatch, ip)
    with pytest.raises(http.BlockedURLError):
        http.validate_url("http://anything.example/")


def test_validate_url_allows_public(monkeypatch):
    _resolves_to(monkeypatch, "93.184.216.34")
    http.validate_url("https://example.com/")  # no raise


def test_validate_url_rejects_scheme():
    for bad in ("file:///etc/passwd", "gopher://x/", "data:text/plain,hi"):
        with pytest.raises(http.BlockedURLError):
            http.validate_url(bad)


def test_safe_request_revalidates_redirect_to_private(monkeypatch):
    # public entry host resolves public; the redirect target is a private literal
    monkeypatch.setattr(http, "_resolve_ips",
                        lambda host: ["10.0.0.9"] if host == "internal.example" else ["93.184.216.34"])
    with respx.mock:
        respx.get("https://public.example/r").mock(return_value=httpx.Response(
            302, headers={"location": "http://internal.example/secret"}))
        with pytest.raises(http.BlockedURLError):
            http.safe_request("GET", "https://public.example/r")


def test_safe_request_enforces_size_cap(monkeypatch):
    _resolves_to(monkeypatch, "93.184.216.34")
    with respx.mock:
        respx.get("https://public.example/big").mock(
            return_value=httpx.Response(200, content=b"x" * 5000))
        with pytest.raises(http.BlockedURLError, match="cap"):
            http.safe_request("GET", "https://public.example/big", max_bytes=1000)


def test_safe_request_returns_body_within_cap(monkeypatch):
    _resolves_to(monkeypatch, "93.184.216.34")
    with respx.mock:
        respx.get("https://public.example/ok").mock(
            return_value=httpx.Response(200, text="hello"))
        resp = http.safe_request("GET", "https://public.example/ok")
        assert resp.status_code == 200
        assert resp.text == "hello"
