from datetime import timedelta

from app.core.config import TrendSettings
from app.domain.trends import KeywordSource, TrendScan
from app.domain.youtube import QuotaPriority
from app.ports.search import WebSearchHit, WebSearchResponse
from app.ports.youtube import RequestContext
from app.services.trends.candidates import WebKeyword
from app.services.trends.ideas import DraftIdea
from app.services.trends.service import TrendRequest, TrendScanService
from app.services.trends.writer import ClientBrief
from tests.news.fixtures import FakeGateway, video_record
from tests.trends.fixtures import CHANNELS, CLIENT_ID, HEART, KNEE, NOW, keyword_trend

BRIEF = ClientBrief(client_id=CLIENT_ID, run_id=None, text="business: Apollo", language="en")


class FakeSuggester:
    async def suggest(self, seed, *, region, language):
        return {"knee replacement": ["knee replacement cost"], "heart attack": ["heart attack signs"]}.get(
            seed, []
        )


class FakeWeb:
    def __init__(self) -> None:
        self.queries = []

    async def search(self, query):
        self.queries.append(query)
        hit = WebSearchHit(
            url="https://news.example/robot", title="Robotic knee surgery now covered", snippet=""
        )
        return WebSearchResponse(query=query, hits=[hit], credits_used=1.0, from_cache=False, fetched_at=NOW)


class FakeWriter:
    def __init__(self) -> None:
        self.calls = 0
        self.idea_keywords: list[str] = []

    async def web_keywords(self, brief, topics, hits, limit, now):
        self.calls += 1
        return [WebKeyword("robotic knee replacement", KNEE.id, "insurers now cover it", [hits[0].url])]

    async def ideas(self, brief, trends, per_keyword, now):
        self.calls += 1
        self.idea_keywords = [t.candidate.keyword for t in trends]
        trend = trends[0]
        return [
            DraftIdea(
                keyword=trend.candidate.keyword,
                title="Is robotic surgery worth it for your knee?",
                angle="Compare recovery with and without the robot",
                fit_to_client="Your hospitals run robotic orthopaedic theatres",
                format="long",
                reference_video_ids=[v.video_id for v in trend.videos[:2]],
            )
        ]


def gateway() -> FakeGateway:
    titles = ["Robotic knee replacement explained", "Knee replacement cost guide", "Knee replacement day one"]
    return FakeGateway(
        [
            video_record(
                f"svcvideo{i:03d}",
                CHANNELS[i % 4],
                titles[i % 3],
                NOW - timedelta(days=1 + i % 5),
                5_000 * (i + 1),
            )
            for i in range(9)
        ]
    )


def request(previous=None, budget=5) -> TrendRequest:
    return TrendRequest(
        brief=BRIEF,
        refs=[KNEE, HEART],
        youtube=RequestContext(client_id=CLIENT_ID, run_id=None, priority=QuotaPriority.interactive),
        brand_terms=["Apollo Hospitals", "Apollo"],
        region="IN",
        language="en",
        competitors={CHANNELS[0]},
        window_days=14,
        keyword_budget=budget,
        previous=previous,
        now=NOW,
    )


async def test_scan_merges_sources_measures_and_writes_grounded_ideas():
    web, writer, yt = FakeWeb(), FakeWriter(), gateway()
    service = TrendScanService(
        settings=TrendSettings(), gateway=yt, suggester=FakeSuggester(), web=web, writer=writer
    )
    scan = await service.scan(request())
    keywords = {t.candidate.keyword: t for t in scan.keywords}
    assert "robotic knee replacement" in keywords
    assert keywords["robotic knee replacement"].candidate.sources == [KeywordSource.web]
    assert keywords["robotic knee replacement"].candidate.web_urls == ["https://news.example/robot"]
    assert len(yt.searches) == len(scan.keywords) <= 5
    assert scan.tavily_credits == 1.0 and scan.llm_calls == 2
    assert len(scan.ideas) == 1
    idea = scan.ideas[0]
    assert idea.reference_video_ids and idea.why[0].startswith(
        f"{keywords[idea.keyword].recent_videos} videos"
    )
    # heart attack has no matching uploads, so it never reaches the idea writer
    assert all("heart" not in keyword for keyword in writer.idea_keywords)


async def test_scan_carries_recent_checks_forward_and_drops_stale_ones():
    recent = keyword_trend("knee replacement day one", 900.0).model_copy(
        update={"checked_at": NOW - timedelta(days=2)}
    )
    stale = keyword_trend("knee replacement myths", 900.0).model_copy(
        update={"checked_at": NOW - timedelta(days=20)}
    )
    previous = TrendScan(as_of=NOW - timedelta(days=2), window_days=14, keywords=[recent, stale])
    service = TrendScanService(
        settings=TrendSettings(), gateway=gateway(), suggester=None, web=None, writer=None
    )
    scan = await service.scan(request(previous=previous, budget=1))
    keywords = [t.candidate.keyword for t in scan.keywords]
    assert "knee replacement day one" in keywords and "knee replacement myths" not in keywords
    assert scan.ideas == [] and scan.llm_calls == 0


async def test_scan_without_searchable_topics_says_what_to_do():
    service = TrendScanService(
        settings=TrendSettings(), gateway=gateway(), suggester=None, web=None, writer=None
    )
    scan = await service.scan(request().__class__(**{**request().__dict__, "refs": []}))
    assert scan.keywords == [] and "profile" in scan.messages[0]


async def test_scan_with_no_matching_uploads_skips_the_idea_writer():
    writer = FakeWriter()
    service = TrendScanService(
        settings=TrendSettings(), gateway=FakeGateway([]), suggester=None, web=None, writer=writer
    )
    scan = await service.scan(request())
    assert scan.keywords and all(not trend.videos for trend in scan.keywords)
    assert scan.ideas == [] and writer.calls == 0
    assert not any("could not be written" in message for message in scan.messages)
