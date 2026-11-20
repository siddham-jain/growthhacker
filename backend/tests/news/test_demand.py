import uuid
from datetime import timedelta

from app.domain.youtube import QuotaBucket
from app.ports.quota import QuotaExhausted
from app.services.news.demand import CandidateVideo, DemandRequest, saturation
from tests.news.fixtures import (
    NOW,
    RUNABLE_ID,
    FakeBaselines,
    FakeGateway,
    FakeReserve,
    MemoryDemandCache,
    NoLocalVideos,
    demand_checker,
    video_record,
)

CHANNELS = [f"UCdemandchannel{i:09d}" for i in range(8)]


def request(**overrides) -> DemandRequest:
    values = dict(
        client_id=RUNABLE_ID,
        run_id=None,
        canonical="Claude Fable 5.2",
        aliases=["Claude Mythos 5.2"],
        disambiguator=None,
        first_published_at=NOW - timedelta(hours=14),
        region="US",
        language="en",
        client_cap=3,
        own_subscriber_count=20_000,
    )
    values.update(overrides)
    return DemandRequest(**values)


def videos(count: int, views: int = 1_000):
    return [
        video_record(
            f"demandvid{i:02d}",
            CHANNELS[i % len(CHANNELS)],
            f"Claude Fable 5.2 test {i}",
            NOW - timedelta(hours=5),
            views,
        )
        for i in range(count)
    ]


async def test_local_cache_first_skips_the_search_call():
    local = [
        CandidateVideo(
            f"localvideo{i}",
            CHANNELS[i],
            "c",
            f"Claude Fable 5.2 deck {i}",
            "",
            NOW - timedelta(hours=3),
            500,
        )
        for i in range(3)
    ]
    gateway = FakeGateway(videos(5))
    outcome = await demand_checker(gateway, local=NoLocalVideos(local)).check(request(), NOW)
    assert outcome.check.status == "local_only"
    assert outcome.check.early_video_count == 3
    assert gateway.searches == []


async def test_shared_cache_serves_the_second_client_without_quota():
    cache, reserve = MemoryDemandCache(), FakeReserve()
    gateway = FakeGateway(videos(2))
    first = await demand_checker(gateway, cache=cache, reserve=reserve).check(request(), NOW)
    second = await demand_checker(gateway, cache=cache, reserve=reserve).check(
        request(client_id=uuid.uuid4()), NOW + timedelta(hours=1)
    )
    assert first.check.status == "checked" and first.check.quota_units_spent == 2
    assert second.from_cache and second.check.early_video_count == 2
    assert len(gateway.searches) == 1 and reserve.reserved == 1


async def test_quota_denial_and_exhaustion_leave_demand_unchecked():
    denied = await demand_checker(FakeGateway(videos(2)), reserve=FakeReserve(allow=False)).check(
        request(), NOW
    )
    assert denied.check.status == "unchecked_quota"

    class ExhaustedGateway(FakeGateway):
        async def search(self, query, page_token, ctx):
            raise QuotaExhausted(QuotaBucket.search, NOW + timedelta(hours=8))

    reserve = FakeReserve()
    exhausted = await demand_checker(ExhaustedGateway(), reserve=reserve).check(request(), NOW)
    assert exhausted.check.status == "unchecked_quota" and reserve.refunded == 1
    disabled = await demand_checker(FakeGateway(), reserve=FakeReserve()).check(request(client_cap=0), NOW)
    assert disabled.check.status == "unchecked_disabled"


async def test_grounding_drops_homonyms_and_requires_the_disambiguator():
    homonym = video_record("fablegame01", CHANNELS[0], "Fable 5 gameplay", NOW - timedelta(hours=2), 9_000)
    unrelated = video_record(
        "fablestory1", CHANNELS[1], "Fable 5.2 bedtime story", NOW - timedelta(hours=2), 9_000
    )
    real = video_record(
        "realfable01", CHANNELS[2], "Claude Fable 5.2 review", NOW - timedelta(hours=2), 9_000
    )
    gateway = FakeGateway([homonym, unrelated, real])
    outcome = await demand_checker(gateway).check(
        request(canonical="Fable 5.2", aliases=[], disambiguator="Claude"), NOW
    )
    assert [v.video_id for v in outcome.check.early_videos] == ["realfable01"]
    assert gateway.searches[0].q == '"Fable 5.2" Claude'


async def test_outlier_ratio_needs_a_baseline_and_uses_the_shared_threshold():
    early = videos(3, views=30_000)
    baselines = FakeBaselines({CHANNELS[0]: 5_000.0, CHANNELS[1]: 20_000.0})
    outcome = await demand_checker(FakeGateway(early), baselines).check(request(), NOW)
    by_channel = {v.channel_id: v for v in outcome.check.early_videos}
    assert by_channel[CHANNELS[0]].outlier_ratio == 6.0 and by_channel[CHANNELS[0]].is_outlier
    assert by_channel[CHANNELS[1]].outlier_ratio == 1.5 and not by_channel[CHANNELS[1]].is_outlier
    assert by_channel[CHANNELS[2]].outlier_ratio is None and not by_channel[CHANNELS[2]].is_outlier
    assert outcome.check.outlier_count == 1
    assert outcome.check.median_views_per_hour == 6_000.0


async def test_saturation_thresholds():
    async def check(count, subscribers=None):
        baselines = FakeBaselines(subscribers=subscribers or {})
        return (
            await demand_checker(FakeGateway(videos(count)), baselines).check(request(), NOW)
        ).check.saturation

    assert await check(0) == "empty"
    assert await check(2) == "emerging"
    assert await check(6) == "active"
    assert await check(20) == "crowded"
    assert await check(5, {c: 500_000 for c in CHANNELS}) == "crowded"
    assert saturation([], 0) == "empty"
