import urllib.robotparser

import pytest

from app.services.website_intel.crawl.robots import (
    MAX_ROBOTS_BYTES,
    RobotsStatus,
    effective_delay,
    robots_from_response,
)

TOKEN = "GrowthHackerBot"
APOLLO = "https://www.apollohospitals.com"


@pytest.fixture
def apollo_robots(fixture_path) -> str:
    return fixture_path("crawl_apollo_robots.txt").read_text(encoding="utf-8")


def test_apollo_rules_use_longest_match_with_wildcards(apollo_robots: str) -> None:
    rules = robots_from_response(200, apollo_robots, token=TOKEN)
    assert rules.status is RobotsStatus.parsed
    assert not rules.can_fetch(f"{APOLLO}/search/?q=cancer")
    assert not rules.can_fetch(f"{APOLLO}/hyderabad/user/42")
    assert not rules.can_fetch(f"{APOLLO}/promotions/offer")
    assert not rules.can_fetch(f"{APOLLO}/lets-talk-health/some-post?page=2")
    assert rules.can_fetch(f"{APOLLO}/departments/cancer")
    assert rules.explicitly_disallows(f"{APOLLO}/search/?q=cancer")
    assert len(rules.sitemaps) == 3
    assert rules.crawl_delay is None


def test_stdlib_parser_diverges_on_apollo(apollo_robots: str) -> None:
    stdlib = urllib.robotparser.RobotFileParser()
    stdlib.parse(apollo_robots.splitlines())
    rules = robots_from_response(200, apollo_robots, token=TOKEN)
    for path in ("/search/?q=cancer", "/hyderabad/user/42", "/promotions/offer"):
        assert stdlib.can_fetch(TOKEN, f"{APOLLO}{path}")
        assert not rules.can_fetch(f"{APOLLO}{path}")


def test_named_group_is_case_insensitive_and_falls_back_to_star() -> None:
    body = "User-agent: growthhackerbot\nDisallow: /private\n\nUser-agent: *\nDisallow: /\n"
    rules = robots_from_response(200, body, token=TOKEN)
    assert rules.can_fetch("https://a.com/public")
    assert not rules.can_fetch("https://a.com/private/x")
    star_only = robots_from_response(200, "User-agent: *\nDisallow: /tmp/\nCrawl-delay: 4\n", token=TOKEN)
    assert not star_only.can_fetch("https://a.com/tmp/1")
    assert star_only.crawl_delay == 4.0


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (404, RobotsStatus.allow_all),
        (410, RobotsStatus.allow_all),
        (401, RobotsStatus.disallow_all),
        (403, RobotsStatus.disallow_all),
        (429, RobotsStatus.disallow_all),
        (500, RobotsStatus.disallow_all),
        (503, RobotsStatus.disallow_all),
        (None, RobotsStatus.disallow_all),
    ],
)
def test_status_handling(status: int | None, expected: RobotsStatus) -> None:
    rules = robots_from_response(status, "User-agent: *\nDisallow: /", token=TOKEN)
    assert rules.status is expected
    assert rules.can_fetch("https://a.com/x") is (expected is RobotsStatus.allow_all)
    assert not rules.explicitly_disallows("https://a.com/x")


def test_body_is_capped_at_512_kib() -> None:
    filler = "# " + "x" * 100 + "\n"
    body = filler * (MAX_ROBOTS_BYTES // len(filler) + 10) + "User-agent: *\nDisallow: /\n"
    rules = robots_from_response(200, body.encode(), token=TOKEN)
    assert rules.can_fetch("https://a.com/x")
    assert rules.body is not None and len(rules.body.encode()) <= MAX_ROBOTS_BYTES


def test_garbage_body_allows_all() -> None:
    rules = robots_from_response(200, b"\x00\xff<html>not robots</html>", token=TOKEN)
    assert rules.can_fetch("https://a.com/anything")


def test_effective_delay_clamps() -> None:
    assert effective_delay(None, 1.0) == 1.0
    assert effective_delay(0.2, 1.0) == 1.0
    assert effective_delay(5, 1.0) == 5
    assert effective_delay(60, 1.0) == 30.0
