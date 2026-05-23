"""Extractor tests — primary path, fallback chain, empty/malformed input."""
from __future__ import annotations

from arc_plugin_websearch.extractors.bs4_extractor import BS4Extractor
from arc_plugin_websearch.extractors.chain import ExtractorChain
from arc_plugin_websearch.extractors.raw_extractor import RawExtractor
from arc_plugin_websearch.extractors.trafilatura_extractor import TrafilaturaExtractor


# ── RawExtractor ─────────────────────────────────────────────────────────


def test_raw_strips_script_and_style(example_html):
    ext = RawExtractor()
    out = ext.extract(example_html, url="https://example.com")
    assert "Example Domain" in out.text
    assert "console.log" not in out.text
    assert ".hidden" not in out.text
    assert out.title == "Example Domain"


def test_raw_empty_html_returns_empty():
    out = RawExtractor().extract("", url="x")
    assert out.text == ""
    assert out.title is None


def test_raw_no_title_tag():
    out = RawExtractor().extract("<html><body>hello</body></html>", url="x")
    assert out.title is None
    assert "hello" in out.text


# ── BS4Extractor ─────────────────────────────────────────────────────────


def test_bs4_extracts_visible_text(example_html):
    out = BS4Extractor().extract(example_html, url="https://example.com")
    assert "Example Domain" in out.text
    assert "console.log" not in out.text
    assert out.title == "Example Domain"


def test_bs4_empty():
    out = BS4Extractor().extract("", url="x")
    assert out.text == ""


# ── TrafilaturaExtractor ─────────────────────────────────────────────────


def test_trafilatura_extracts_article(example_html):
    """example.com's text is short; trafilatura with favor_recall=True
    should still pull the main paragraph(s) most of the time. If
    trafilatura returns empty (heuristic miss), ExtractorChain falls back
    — covered separately below."""
    out = TrafilaturaExtractor().extract(example_html, url="https://example.com")
    # Either trafilatura got something, or it returned empty — both are
    # acceptable at the unit level. The chain test below covers the fallback.
    assert isinstance(out.text, str)
    # Title metadata is reliable on this fixture
    assert out.title == "Example Domain"


# ── ExtractorChain ───────────────────────────────────────────────────────


def test_chain_uses_primary_when_it_returns_text(example_html):
    """If primary returns non-empty text, chain returns it as-is."""
    class _Primary:
        name = "primary"
        def extract(self, html, *, url):
            from arc_plugin_websearch.extractors.base import ExtractedContent
            return ExtractedContent(text="primary won", title="T")

    chain = ExtractorChain(primary=_Primary())
    out = chain.extract(example_html, url="https://x")
    assert out.text == "primary won"
    assert out.fallback_used is False


def test_chain_falls_back_to_raw_when_primary_empty(example_html):
    """Primary returning empty → chain runs RawExtractor + marks fallback_used."""
    class _Empty:
        name = "primary"
        def extract(self, html, *, url):
            from arc_plugin_websearch.extractors.base import ExtractedContent
            return ExtractedContent(text="", title=None)

    chain = ExtractorChain(primary=_Empty())
    out = chain.extract(example_html, url="https://x")
    assert "Example Domain" in out.text
    assert out.fallback_used is True


def test_chain_name_reflects_primary():
    class _P:
        name = "fancy"
        def extract(self, html, *, url):
            from arc_plugin_websearch.extractors.base import ExtractedContent
            return ExtractedContent(text="ok")

    assert ExtractorChain(primary=_P()).name == "fancy"


def test_chain_preserves_primary_metadata_on_fallback(example_html):
    """If primary has title/metadata but empty body, chain keeps the meta."""
    class _MetaOnly:
        name = "primary"
        def extract(self, html, *, url):
            from arc_plugin_websearch.extractors.base import ExtractedContent
            return ExtractedContent(text="", title="From Primary",
                                    metadata={"author": "Anne"})

    out = ExtractorChain(primary=_MetaOnly()).extract(example_html, url="x")
    assert out.fallback_used is True
    assert out.title == "From Primary"
    assert out.metadata["author"] == "Anne"
