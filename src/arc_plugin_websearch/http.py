"""Shared httpx client + the SSRF safety seam every fetch routes through.

Centralizing this gives us one place to set timeout / UA / redirect policy AND
one chokepoint for SSRF defense. `safe_request()` is the only fetch entry point
the tools should use — it validates the target (and every redirect hop) against
a resolved-IP denylist, follows redirects manually so a 3xx can't smuggle the
agent to an internal host, and caps the (decompressed) response body.

We use short-lived clients (per-call) rather than a global: constructing an
`httpx.Client` is cheap and per-call construction sidesteps shutdown-ordering
issues during arc's tear-down.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

import httpx

DEFAULT_USER_AGENT = "arc/2 (+https://github.com/anthropics/arc)"
DEFAULT_TIMEOUT_SECONDS = 30
MAX_REDIRECTS = 5
DEFAULT_ALLOW_SCHEMES = ("http", "https")
# Hard cap on the DECOMPRESSED response body — defeats gzip/deflate bombs that
# have a tiny Content-Length but inflate to gigabytes.
DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10 MiB


class BlockedURLError(ValueError):
    """A URL was rejected by the SSRF guard (bad scheme, private/loopback IP,
    unresolvable host, or an oversized response)."""


def _resolve_ips(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise BlockedURLError(f"cannot resolve host {host!r}: {exc}") from None
    return list({info[4][0] for info in infos})


def _ip_is_blocked(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True  # unparseable → refuse
    return (addr.is_private or addr.is_loopback or addr.is_link_local
            or addr.is_reserved or addr.is_multicast or addr.is_unspecified)


def validate_url(url: str, *, allow_schemes: tuple[str, ...] = DEFAULT_ALLOW_SCHEMES) -> None:
    """Raise BlockedURLError unless `url` is a public http(s) target.

    Resolves the host and rejects it if ANY resolved address is loopback,
    private (RFC1918), link-local (incl. 169.254.169.254 cloud metadata),
    reserved, multicast, or unspecified. Catches the octal/decimal/IPv4-mapped
    and public-name-resolving-to-loopback tricks that a string denylist misses.
    """
    parsed = urlparse(url)
    if parsed.scheme.lower() not in allow_schemes:
        raise BlockedURLError(
            f"scheme {parsed.scheme!r} not allowed (allow: {sorted(allow_schemes)})")
    host = parsed.hostname
    if not host:
        raise BlockedURLError("URL has no host")
    for ip in _resolve_ips(host):
        if _ip_is_blocked(ip):
            raise BlockedURLError(
                f"host {host!r} resolves to a non-public address ({ip}) — blocked by SSRF guard")


def client(
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    user_agent: str = DEFAULT_USER_AGENT,
    follow_redirects: bool = False,
) -> httpx.Client:
    """A configured short-lived httpx.Client. Redirects are OFF by default —
    fetches go through `safe_request`, which follows + re-validates them."""
    return httpx.Client(
        timeout=httpx.Timeout(timeout_seconds),
        headers={"User-Agent": user_agent},
        follow_redirects=follow_redirects,
        max_redirects=MAX_REDIRECTS,
    )


def safe_request(
    method: str,
    url: str,
    *,
    allow_schemes: tuple[str, ...] = DEFAULT_ALLOW_SCHEMES,
    max_bytes: int = DEFAULT_MAX_BYTES,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    user_agent: str = DEFAULT_USER_AGENT,
    **request_kwargs,
) -> httpx.Response:
    """SSRF-safe fetch. Validates the target and EVERY redirect hop, follows
    redirects manually, and caps the decompressed body at `max_bytes`.

    Raises BlockedURLError on a blocked target / oversized body; httpx errors
    (timeout, connection) propagate for the caller to translate.
    """
    validate_url(url, allow_schemes=allow_schemes)
    with client(timeout_seconds=timeout_seconds, user_agent=user_agent,
                follow_redirects=False) as c:
        current, method = url, method.upper()
        for _ in range(MAX_REDIRECTS + 1):
            resp = c.send(c.build_request(method, current, **request_kwargs), stream=True)
            if resp.is_redirect and resp.headers.get("location"):
                nxt = str(resp.url.join(resp.headers["location"]))
                resp.close()
                validate_url(nxt, allow_schemes=allow_schemes)  # re-validate each hop
                current = nxt
                if resp.status_code == 303:
                    method = "GET"
                    request_kwargs.pop("json", None)
                    request_kwargs.pop("content", None)
                continue
            data = bytearray()
            for chunk in resp.iter_bytes():          # iter_bytes decodes gzip → real size
                data.extend(chunk)
                if len(data) > max_bytes:
                    resp.close()
                    raise BlockedURLError(
                        f"response exceeded {max_bytes // (1024 * 1024)} MiB cap")
            resp.close()
            resp._content = bytes(data)               # let .text/.json()/.content work
            return resp
        raise BlockedURLError(f"too many redirects (>{MAX_REDIRECTS})")


__all__ = [
    "client", "safe_request", "validate_url", "BlockedURLError",
    "DEFAULT_USER_AGENT", "DEFAULT_TIMEOUT_SECONDS", "MAX_REDIRECTS",
    "DEFAULT_ALLOW_SCHEMES", "DEFAULT_MAX_BYTES",
]
