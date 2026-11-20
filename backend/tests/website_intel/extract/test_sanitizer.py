import pytest
from selectolax.lexbor import LexborHTMLParser

from app.services.website_intel.extract.sanitizer import (
    DomSanitizer,
    block_text,
    drop_code_lines,
    inline_text,
    looks_like_code,
    normalize_text,
)

# from a real hospital home page: an inline svg icon with its own <style> inside a phone link
SVG_PHONE_LINK = (
    "<a href='tel:+919972899728'><svg viewBox='0 0 512 512'><defs><style>.st0 {fill: none;stroke: currentColor;"
    "stroke-linecap: round;stroke-width: 40px;}</style></defs><path class='st0' d='M12 2C6.48 2 2 6.48 2 12s4.48 10"
    " 10 10'/></svg>+91 99728-99728</a>"
)


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


def test_inline_text_skips_svg_style_and_script_content():
    tree = LexborHTMLParser(f"<body>{SVG_PHONE_LINK}<span>Book<script>x=1</script> <b>now</b></span></body>")
    assert inline_text(tree.css_first("a")) == "+91 99728-99728"
    assert inline_text(tree.css_first("span")) == "Book now"


@pytest.mark.parametrize(
    "text",
    [
        ".st0 {\nfill: none;\nstroke: currentColor;\nstroke-width: 40px;\n}",
        "fill:#fff;stroke-width",
        "@media (max-width: 768px) { .nav { display: none } }",
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">',
        "M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10",
        "M0 0h24v24H0z",
        "stroke-linecap: round",
        "data:image/svg+xml;base64,PHN2ZyB4bWxucz0i",
        "if(a){b=c}else{d=e}while(f){g=h}",
        "3f2a9c0d1e4b5a6f7c8d9e0a1b2c3d4e5f60",
    ],
)
def test_markup_css_and_path_data_look_like_code(text):
    assert looks_like_code(text)


@pytest.mark.parametrize(
    "text",
    [
        "Full body check-up at ₹4,100/- only",
        "24×7 emergency care across all hospitals",
        "Timings: Mon–Sat 9:00–18:00; Sunday closed",
        "Claim settlement ratio: 98%",
        "Call +91 99728-99728 to book",
        "| Dental Bonding | Small (up to 2–3mm) | Same day |",
        "We are a <u>middle-class company with middle-class values.</u>",
        "Courses in C++ & C# for beginners",
        "Stroke care with a 24-hour treatment window",
        "Room tariff: Rs. 12,500 per night; includes meals",
    ],
)
def test_prose_prices_and_tables_are_not_code(text):
    assert not looks_like_code(text)


def test_drop_code_lines_keeps_prose_lines():
    text = "Book a scan today\n.cls-1 { fill: none; }\nPackages from ₹4,100/-"
    assert drop_code_lines(text) == "Book a scan today\nPackages from ₹4,100/-"
