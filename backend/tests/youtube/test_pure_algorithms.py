import random
from datetime import UTC, date, datetime, timedelta

import pytest

from app.core.config import (
    YouTubeFormatSettings,
    YouTubeFreshnessSettings,
    YouTubeIngestSettings,
    YouTubeSnapshotSettings,
)
from app.domain.common import VideoFormat
from app.domain.youtube import SearchQuery, VideoSnapshot
from app.ports.youtube import PlaylistItem
from app.services.youtube.context import SearchPool
from app.services.youtube.format_classifier import FormatClassifier, FormatInput
from app.services.youtube.ingest import LookbackPlanner
from app.services.youtube.input_parser import InputParser
from app.services.youtube.search import normalize_query_text, search_key, search_ttl
from app.services.youtube.snapshots import CadencePlanner, counting_regime_for, snapshots_to_drop
from tests.youtube.fixtures import APOLLO, NOW, video_id, video_record

CLASSIFIER = FormatClassifier(YouTubeFormatSettings())


def classify(
    duration: int | None,
    width: int | None = 400,
    height: int | None = 711,
    *,
    published: datetime = NOW,
    live: str = "none",
    ended: datetime | None = None,
    content_type: str | None = None,
    artist: bool | None = None,
):
    return CLASSIFIER.classify(
        FormatInput(
            live_broadcast_content=live,
            live_actual_end_at=ended,
            duration_s=duration,
            embed_width=width,
            embed_height=height,
            effective_published_at=published,
            creator_content_type=content_type,
            artist_channel=artist,
        )
    )


@pytest.mark.parametrize(
    ("duration", "width", "height", "published", "expected", "confidence"),
    [
        (45, 400, 711, NOW, VideoFormat.short_form, "high"),
        (170, 400, 500, NOW, VideoFormat.short_form, "high"),
        (227, 400, 711, NOW, VideoFormat.long_form, "high"),
        (31, 400, 225, NOW, VideoFormat.long_form, "high"),
        (90, 400, 711, datetime(2024, 10, 14, tzinfo=UTC), VideoFormat.long_form, "high"),
        (90, 400, 711, datetime(2024, 10, 15, tzinfo=UTC), VideoFormat.short_form, "medium"),
        (55, 400, 711, datetime(2024, 10, 14, tzinfo=UTC), VideoFormat.short_form, "high"),
        (50, None, None, NOW, VideoFormat.short_form, "low"),
        (120, None, None, NOW, VideoFormat.long_form, "medium"),
        (60, 400, 400, NOW, VideoFormat.short_form, "high"),
    ],
)
def test_shorts_rule(duration, width, height, published, expected, confidence) -> None:
    decision = classify(duration, width, height, published=published)
    assert (decision.format, decision.confidence) == (expected, confidence)
    assert decision.basis


def test_rule_order_puts_owner_analytics_and_live_first() -> None:
    assert classify(45, content_type="VIDEO_ON_DEMAND").format is VideoFormat.long_form
    assert classify(None, None, None, live="upcoming").format is VideoFormat.upcoming
    assert classify(45, live="live").format is VideoFormat.unknown
    assert classify(45, ended=NOW).format is VideoFormat.live
    assert classify(None).format is VideoFormat.unknown


def test_official_artist_channels_keep_sixty_seconds_until_their_cutoff() -> None:
    published = datetime(2025, 6, 1, tzinfo=UTC)
    assert classify(120, published=published, artist=True).format is VideoFormat.long_form
    assert classify(120, published=published, artist=False).confidence == "high"
    assert (
        classify(120, published=datetime(2025, 12, 8, tzinfo=UTC), artist=True).format
        is VideoFormat.short_form
    )


PARSER = InputParser()
VID = "q9hPAs_pOjo"


@pytest.mark.parametrize(
    ("raw", "kind", "value"),
    [
        (APOLLO, "channel_id", APOLLO),
        (f"https://www.youtube.com/channel/{APOLLO}", "channel_id", APOLLO),
        (f"youtube.com/channel/{APOLLO}/videos?si=x", "channel_id", APOLLO),
        ("@ApolloHospitalsIndia", "handle", "ApolloHospitalsIndia"),
        ("https://m.youtube.com/@ApolloHospitalsIndia/shorts", "handle", "ApolloHospitalsIndia"),
        ("https://www.youtube.com/%40ApolloHospitalsIndia", "handle", "ApolloHospitalsIndia"),
        ("https://www.youtube.com/@अपोलो", "handle", "अपोलो"),
        ("https://www.youtube.com/user/GoogleDevelopers", "username", "GoogleDevelopers"),
        ("https://www.youtube.com/c/ApolloHospitals", "custom_url", "ApolloHospitals"),
        ("https://www.youtube.com/ApolloHospitals", "custom_url", "ApolloHospitals"),
        (f"https://youtu.be/{VID}?si=abc", "video", VID),
        (f"https://www.youtube.com/watch?v={VID}&list=PLxyz1234567890", "video", VID),
        (f"https://www.youtube.com/shorts/{VID}", "video", VID),
        (f"https://www.youtube.com/live/{VID}", "video", VID),
        (f"https://www.youtube-nocookie.com/embed/{VID}", "video", VID),
        (f"https://music.youtube.com/watch?v={VID}", "video", VID),
        (
            "https://www.youtube.com/playlist?list=UU0InVdvqNyNzKBl1-TL348A",
            "playlist",
            "UU0InVdvqNyNzKBl1-TL348A",
        ),
        ("ApolloHospitalsIndia", "handle", "ApolloHospitalsIndia"),
        ("Apollo Hospitals Chennai", "unknown", "Apollo Hospitals Chennai"),
        ("https://www.apollohospitals.com/@fake", "unknown", "https://www.apollohospitals.com/@fake"),
        ("https://www.youtube.com/feed/trending", "unknown", "https://www.youtube.com/feed/trending"),
    ],
)
def test_input_parser_forms(raw: str, kind: str, value: str) -> None:
    ref = PARSER.parse(raw)
    assert (ref.kind, ref.value) == (kind, value)
    assert ref.raw == raw


def test_input_parser_never_raises_on_junk() -> None:
    rng = random.Random(7)
    alphabet = "ab@/:?=&%#.-_ अपोलो中\U0001f600https://youtube.com"
    for _ in range(2000):
        raw = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
        ref = PARSER.parse(raw)
        assert ref.kind in ("channel_id", "handle", "username", "custom_url", "video", "playlist", "unknown")


def test_search_key_normalisation_shares_equivalent_queries() -> None:
    base = SearchQuery(q="Knee  Replacement | cost!", type="video", published_after=date(2026, 10, 21))
    same = SearchQuery(q="COST|knee replacement", type="video", published_after=date(2026, 10, 21))
    assert normalize_query_text(base.q) == "cost | knee replacement"
    assert search_key(base) == search_key(same)
    assert search_key(base) != search_key(base.model_copy(update={"page": 1}))
    assert search_key(base) != search_key(base.model_copy(update={"order": "viewCount"}))


def test_search_ttl_by_window_and_pool() -> None:
    settings, today = YouTubeFreshnessSettings(), date(2026, 11, 20)

    def ttl(days: int | None, pool: SearchPool = SearchPool.niche) -> timedelta:
        after = today - timedelta(days=days) if days is not None else None
        return search_ttl(SearchQuery(q="x", type="video", published_after=after), pool, settings, today)

    assert ttl(7) == timedelta(hours=12)
    assert ttl(30) == timedelta(hours=24)
    assert ttl(90) == timedelta(hours=72)
    assert ttl(None) == timedelta(hours=72)
    assert ttl(90, SearchPool.news) == timedelta(hours=6)


def items(count: int, *, every_h: float, start: datetime = NOW) -> list[PlaylistItem]:
    return [
        PlaylistItem(video_id=video_id(i), published_at=start - timedelta(hours=every_h * i))
        for i in range(count)
    ]


def run_planner(uploads: list[PlaylistItem], format_of, cap: int = 300) -> LookbackPlanner:
    planner = LookbackPlanner(YouTubeIngestSettings(), NOW, cap)
    for start in range(0, len(uploads), 50):
        kept = [item for item in uploads[start : start + 50] if planner.accept(item)]
        kept = kept[: max(0, cap - planner.state.collected)]
        planner.count([video_record(item.video_id, **format_of(i)) for i, item in enumerate(kept)])
        if planner.done():
            break
    return planner


SHORT = {"duration": "PT40S", "width": 400, "height": 711}
LONG = {"duration": "PT8M", "width": 400, "height": 225}


def test_lookback_stops_after_window_once_formats_are_covered() -> None:
    # apollo-like cadence: about 16 uploads a month, half of them shorts
    planner = run_planner(items(4300, every_h=45), lambda i: SHORT if i % 2 else LONG)
    assert planner.formats_satisfied()
    assert planner.state.oldest < NOW - timedelta(days=180)
    assert planner.state.collected == 100


def test_lookback_caps_huge_channels_at_300() -> None:
    planner = run_planner(items(4300, every_h=2), lambda i: SHORT)
    assert planner.state.collected == 300


def test_lookback_shorts_only_channel_reads_up_to_the_max_horizon() -> None:
    planner = run_planner(items(200, every_h=48), lambda i: SHORT)
    assert planner.state.per_format[VideoFormat.long_form] == 0
    assert planner.state.oldest < NOW - timedelta(days=365)
    assert planner.state.collected < 200


def test_lookback_small_channel_takes_everything() -> None:
    planner = run_planner(items(12, every_h=24 * 20), lambda i: LONG)
    assert planner.state.collected == 12


SNAP = YouTubeSnapshotSettings()
PLANNER = CadencePlanner(SNAP, horizon_days=180)


def test_cadence_tiers_by_age() -> None:
    def plan(age_h: float, last_h: float):
        published = NOW - timedelta(hours=age_h)
        return PLANNER.plan(published, NOW, NOW - timedelta(hours=last_h))

    assert plan(10, 0.1).tier == "hourly" and plan(10, 0.1).next_due_at == NOW + timedelta(hours=0.9)
    assert plan(100, 1).tier == "six_hourly"
    assert plan(20 * 24, 1).tier == "daily"
    assert plan(100 * 24, 1).tier == "weekly"
    assert plan(200 * 24, 1) is None


def test_milestones_at_day_1_3_7_and_30() -> None:
    published = NOW - timedelta(hours=165)
    plan = PLANNER.plan(published, NOW, NOW - timedelta(hours=1))
    assert plan.milestone_target_h == 168 and plan.priority == 0
    assert plan.next_due_at == published + timedelta(hours=167)
    published = NOW - timedelta(hours=70)
    plan = PLANNER.plan(published, NOW, NOW - timedelta(hours=1))
    assert plan.milestone_target_h == 72
    plan = PLANNER.plan(published, NOW, published + timedelta(hours=71.5))
    assert plan.milestone_target_h is None


def test_counting_regime_markers() -> None:
    regime = counting_regime_for(SNAP)
    assert regime(datetime(2025, 1, 1, tzinfo=UTC)) == "standard"
    assert regime(datetime(2026, 9, 1, tzinfo=UTC)) == "2026-08-27-playback-start"
    assert regime(NOW) == "2026-11-16-coviews"


def series(hours: list[float]) -> list[VideoSnapshot]:
    published = NOW - timedelta(days=60)
    return [
        VideoSnapshot(
            video_id=video_id(1),
            slot_at=published + timedelta(hours=h),
            captured_at=published + timedelta(hours=h),
            age_hours=h,
            view_count=int(h * 10),
            source="batch_stats",
            counting_regime="2026-11-16-coviews",
        )
        for h in hours
    ]


def test_compaction_keeps_milestone_brackets_and_one_per_day() -> None:
    hours = [float(h) for h in range(0, 48)] + [float(h) for h in range(48, 192, 6)]
    snapshots = series(hours)
    dropped = {s.age_hours for s in snapshots_to_drop(snapshots, current_age_h=20 * 24, settings=SNAP)}
    kept = set(hours) - dropped
    assert {24.0, 72.0, 168.0} <= kept
    assert {23.0, 25.0} & kept == set()
    assert 0.0 in kept and 30.0 not in kept
    assert snapshots_to_drop(snapshots, current_age_h=7 * 24, settings=SNAP) == []


def test_compaction_thins_old_series_to_weekly() -> None:
    hours = [float(h) for h in range(8 * 24, 50 * 24, 24)]
    dropped = snapshots_to_drop(series(hours), current_age_h=50 * 24, settings=SNAP)
    kept = sorted(set(hours) - {s.age_hours for s in dropped})
    assert 720.0 in kept
    assert len(kept) <= 8
