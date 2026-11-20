from selectolax.lexbor import LexborHTMLParser

from app.services.website_intel.extract.sanitizer import DomSanitizer, block_text, normalize_text


def _clean(html: str) -> str:
    tree = LexborHTMLParser(html)
    DomSanitizer().strip(tree)
    return block_text(tree.body)


def test_hidden_nodes_are_removed():
    text = _clean(
        "<body><p>visible</p><p hidden>a</p><p aria-hidden='true'>b</p><p style='display: none'>c</p>"
        "<p style='visibility:hidden'>d</p><p style='opacity:0;'>e</p><p style='font-size:0px'>f</p>"
        "<span class='sr-only'>g</span><span class='visually-hidden'>h</span><div class='d-none'>i</div>"
        "<div class='hidden'>j</div></body>"
    )
    assert text == "visible"


def test_responsive_hidden_classes_are_kept():
    text = _clean(
        "<body><div class='hidden lg:flex'>desktop menu</div><div class='d-none d-md-block'>wide</div></body>"
    )
    assert text.split("\n") == ["desktop menu", "wide"]


def test_scripts_comments_forms_and_consent_roots_are_dropped():
    text = _clean(
        "<body><script>alert(1)</script><style>p{}</style><!-- secret --><form><input value='x'>form text</form>"
        "<div id='onetrust-banner-sdk'>cookies</div><div id='CybotCookiebotDialog'>more cookies</div>"
        "<div class='site-cookie-banner'>banner</div><p>content</p></body>"
    )
    assert text == "content"


def test_block_structure_and_heading_markers():
    text = _clean(
        "<body><h2>Title</h2><p>Hello <b>bold</b> world</p><ul><li>one</li><li>two</li></ul></body>"
    )
    assert text.split("\n") == ["## Title", "Hello bold world", "one", "two"]


def test_normalize_text_strips_zero_width_and_bidi_and_applies_nfkc():
    raw = "ﬁne​ te‮xt⁦ ﻿here  now\n\n\nnext"
    assert normalize_text(raw) == "fine text here now\nnext"
