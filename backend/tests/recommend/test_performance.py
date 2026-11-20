import random
import time

import pytest

from app.core.config import RecommendSettings
from app.domain.common import ChannelRole
from app.domain.topics import TopicKind
from app.services.recommend.service import RecommendationService
from tests.recommend import fixtures
from tests.recommend.fixtures import ChannelSpec, TopicSpec, VideoSpec

VIDEOS, TOPICS, CREATORS = 5_000, 60, 40


@pytest.fixture
def large_niche(monkeypatch: pytest.MonkeyPatch):
    rng = random.Random(5)
    channels = [fixtures.CHANNEL_BY_KEY[key] for key in ("apollo", *fixtures.COMPETITOR_KEYS)]
    channels += [
        ChannelSpec(
            f"cr{index}",
            f"Creator {index}",
            f"@Creator{index}",
            ChannelRole.niche,
            rng.choice([800, 2_000, 6_000]),
        )
        for index in range(CREATORS)
    ]
    topics = [TopicSpec(f"kw{index}", TopicKind.keyword, f"keyword {index}") for index in range(TOPICS)]
    videos = []
    for index in range(VIDEOS):
        channel = rng.choice(channels[1:])
        ratio = round(rng.lognormvariate(0, 0.9), 2)
        videos.append(
            VideoSpec(
                f"p{index}",
                channel.key,
                f"Video {index} about keyword {index % TOPICS}",
                round(rng.uniform(2.5, 89), 1),
                ratio,
                segment=rng.choice(["long_form", "short_form"]),
                topics={f"kw{index % TOPICS}": None},
                question=rng.random() < 0.3,
                thumb_text=rng.random() < 0.6,
            )
        )
    monkeypatch.setattr(fixtures, "CHANNELS", channels)
    monkeypatch.setattr(fixtures, "CHANNEL_BY_KEY", {spec.key: spec for spec in channels})
    monkeypatch.setattr(fixtures, "TOPICS", topics)
    return fixtures.apollo_inputs(
        videos=videos,
        buyer_questions=[],
        news_drafts=[],
        refresh_drafts=[],
        refreshes=[],
        competitor_candidates=[],
        niche_search_hits={fixtures.channel_id(f"cr{index}"): 3 for index in range(CREATORS)},
    )


async def test_deterministic_recommend_for_a_large_niche_stays_fast(large_niche) -> None:
    service = RecommendationService(RecommendSettings())
    started = time.perf_counter()
    plan = service.plan(large_niche, 30)
    elapsed = time.perf_counter() - started

    assert plan is not None and plan.candidate_count > 0
    assert elapsed < 2.0, f"one window took {elapsed:.2f}s"
