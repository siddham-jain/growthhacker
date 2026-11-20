from datetime import UTC, datetime, timedelta

import pytest

from app.domain.news import SourceTier
from app.domain.profile import WatchTopic
from app.ports.freshness import RawNewsItem
from app.services.news.normalize import canonicalize_url, date_precision, detect_language, registrable_domain
from app.services.news.packs import load_pack_registry
from app.services.news.query_planner import QueryPlanner
from app.services.news.tiers import SourceTierResolver
from tests.news.fixtures import (
    NEWS_SETTINGS,
    NOW,
    PACKS,
    apollo_context,
    normalizer,
    runable_context,
    runable_profile,
)


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("https://www.Example.com/a/b/?utm_source=x&b=2&a=1#top", "https://example.com/a/b?a=1&b=2"),
        ("https://m.example.com/story/amp/", "https://example.com/story"),
        ("https://amp.example.com/x?fbclid=1&gclid=2&ref=home", "https://example.com/x"),
        ("HTTPS://example.com/", "https://example.com"),
    ],
)
def test_url_canonicalisation(raw, canonical):
    assert canonicalize_url(raw) == canonical


def test_dates_precision_and_future_dates():
    assert date_precision(datetime(2026, 11, 17, tzinfo=UTC)) == "date"
    assert date_precision(datetime(2026, 11, 16, 18, 27, 56, tzinfo=UTC)) == "datetime"
    assert date_precision(None) == "none"
    future = RawNewsItem(
        url="https://reuters.com/x", title="Future", snippet="", published_at=NOW + timedelta(days=2)
    )
    item = normalizer().normalize(future, source_key="tavily_news", fetched_at=NOW).item
    assert item.published_at == NOW and "future_date" in item.flags


@pytest.mark.parametrize(
    ("url", "flag"),
    [
        ("https://www.wsj.com/tech/story", "paywalled"),
        ("https://www.prnewswire.com/news-releases/x.html", "press_release"),
        ("https://www.instagram.com/p/abc", "social"),
        ("https://arxiv.org/abs/2611.00001", "preprint"),
        ("https://github.com/anthropics/claude-code/releases", "continuously_updated"),
    ],
)
def test_flags(url, flag):
    raw = RawNewsItem(url=url, title="Some title", snippet="x" * 300, published_at=NOW)
    assert flag in normalizer().normalize(raw, source_key="tavily_news", fetched_at=NOW).item.flags


def test_youtube_and_satire_are_dropped():
    for url, reason in (
        ("https://www.youtube.com/watch?v=abc", "excluded_domain"),
        ("https://theonion.com/x", "blocked_domain"),
    ):
        raw = RawNewsItem(url=url, title="t", snippet="", published_at=NOW)
        outcome = normalizer().normalize(raw, source_key="tavily_news", fetched_at=NOW)
        assert outcome.item is None and outcome.dropped_reason == reason


def test_tier_resolution_and_domains():
    resolver = SourceTierResolver(
        PACKS.tiers, PACKS.resolve(PACKS.keys()), curated={"medplusmart.com": SourceTier.tier2_established}
    )
    assert resolver.resolve("anthropic.com") is SourceTier.tier1_primary
    assert resolver.resolve("ncdc.mohfw.gov.in") is SourceTier.tier1_authority
    assert resolver.resolve("techcrunch.com") is SourceTier.tier2_established
    assert resolver.resolve("medplusmart.com") is SourceTier.tier2_established
    assert resolver.resolve("unknown-blog.example") is SourceTier.tier3_low
    assert registrable_domain("timesofindia.indiatimes.com") == "indiatimes.com"
    assert detect_language("डेंगू के मामले बढ़े, स्वास्थ्य मंत्रालय ने जारी की एडवाइजरी") == "hi"


def test_all_packs_load():
    registry = load_pack_registry()
    assert set(registry.keys()) == {
        "ai_frontier",
        "health_in",
        "health_global",
        "dental",
        "auto_in",
        "auto_us",
        "home_services_us",
    }
    assert ("Claude Mythos 5.2", "Claude Fable 5.2") in registry.get("ai_frontier").alias_pairs


def _plan(ctx):
    return QueryPlanner(NEWS_SETTINGS).plan(ctx, PACKS.resolve(ctx.freshness.source_packs), NOW)


def test_runable_plan_covers_entities_feeds_and_domains_with_stable_hashes():
    plan = _plan(runable_context())
    by_origin = {}
    for subscription in plan.subscriptions:
        by_origin.setdefault(subscription.origin, []).append(subscription.query)
    entity_texts = [q.normalized_query for q in by_origin["entity"]]
    assert entity_texts[0] == 'anthropic OR claude "new model" OR launch OR release'
    assert by_origin["entity"][0].params["fallback_query"] == "(anthropic OR claude)"
    assert by_origin["client"][0].normalized_query == "runable"
    assert {q.params["url"] for q in by_origin["feed"]} >= {"https://openai.com/news/rss.xml"}
    assert by_origin["domain"][0].params["include_domains"] == [
        "ai.meta.com",
        "anthropic.com",
        "mistral.ai",
        "x.ai",
    ]
    assert {q.source_key for q in by_origin["research"]} == {"arxiv", "hf_trending"}
    assert all(q.cadence_minutes == 360 for q in by_origin["entity"])
    assert (
        _plan(runable_context()).subscriptions[1].query.query_hash == plan.subscriptions[1].query.query_hash
    )


def test_same_watch_entity_shares_one_query_across_clients():
    other = runable_profile().model_copy(update={"client_id": runable_profile().client_id.__class__(int=7)})
    first = {s.query.query_hash for s in _plan(runable_context()).subscriptions if s.origin == "entity"}
    second = {
        s.query.query_hash
        for s in _plan(runable_context(profile=other)).subscriptions
        if s.origin == "entity"
    }
    assert first == second


def test_budget_truncates_paid_queries_by_importance():
    from app.domain.profile import WatchEntity
    from tests.news.fixtures import meta

    profile = runable_profile()
    many = [
        WatchEntity(name=f"Model{i}", kind="model", relation_to_client="ecosystem", importance=1, meta=meta())
        for i in range(20)
    ]
    freshness = profile.freshness.model_copy(
        update={"watch_entities": [*profile.freshness.watch_entities, *many]}
    )
    plan = _plan(runable_context(profile=profile.model_copy(update={"freshness": freshness})))
    paid = [s for s in plan.subscriptions if s.costs_credits]
    assert len(paid) == NEWS_SETTINGS.envelopes["high"].max_queries_per_client
    assert plan.dropped and all(s.importance == 1 for s in plan.dropped)
    assert any(s.origin == "client" for s in paid)


def test_seasonal_window_raises_cadence_and_plans_each_language():
    plan = _plan(apollo_context())
    seasonal = [s.query for s in plan.subscriptions if s.origin == "seasonal"]
    assert {q.params["language"] for q in seasonal} == {"en", "hi"}
    assert all(q.normalized_query == '"dengue advisory" india' for q in seasonal)
    assert all(q.cadence_minutes == 360 for q in seasonal)
    entity = next(s.query for s in plan.subscriptions if s.origin == "entity")
    assert entity.cadence_minutes == 1440
    assert any(s.query.source_key == "pubmed" for s in plan.subscriptions)


def test_excluded_topics_are_never_queried():
    profile = runable_profile()
    freshness = profile.freshness.model_copy(update={"excluded_topics": ["GPT"]})
    plan = _plan(runable_context(profile=profile.model_copy(update={"freshness": freshness})))
    assert not any("gpt" in s.query.normalized_query for s in plan.subscriptions if s.origin == "entity")


def test_profiles_without_packs_get_the_packs_declared_for_their_industry_and_country():
    def keys(industry, country, own=()):
        return [pack.key for pack in PACKS.for_profile(list(own), industry, country)]

    assert keys("ai_software", "US") == ["ai_frontier"]
    assert keys("healthcare_hospital", "IN") == ["health_global", "health_in"]
    assert keys("healthcare_hospital", "US") == ["health_global"]
    assert keys("auto_dealer", "US") == ["auto_us"]
    assert keys("restaurant", "IN") == []
    assert keys("ai_software", "US", own=["dental"]) == ["dental"]


def test_watch_topics_are_planned_as_their_own_queries():
    profile = runable_context().profile
    topic = WatchTopic(query="Claude new model release", language="en")
    freshness = profile.freshness.model_copy(update={"watch_topics": [topic]})
    plan = _plan(runable_context(profile=profile.model_copy(update={"freshness": freshness})))
    topics = [s for s in plan.subscriptions if s.origin == "topic"]
    assert [s.query.normalized_query for s in topics] == ["claude new model release"]
    assert topics[0].importance == 3
