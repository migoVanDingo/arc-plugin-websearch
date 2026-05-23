"""Raw extractor — strip tags, collapse whitespace. The fallback of last resort."""
from __future__ import annotations

import re
from typing import ClassVar

from arc_plugin_websearch.extractors.base import ExtractedContent


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


class RawExtractor:
    name: ClassVar[str] = "raw"

    def extract(self, html: str, *, url: str) -> ExtractedContent:
        if not html:
            return ExtractedContent(text="", title=None)
        # Drop script / style blocks before tag stripping — their content
        # would otherwise pollute the output.
        cleaned = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html,
                         flags=re.DOTALL | re.IGNORECASE)
        text = _TAG_RE.sub(" ", cleaned)
        text = _WS_RE.sub(" ", text).strip()
        title = _extract_title(html)
        return ExtractedContent(text=text, title=title)


def _extract_title(html: str) -> str | None:
    """Best-effort <title> capture without dragging bs4 in for the raw path."""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
    if not m:
        return None
    return _WS_RE.sub(" ", m.group(1)).strip() or None
