"""Trafilatura extractor — primary HTML→article-text strategy.

Trafilatura is purpose-built for news/article extraction and handles the
"strip nav/footer/boilerplate" problem well. Falls back to `raw` upstream
when trafilatura returns empty text (handled by ExtractorChain in plugin.py).
"""
from __future__ import annotations

from typing import ClassVar

import trafilatura

from arc_plugin_websearch.extractors.base import ExtractedContent


class TrafilaturaExtractor:
    name: ClassVar[str] = "trafilatura"

    def extract(self, html: str, *, url: str) -> ExtractedContent:
        if not html:
            return ExtractedContent(text="", title=None)
        # `include_comments=False` keeps Disqus / forum threads out of the body.
        # `favor_recall=True` keeps more of the article when in doubt — better
        # to over-include for an LLM than to silently lose paragraphs.
        text = trafilatura.extract(
            html,
            url=url,
            include_comments=False,
            include_tables=True,
            favor_recall=True,
        ) or ""

        # Title + metadata come from a second pass; cheap and worth it.
        meta = trafilatura.extract_metadata(html, default_url=url)
        title = meta.title if meta else None
        metadata = {}
        if meta:
            if meta.author:
                metadata["author"] = meta.author
            if meta.sitename:
                metadata["sitename"] = meta.sitename
            if meta.date:
                metadata["date"] = meta.date
            if meta.language:
                metadata["language"] = meta.language

        return ExtractedContent(text=text, title=title, metadata=metadata)
