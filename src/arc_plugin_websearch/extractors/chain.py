"""Extractor chain — try the primary; fall back to `raw` if it returned empty.

When the chain falls back, the returned ExtractedContent has
`fallback_used=True` so the tool can surface it in the observability event
without polluting the model-visible output.
"""
from __future__ import annotations

from typing import ClassVar

from arc_plugin_websearch.extractors.base import ExtractedContent, Extractor
from arc_plugin_websearch.extractors.raw_extractor import RawExtractor


class ExtractorChain:
    """Primary extractor → raw fallback. Marks fallback_used in metadata.

    `name` reports the primary extractor's name so observability events
    carry the user's *intended* extractor; whether fallback fired is a
    separate field on the ExtractedContent.
    """

    def __init__(self, primary: Extractor, *, fallback: Extractor | None = None) -> None:
        self._primary = primary
        self._fallback = fallback or RawExtractor()

    @property
    def name(self) -> str:
        return self._primary.name

    def extract(self, html: str, *, url: str) -> ExtractedContent:
        primary = self._primary.extract(html, url=url)
        if primary.text and primary.text.strip():
            return primary

        # Empty / whitespace-only → fall back. Preserve title/metadata from
        # primary if any, since trafilatura's metadata can succeed even when
        # body extraction fails on a weird layout.
        fb = self._fallback.extract(html, url=url)
        merged_title = primary.title or fb.title
        merged_meta = {**fb.metadata, **primary.metadata}
        return ExtractedContent(
            text=fb.text,
            title=merged_title,
            metadata=merged_meta,
            fallback_used=True,
        )
