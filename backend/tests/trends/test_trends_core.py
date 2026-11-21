from datetime import timedelta

from app.adapters.youtube.autocomplete import parse_suggestions
from app.domain.topics import TopicKind
from app.domain.trends import KeywordSource, TrendScan
from app.domain.youtube import QuotaBucket, QuotaPriority
from app.ports.quota import QuotaExhausted
from app.ports.youtube import RequestContext
from app.services.trends.candidates import CandidateBuilder, WebKeyword, brand_terms, searchable_seeds
from app.services.trends.heat import score
from app.services.trends.ideas import DraftIdea, accept_ideas
from app.services.trends.measure import KeywordMeasurer, grounded
from tests.news.fixtures import FakeGateway, video_record
from tests.refresh.fixtures import topic_ref
from tests.trends.fixtures import (
    CHANNELS,
    CLIENT_ID,
    HEART,
    KNEE,
    NOW,
    candidate,
    keyword_trend,
    trend_video,
)

JSONP = (
    'window.google.ac.h(["knee replacement",[["knee replacement surgery",0,[512]],'
    '["knee replacement cost in india",0,[512,433]],["knee replacement surgery",0,[512]]],{"k":1}])'
)


def test_parse_suggestions_reads_jsonp_and_dedupes():
    assert parse_suggestions(JSONP) == ["knee replacement surgery", "knee replacement cost in india"]
    assert parse_suggestions("<html>blocked</html>") == []


def test_seeds_prefer_offerings_and_skip_short_words_and_brands():
    refs = [topic_ref("ENT"), HEART, KNEE, topic_ref("Apollo ProHealth checks", TopicKind.offering)]
    brands = brand_terms("Apollo Hospitals", [])
    assert brands == ["Apollo Hospitals", "Apollo"]
    seeds = searchable_seeds(refs, 5, brand_terms=brands)
    assert [ref.label for ref in seeds] == ["knee replacement", "heart attack"]


def test_seeds_rotate_by_day_across_every_topic():
    refs = [topic_ref(f"offering number {i}", TopicKind.offering) for i in range(5)]
    seen = {ref.label for day in range(3) for ref in searchable_seeds(refs, 2, day=day)}
    assert len(seen) == 5
    assert searchable_seeds(refs, 2, day=0) != searchable_seeds(refs, 2, day=1)


def test_builder_merges_sources_and_drops_unanchored_or_unknown():
    builder = CandidateBuilder([KNEE, HEART])
    builder.add_seed(KNEE)
    builder.add_suggestions(KNEE, ["knee replacement", "knee replacement cost", "nfl knee news"], limit=5)
    builder.add_web(
        [
            WebKeyword("Knee Replacement Cost", KNEE.id, "price cap news", ["https://example.org/a"]),
            WebKeyword("lottery results", "tr_000000000000", "noise"),
        ]
    )
    ranked = builder.ranked(10)
    keywords = [c.keyword for c in ranked]
    assert keywords == ["knee replacement cost", "knee replacement"]
    assert ranked[0].sources == [KeywordSource.web, KeywordSource.autocomplete]
    assert ranked[0].web_urls == ["https://example.org/a"]


def test_builder_spreads_budget_across_topics():
    builder = CandidateBuilder([KNEE, HEART])
    builder.add_suggestions(KNEE, ["knee replacement a", "knee replacement b", "knee replacement c"], limit=5)
    builder.add_suggestions(HEART, ["heart attack signs"], limit=5)
    assert {c.keyword for c in builder.ranked(2)} == {"knee replacement a", "heart attack signs"}


def test_builder_gives_news_only_keywords_a_third_of_the_searches():
    builder = CandidateBuilder([KNEE, HEART])
    builder.add_seed(KNEE)
    builder.add_seed(HEART)
    builder.add_web(
        [WebKeyword(f"knee replacement news {i}", KNEE.id, "story") for i in range(4)]
        + [WebKeyword(f"heart attack news {i}", HEART.id, "story") for i in range(4)]
    )
    ranked = builder.ranked(6)
    news_only = [c for c in ranked if c.sources == [KeywordSource.web]]
    assert len(ranked) == 4 and len(news_only) == 2
    assert {"knee replacement", "heart attack"} <= {c.keyword for c in ranked}


class QuotaGateway(FakeGateway):
    async def search(self, query, page_token, ctx):
        raise QuotaExhausted(QuotaBucket.search, NOW, "search_pool")


CTX = RequestContext(client_id=CLIENT_ID, run_id=None, priority=QuotaPriority.interactive)


async def test_measure_keeps_grounded_recent_videos():
    videos = [
        video_record(
            "kneevideo01", CHANNELS[0], "Knee replacement recovery", NOW - timedelta(days=2), 20_000
        ),
        video_record("kneevideo02", CHANNELS[1], "घुटना knee replacement", NOW - timedelta(days=1), 4_000),
        video_record("kneevideo03", CHANNELS[2], "NFL star out for season", NOW - timedelta(days=1), 90_000),
        video_record("kneevideo04", CHANNELS[3], "Knee replacement old", NOW - timedelta(days=40), 90_000),
        video_record(
            "kneevideo05",
            CHANNELS[4],
            "Funny cat",
            NOW - timedelta(days=1),
            99_000,
            description="knee replacement",
        ),
    ]
    gateway = FakeGateway(videos)
    measurer = KeywordMeasurer(gateway=gateway, window_days=14, videos_kept=5)
    trend = await measurer.measure(
        candidate("knee replacement"),
        ref=KNEE,
        ctx=CTX,
        region="IN",
        language="en",
        competitors={CHANNELS[1]},
        now=NOW,
    )
    assert trend.status == "checked"
    assert [v.video_id for v in trend.videos] == ["kneevideo01", "kneevideo02"]
    assert trend.top_views_per_day == 10_000 and trend.median_views_per_day == 7_000
    assert trend.competitor_videos == 1 and trend.channels == 2
    query = gateway.searches[0]
    assert query.order == "viewCount" and query.region_code == "IN"
    assert query.published_after == (NOW - timedelta(days=14)).date()


def test_long_service_keywords_ground_on_all_but_one_word():
    service = topic_ref("Influencer content management", TopicKind.offering)
    keyword = candidate("influencer content management", ref=service)

    def matches(title: str, phrase=keyword) -> bool:
        record = video_record("svc00000001", CHANNELS[0], title, NOW - timedelta(days=1), 1_000)
        return grounded(record, phrase, service)

    assert matches("Why Authentic Influencer Content Drives Brand Trust")
    assert not matches("Stop Trying to Become an Influencer")
    # two-word keywords still need both words
    assert not matches("Knee pain at night", candidate("knee replacement"))


async def test_measure_without_quota_is_unchecked():
    measurer = KeywordMeasurer(gateway=QuotaGateway(), window_days=14, videos_kept=5)
    trend = await measurer.measure(
        candidate("knee replacement"),
        ref=KNEE,
        ctx=CTX,
        region=None,
        language=None,
        competitors=set(),
        now=NOW,
    )
    assert trend.status == "unchecked_quota" and trend.heat == "quiet" and not trend.videos


def test_heat_is_relative_to_the_clients_keywords_and_uses_momentum():
    earlier = keyword_trend("b", 100.0).model_copy(update={"checked_at": NOW - timedelta(days=2)})
    previous = TrendScan(as_of=NOW, window_days=14, keywords=[earlier])
    scored = score(
        [
            keyword_trend("a", 3_000.0),
            keyword_trend("b", 400.0),
            keyword_trend("c", 500.0),
            keyword_trend("d", 50.0, videos=1),
        ],
        previous,
        hot_ratio=2.0,
        min_videos=3,
        min_channels=2,
    )
    heat = {t.candidate.keyword: t.heat for t in scored}
    assert heat == {"a": "hot", "b": "hot", "c": "warm", "d": "quiet"}
    assert next(t for t in scored if t.candidate.keyword == "b").momentum == 4.0
    assert scored[0].candidate.keyword == "a"


def test_accept_ideas_drops_copies_invented_numbers_and_foreign_references():
    videos = [
        trend_video(1, "Knee replacement recovery tips", 9_000),
        trend_video(2, "Robotic knee surgery", 4_000),
    ]
    trend = keyword_trend("knee replacement", 6_500.0, videos=2, channels=2).model_copy(
        update={"videos": videos}
    )

    def draft(title, refs=("trendvid001", "trendvid002"), angle="Walk viewers through day one at home"):
        return DraftIdea(
            keyword="Knee Replacement",
            title=title,
            angle=angle,
            fit_to_client="Your orthopaedics team does this surgery",
            format="long",
            reference_video_ids=list(refs),
            buyer_question="how long is knee replacement recovery?",
        )

    ideas = accept_ideas(
        [
            draft("What the first week after knee replacement really feels like"),
            draft("Knee replacement recovery tips!"),
            draft("Robot or surgeon: who does your knee?", angle="Explain 3 robot types"),
            draft("Knee pain myths", refs=("notourvideo",)),
            draft("Recovery at home after a new knee", refs=("trendvid001",)),
        ],
        [trend],
        buyer_questions=["How long is knee replacement recovery?"],
        own_titles=[],
        min_references=2,
        per_keyword=3,
    )
    assert [idea.title for idea in ideas] == ["What the first week after knee replacement really feels like"]
    idea = ideas[0]
    assert idea.buyer_question == "How long is knee replacement recovery?"
    assert idea.why[0].startswith("2 videos on “knee replacement” in the last 14 days")
    assert idea.reference_video_ids == ["trendvid001", "trendvid002"]
