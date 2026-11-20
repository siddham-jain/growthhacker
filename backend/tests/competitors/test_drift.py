import uuid
from datetime import UTC, datetime, timedelta

from app.core.config import CompetitorSettings
from app.domain.common import SubscriberBand
from app.domain.competitors import YouTubeRelevance
from app.domain.profile import FreshnessSensitivity, ProfileChange, ProfileDiff
from app.domain.youtube import OwnershipSignal
from app.services.competitors.drift.detector import DriftDetector
from app.services.competitors.drift.rediscovery import (
    RediscoveryPolicy,
    new_entrant_events,
    rediscovery_sources,
)
from app.services.competitors.drift.rules import ChannelSnapshot, DriftContext, TrackedChannel, week_marker
from app.services.competitors.score.topics import PhraseTopicMatcher
from tests.competitors.apollo import apollo_profile
from tests.competitors.fakes import NOW

CANDIDATE = uuid.uuid4()
CHANNEL = "UCn0ShMNqXI3kQP-Eoaf7olg"


def tracked(**changes) -> TrackedChannel:
    fields = dict(
        candidate_id=CANDIDATE,
        channel_id=CHANNEL,
        in_set=True,
        title="Fortis Healthcare",
        handle="@fortishealthcareofficial",
        official_domain="fortishealthcare.com",
        signals=(OwnershipSignal(name="channel_links_site", weight=0.4, detail="d"),),
        relevance=YouTubeRelevance(
            activity=1, content_overlap=0.6, size_band="peer", subscriber_band=SubscriberBand.from_100k_to_1m
        ),
        flags=frozenset(),
    )
    fields.update(changes)
    return TrackedChannel(**fields)


def snapshot(**changes) -> ChannelSnapshot:
    fields = dict(
        title="Fortis Healthcare",
        handle="@fortishealthcareofficial",
        description="www.fortishealthcare.com",
        availability="available",
        last_upload_at=NOW - timedelta(days=2),
        recent_texts=("Knee replacement recovery", "Heart surgery explained"),
    )
    fields.update(changes)
    return ChannelSnapshot(**fields)


def run(item: TrackedChannel, latest: ChannelSnapshot | None, now: datetime = NOW):
    ctx = DriftContext(
        client_id=uuid.uuid4(),
        now=now,
        settings=CompetitorSettings(),
        matcher=PhraseTopicMatcher.from_profile(apollo_profile()),
        latest={CHANNEL: latest} if latest else {},
    )
    return {event.kind for event in DriftDetector().detect([item], ctx)}, ctx


def test_healthy_channel_has_no_drift():
    assert run(tracked(), snapshot())[0] == set()


def test_missing_inactive_and_renamed():
    assert run(tracked(), snapshot(availability="unavailable"))[0] == {"channel_missing"}
    assert run(tracked(), snapshot(last_upload_at=NOW - timedelta(days=120)))[0] == {"inactive"}
    assert run(tracked(flags=frozenset({"inactive"})), snapshot(last_upload_at=None))[0] == set()
    assert run(tracked(), snapshot(title="Fortis Hospitals", handle="@fortishospitals"))[0] == {"renamed"}


def test_ownership_weakens_when_the_description_link_disappears():
    kinds, _ = run(tracked(), snapshot(title="Fortis Hospitals", description="new description"))
    assert kinds == {"renamed", "ownership_weakened"}
    site_backed = tracked(
        signals=(OwnershipSignal(name="site_links_channel", weight=0.5, detail="d"), *tracked().signals)
    )
    assert run(site_backed, snapshot(description="new description"))[0] == set()


def test_overlap_drop_needs_two_consecutive_low_weeks():
    low = snapshot(recent_texts=("Vlog", "Behind the scenes", "Award night"))
    item = tracked()
    kinds, ctx = run(item, low)
    assert "overlap_drop" not in kinds
    markers = DriftDetector.overlap_markers(item, ctx)
    assert markers == {week_marker(NOW)}
    next_week = NOW + timedelta(days=7)
    kinds, ctx = run(tracked(flags=frozenset(markers)), low, now=next_week)
    assert "overlap_drop" in kinds
    assert DriftDetector.overlap_markers(tracked(flags=frozenset(markers)), ctx) == {
        week_marker(next_week),
        week_marker(NOW),
    }


def test_niche_riser_flags_channels_outside_the_set():
    assert run(tracked(in_set=False, niche_queries_recent=3), None)[0] == {"niche_riser"}
    assert run(tracked(in_set=False, niche_queries_recent=2), None)[0] == set()


def test_rediscovery_policy():
    policy = RediscoveryPolicy(CompetitorSettings())
    assert policy.due(None, NOW, None)
    assert not policy.due(NOW - timedelta(days=20), NOW, FreshnessSensitivity.medium)
    assert policy.due(NOW - timedelta(days=20), NOW, FreshnessSensitivity.very_high)
    assert policy.due(NOW - timedelta(days=31), NOW, FreshnessSensitivity.low)
    diff = ProfileDiff(
        from_version=1,
        to_version=2,
        changed=[ProfileChange(id="o1", field="offerings.o1.name", before="a", after="b")],
        significance="high",
    )
    assert policy.profile_change_requires_rediscovery(diff)
    assert not policy.profile_change_requires_rediscovery(
        ProfileDiff(from_version=1, to_version=2, added=["value_propositions.v1"], significance="low")
    )
    assert rediscovery_sources() == {"llm", "web_search", "niche_search", "rediscovery_news"}


async def test_new_entrants_are_only_unknown_confident_candidates():
    from tests.competitors.test_golden_apollo import run_apollo

    result, *_ = await run_apollo()
    known = {candidate.id for candidate in result.candidates if candidate.brand_name != "Max Healthcare"}
    events = new_entrant_events(result, known, uuid.uuid4(), datetime(2026, 12, 20, tzinfo=UTC))
    assert [event.detail["name"] for event in events] == ["Max Healthcare"]
