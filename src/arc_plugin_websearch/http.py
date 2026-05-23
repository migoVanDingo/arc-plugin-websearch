"""Shared httpx client used by every backend, extractor, and tool.

Centralizing this gives us one place to set timeout / UA / redirect policy
and one seam to hang a future response-caching layer off.

We expose a module-level `client()` factory rather than a long-lived global:
constructing an `httpx.Client` is cheap and per-call construction sidesteps
shutdown-ordering issues during arc's tear-down (the TUI exits, hooks fire,
and any open keep-alive sockets in a singleton would dangle).
"""
from __future__ import annotations

import httpx

DEFAULT_USER_AGENT = "arc/2 (+https://github.com/anthropics/arc)"
DEFAULT_TIMEOUT_SECONDS = 30
MAX_REDIRECTS = 5


def client(
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    user_agent: str = DEFAULT_USER_AGENT,
) -> httpx.Client:
    """Return a configured short-lived httpx.Client.

    Caller owns the lifecycle — use as a context manager:

        with http.client(timeout_seconds=15) as c:
            r = c.get(url)
    """
    return httpx.Client(
        timeout=httpx.Timeout(timeout_seconds),
        headers={"User-Agent": user_agent},
        follow_redirects=True,
        max_redirects=MAX_REDIRECTS,
        # http/2 has nicer multiplexing but adds the `h2` dep; default to http/1.1.
    )


__all__ = ["client", "DEFAULT_USER_AGENT", "DEFAULT_TIMEOUT_SECONDS", "MAX_REDIRECTS"]
