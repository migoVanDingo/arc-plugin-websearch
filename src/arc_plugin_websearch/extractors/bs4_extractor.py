"""BeautifulSoup text extractor — same parser extract_html uses, re-exposed."""
from __future__ import annotations

from typing import ClassVar

from bs4 import BeautifulSoup

from arc_plugin_websearch.extractors.base import ExtractedContent


class BS4Extractor:
    name: ClassVar[str] = "bs4-text"

    def extract(self, html: str, *, url: str) -> ExtractedContent:
        if not html:
            return ExtractedContent(text="", title=None)
        soup = BeautifulSoup(html, "html.parser")
        # Strip noise before pulling text — script/style blocks contain
        # code, not content. nav/footer/aside are usually boilerplate.
        for sel in ("script", "style", "noscript"):
            for tag in soup(sel):
                tag.decompose()

        title_tag = soup.find("title")
        title = title_tag.get_text(strip=True) if title_tag else None

        # get_text with a separator gives us roughly the visible text flow.
        text = soup.get_text(separator=" ", strip=True)
        # Collapse run-on whitespace from the joins.
        text = " ".join(text.split())
        return ExtractedContent(text=text, title=title)
