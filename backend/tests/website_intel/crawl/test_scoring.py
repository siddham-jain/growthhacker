from datetime import UTC, datetime, timedelta

import pytest

from app.services.website_intel.crawl.scoring import DeterministicSelector, PriorityScorer
from app.services.website_intel.crawl.templates import TemplateClusterer
from app.services.website_intel.models import PageType, UrlCandidate

NOW = datetime(2026, 11, 20, tzinfo=UTC)


def _candidate(path: str, page_type: PageType, **kwargs) -> UrlCandidate:
    return UrlCandidate(url=f"https://a.com{path}", source="sitemap", page_type=page_type, **kwargs)


def test_score_formula_exact() -> None:
    candidate = _candidate(
        "/pricing",
        PageType.pricing,
        nav_depth=1,
        linked_from_home=True,
        inlinks=9,
        depth=1,
        lastmod=NOW,
        sitemap_priority=1.0,
    )
    expected = 0.35 * 0.95 + 0.20 + 0.12 + 0.10 + 0.08 * 0.5 + 0.0 + 0.04 + 0.03
    assert PriorityScorer().score(candidate, max_inlinks=9, now=NOW) == pytest.approx(expected)


def test_unknown_signals_and_penalties() -> None:
    scorer = PriorityScorer()
    base = _candidate("/x", PageType.other)
    assert scorer.score(base, max_inlinks=0, now=NOW) == pytest.approx(
        0.35 * 0.25 + 0.08 + 0.04 * 0.5 + 0.03 * 0.5
    )
    old_post = _candidate("/blog/old", PageType.blog_post, lastmod=NOW - timedelta(days=900))
    new_post = _candidate("/blog/new", PageType.blog_post, lastmod=NOW)
    assert (
        scorer.score(new_post, max_inlinks=0, now=NOW) - scorer.score(old_post, max_inlinks=0, now=NOW) > 0.4
    )
    alternate = _candidate("/ar/about", PageType.about, language_alternate=True)
    about = _candidate("/about", PageType.about)
    assert scorer.score(about, max_inlinks=0, now=NOW) - scorer.score(
        alternate, max_inlinks=0, now=NOW
    ) == pytest.approx(0.3)
    pdf = _candidate("/rates.pdf", PageType.pricing)
    assert scorer.score(pdf, max_inlinks=0, now=NOW) < scorer.score(
        _candidate("/rates", PageType.pricing), max_inlinks=0, now=NOW
    )
    saturated = _candidate("/s", PageType.other, extra={"template_saturated": True})
    assert scorer.score(base, max_inlinks=0, now=NOW) - scorer.score(
        saturated, max_inlinks=0, now=NOW
    ) == pytest.approx(0.5)


def test_ordering_prefers_business_pages() -> None:
    scorer = PriorityScorer()
    pages = [
        _candidate("/careers", PageType.careers, nav_depth=2),
        _candidate("/services", PageType.offering_hub, nav_depth=1, linked_from_home=True),
        _candidate("/blog/post", PageType.blog_post, depth=2),
        _candidate("/about", PageType.about, nav_depth=1, linked_from_home=True),
    ]
    ranked = sorted(pages, key=lambda c: scorer.score(c, max_inlinks=1, now=NOW), reverse=True)
    assert [c.url.rsplit("/", 1)[-1] for c in ranked] == ["services", "about", "careers", "post"]


def test_selector_mandatory_set_and_caps() -> None:
    cities = ["hyderabad", "chennai", "delhi", "mumbai", "pune", "kolkata", "bangalore", "lucknow"]
    services = [f"service-{index}" for index in range(8)]
    grid = [
        _candidate(f"/{city}/procedures/{service}", PageType.offering_detail, score=0.9)
        for city in cities
        for service in services
    ]
    hubs = [
        _candidate(f"/departments/d{index}", PageType.offering_hub, nav_depth=1, score=0.5)
        for index in range(10)
    ]
    pages = [
        _candidate("/", PageType.home, score=1.0),
        _candidate("/about", PageType.about, score=0.4),
        _candidate("/contact", PageType.contact, score=0.3),
        *[_candidate(f"/faq{index}", PageType.faq, score=0.2) for index in range(7)],
        _candidate("/locations", PageType.locations_hub, score=0.1),
        _candidate("/careers", PageType.careers, score=0.05),
        *hubs,
        *grid,
    ]
    clustering = TemplateClusterer().cluster([page.url for page in pages])
    selection = DeterministicSelector().select(pages, budget=30, per_template_cap=3, clustering=clustering)
    mandatory = [page.url.removeprefix("https://a.com") for page in selection.mandatory]
    assert mandatory[:3] == ["/", "/about", "/contact"]
    assert sum(path.startswith("/faq") for path in mandatory) == 5
    assert sum(path.startswith("/departments/") for path in mandatory) == 8
    assert "/locations" in mandatory
    grid_picks = [page for page in selection.all if "/procedures/" in page.url]
    assert len(grid_picks) == 3
    assert len(selection.all) <= 30
    assert len({page.url for page in selection.all}) == len(selection.all)
