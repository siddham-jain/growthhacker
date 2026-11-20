from selectolax.lexbor import LexborHTMLParser

from app.services.website_intel.extract.main_text import MainTextExtractor
from app.services.website_intel.extract.sanitizer import DomSanitizer
from tests.website_intel.extract.conftest import fixture_html


def _extract(html: str) -> str:
    tree = LexborHTMLParser(html)
    DomSanitizer().strip(tree)
    return MainTextExtractor().extract(tree, url="https://example.com/")


def test_hub_pages_fall_back_to_body_text_without_chrome():
    items = "".join(
        f"<div class='card'><h3>Department {i}</h3><p>Care unit {i} with experts.</p></div>"
        for i in range(60)
    )
    html = f"<html><body><nav><a href='/'>Menu link</a></nav><main>{items}</main><footer>Footer note</footer></body></html>"
    text = _extract(html)
    assert "Department 59" in text
    assert "Menu link" not in text and "Footer note" not in text


def test_apollo_home_keeps_most_visible_text():
    text = _extract(fixture_html("apollo_home.html.gz"))
    assert len(text) > 20_000
    assert "Clinical Excellence at Apollo Hospitals" in text


def test_article_pages_use_trafilatura():
    paragraphs = "".join(
        f"<p>Paragraph {i} explains how preventive dental care keeps families healthy over many years.</p>"
        for i in range(12)
    )
    html = f"<html><body><article><h1>Preventive care</h1>{paragraphs}</article></body></html>"
    text = _extract(html)
    assert text.count("Paragraph") == 12
