import pytest

from app.services.website_intel.crawl.traps import TrapDetector, TrapRules


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("https://a.com/services/dental-implants", None),
        ("https://a.com/blog?page=2", None),
        ("https://a.com/blog?page=4", "pagination"),
        ("https://a.com/blog/page/9", "pagination"),
        ("https://a.com/blog/page/2", None),
        ("https://a.com/x?a=1&b=2&c=3&d=4", "too_many_params"),
        ("https://a.com/x?PHPSESSID=abc", "trap_param:phpsessid"),
        ("https://a.com/shop?sort=price", "trap_param:sort"),
        ("https://a.com/events?month=11", "trap_param:month"),
        ("https://a.com/post?replytocom=12", "trap_param:replytocom"),
        ("https://a.com/?s=implants", "excluded_page:search"),
        ("https://a.com/a/b/a/b/a/b", "repeated_segment"),
        ("https://a.com/1/2/3/4/5/6/7/8/9", "too_deep"),
        ("https://a.com/" + "x" * 300, "too_long"),
        ("https://a.com/my-account/orders", "excluded_page:my-account"),
        ("https://a.com/cart", "excluded_page:cart"),
        ("https://a.com/wp-admin/edit.php", "excluded_page:wp-admin"),
        ("https://a.com/search/", "excluded_page:search"),
        ("https://a.com/images/team.jpg", "non_html:.jpg"),
        ("https://a.com/data/feed.json", "non_html:.json"),
        ("https://a.com/brochure.docx", "non_html:.docx"),
        ("https://a.com/rate-card.pdf", None),
    ],
)
def test_trap_rules(url: str, reason: str | None) -> None:
    assert TrapDetector().reason(url) == reason


def test_calendar_values_capped_per_template() -> None:
    detector = TrapDetector()
    reasons = [detector.reason(f"https://a.com/archive/2025/{month}/") for month in range(1, 6)]
    assert reasons == [None, None, None, "calendar", "calendar"]
    assert detector.reason("https://a.com/news/2025/01/") is None


def test_param_pattern_quarantine() -> None:
    detector = TrapDetector(TrapRules(quarantine_threshold=5))
    reasons = [detector.reason(f"https://a.com/product?id={index}") for index in range(8)]
    assert reasons[:5] == [None] * 5
    assert set(reasons[5:]) == {"quarantined_params"}
    assert detector.reason("https://a.com/product?id=1") == "quarantined_params"
    assert detector.reason("https://a.com/other?id=1") is None
