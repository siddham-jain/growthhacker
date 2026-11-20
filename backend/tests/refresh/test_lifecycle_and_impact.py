import hashlib
from datetime import timedelta

import pytest

from app.core.config import RefreshSettings
from app.domain.refresh import RefreshStatus
from app.services.refresh.eligibility import build_video_states
from app.services.refresh.impact import ImpactCalculator, views_at
from app.services.refresh.inputs import StoredRefresh
from app.services.refresh.lifecycle import RefreshLifecycle, detect_applied
from app.services.refresh.ops.apply import apply_ops
from tests.refresh import scenarios
from tests.refresh.fixtures import NOW, metrics, snapshots_linear, video, video_id

SETTINGS = RefreshSettings()


def stored(refresh, status=RefreshStatus.proposed, applied_days_ago: int | None = None) -> StoredRefresh:
    applied = (NOW - timedelta(days=applied_days_ago)).date() if applied_days_ago is not None else None
    return StoredRefresh(
        refresh=refresh.model_copy(update={"status": status}),
        applied_at=applied,
        applied_source="user" if applied else None,
        updated_at=NOW,
    )


def reconcile(items, *, signal_status=None, description=None, privacy="public", regenerated=()):
    data = scenarios.runable()
    signals = data.news_signals
    if signal_status:
        signals = [signals[0].model_copy(update={"verification_status": signal_status})]
    own = data.own_videos[0]
    if description is not None:
        own = own.model_copy(
            update={
                "description": description,
                "description_hash": hashlib.sha1(description.encode()).hexdigest(),
            }
        )
    own = own.model_copy(update={"privacy_status": privacy})
    return RefreshLifecycle(SETTINGS).reconcile(
        items, videos={own.video_id: own}, signals=signals, regenerated_video_ids=set(regenerated), now=NOW
    )


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("retracted", RefreshStatus.withdrawn),
        ("disputed", RefreshStatus.withdrawn),
        ("superseded", RefreshStatus.superseded),
    ],
)
async def test_news_status_propagates_to_open_suggestions(sample_refresh, status, expected) -> None:
    outcome = reconcile([stored(sample_refresh)], signal_status=status)
    assert [change.to_status for change in outcome.changes] == [expected]


async def test_retraction_after_applying_raises_a_correction_alert(sample_refresh) -> None:
    outcome = reconcile(
        [stored(sample_refresh, RefreshStatus.applied_detected, 5)], signal_status="retracted"
    )
    alert = outcome.alerts[0]
    assert alert.kind == "correction"
    assert "was retracted per Anthropic" in alert.message
    assert "Claude Fable 5.2 is now available" in alert.message
    disputed = reconcile([stored(sample_refresh, RefreshStatus.applied_claimed, 1)], signal_status="disputed")
    assert [item.kind for item in disputed.alerts] == ["caution"]


async def test_applied_is_detected_from_the_refetched_description(sample_refresh) -> None:
    description = scenarios.runable().own_videos[0].description
    pasted = apply_ops(description, sample_refresh.operations)
    outcome = reconcile([stored(sample_refresh)], description=pasted)
    change = outcome.changes[0]
    assert change.to_status is RefreshStatus.applied_detected
    assert change.applied_source == "detected"
    assert outcome.alerts[0].message.startswith("Looks like you applied this on 20 Nov")


async def test_partial_paste_with_reworded_line_still_counts(sample_refresh) -> None:
    description = scenarios.runable().own_videos[0].description
    reworded = "Claude Fable 5.2 is out now (Nov 2026); this was recorded before it.\n" + description
    reworded += "\n#MakeMoneyWithAI #AITools"
    assert detect_applied(sample_refresh, reworded, SETTINGS.applied_token_overlap)


async def test_rewritten_description_supersedes_the_suggestion(sample_refresh) -> None:
    outcome = reconcile([stored(sample_refresh)], description="A completely new description.")
    assert outcome.changes[0].to_status is RefreshStatus.superseded


async def test_expiry_privacy_and_regeneration(sample_refresh) -> None:
    expired = sample_refresh.model_copy(update={"expires_at": NOW - timedelta(minutes=1)})
    assert reconcile([stored(expired)]).changes[0].to_status is RefreshStatus.expired
    assert (
        reconcile([stored(sample_refresh)], privacy="private").changes[0].to_status is RefreshStatus.withdrawn
    )
    regenerated = reconcile([stored(sample_refresh)], regenerated=[sample_refresh.video_id])
    assert regenerated.changes[0].to_status is RefreshStatus.superseded


async def test_claimed_but_not_visible_nudges_the_user(sample_refresh) -> None:
    outcome = reconcile([stored(sample_refresh, RefreshStatus.applied_claimed, 3)])
    assert [alert.kind for alert in outcome.alerts] == ["not_visible"]


def impact_fixture(subject_post: float, control_post: float = 100.0, controls: int = 5, confound=None):
    applied_on = (NOW - timedelta(days=15)).date()
    start = applied_on - timedelta(days=14)
    records = [video(video_id("subj", 0), title="S", description="D", published_days_ago=300)]
    records += [
        video(video_id("ctrl", index), title="C", description="D", published_days_ago=290)
        for index in range(controls)
    ]
    states = build_video_states(
        records, [metrics(record, velocity=100) for record in records], [], NOW, SETTINGS
    )
    series = {
        records[0].video_id: snapshots_linear(
            records[0].video_id, start_views=10_000, per_day=[100] * 14 + [subject_post] * 15, start=start
        )
    }
    for record in records[1:]:
        series[record.video_id] = snapshots_linear(
            record.video_id, start_views=5_000, per_day=[100] * 14 + [control_post] * 15, start=start
        )
    return records, states, series, applied_on


async def test_impact_compares_against_unrefreshed_controls(sample_refresh) -> None:
    records, states, series, applied_on = impact_fixture(subject_post=150)
    refresh = sample_refresh.model_copy(update={"video_id": records[0].video_id})
    item = StoredRefresh(
        refresh=refresh.model_copy(update={"status": RefreshStatus.applied_claimed}),
        applied_at=applied_on,
        applied_source="user",
        updated_at=NOW,
    )
    calculator = ImpactCalculator(SETTINGS)
    assert calculator.due(item, NOW)
    impact = calculator.compute(
        item,
        states={state.video_id: state for state in states},
        snapshots=series,
        history=[item],
        owner_search_daily={},
        now=NOW,
    )
    assert impact.pre_velocity == pytest.approx(100)
    assert impact.post_velocity == pytest.approx(150)
    assert impact.control_ratio == pytest.approx(1.0)
    assert impact.adjusted_ratio == pytest.approx(1.5)
    assert impact.verdict == "up"
    assert any("not proof" in caveat for caveat in impact.caveats)


@pytest.mark.parametrize(
    ("subject_post", "controls", "expected"),
    [(70, 5, "down"), (105, 5, "no_clear_change"), (150, 4, "insufficient_data")],
)
async def test_impact_verdicts(sample_refresh, subject_post, controls, expected) -> None:
    records, states, series, applied_on = impact_fixture(subject_post=subject_post, controls=controls)
    item = StoredRefresh(
        refresh=sample_refresh.model_copy(
            update={"video_id": records[0].video_id, "status": RefreshStatus.applied_detected}
        ),
        applied_at=applied_on,
        applied_source="detected",
        updated_at=NOW,
    )
    impact = ImpactCalculator(SETTINGS).compute(
        item,
        states={state.video_id: state for state in states},
        snapshots=series,
        history=[item],
        owner_search_daily={},
        now=NOW,
    )
    assert impact.verdict == expected


async def test_impact_is_confounded_by_another_change(sample_refresh) -> None:
    records, states, series, applied_on = impact_fixture(subject_post=150)
    base = sample_refresh.model_copy(
        update={"video_id": records[0].video_id, "status": RefreshStatus.applied_claimed}
    )
    item = StoredRefresh(refresh=base, applied_at=applied_on, applied_source="user", updated_at=NOW)
    other = StoredRefresh(
        refresh=base.model_copy(update={"id": "00000000-0000-0000-0000-000000000001"}),
        applied_at=applied_on + timedelta(days=3),
        applied_source="user",
        updated_at=NOW,
    )
    impact = ImpactCalculator(SETTINGS).compute(
        item,
        states={state.video_id: state for state in states},
        snapshots=series,
        history=[item, other],
        owner_search_daily={},
        now=NOW,
    )
    assert impact.verdict == "confounded"


def test_views_are_interpolated_between_snapshots() -> None:
    series = snapshots_linear("interp00001", start_views=0, per_day=[100, 100], start=NOW.date())
    midday = series[0].captured_at + timedelta(hours=12)
    assert views_at(series, midday) == pytest.approx(50)
    assert views_at(series, series[-1].captured_at + timedelta(days=3)) is None
