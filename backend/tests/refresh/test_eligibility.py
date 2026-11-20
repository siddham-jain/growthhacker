from datetime import timedelta

from app.core.config import RefreshSettings
from app.domain.common import VideoFormat
from app.domain.profile import FreshnessSensitivity
from app.services.refresh.eligibility import BusinessEligibilityEvaluator, VideoGate, build_video_states
from app.services.refresh.inputs import RefreshClientOptions, StoredRefresh
from tests.refresh.fixtures import CLIENT_ID, NOW, RUN_ID, freshness, metrics, news_signal, video, video_id

SETTINGS = RefreshSettings()


def states_for(velocities_by_age: list[tuple[float, float]], *, snapshots: bool = True):
    videos, measures = [], []
    for index, (age, speed) in enumerate(velocities_by_age):
        record = video(
            video_id("vid", index), title=f"Video {index}", description="Body", published_days_ago=age
        )
        videos.append(record)
        measures.append(metrics(record, velocity=speed, snapshots_basis=snapshots))
    return build_video_states(videos, measures, [], NOW, SETTINGS)


def evaluate(states, *, sensitivity=FreshnessSensitivity.low, signals=(), gaps=(), regulated=False, **kwargs):
    return BusinessEligibilityEvaluator(SETTINGS).evaluate(
        client_id=CLIENT_ID,
        run_id=RUN_ID,
        now=NOW,
        freshness=freshness(
            sensitivity, regulated=regulated, domain_kind="healthcare" if regulated else "none"
        ),
        states=states,
        signals=list(signals),
        buyer_gap_video_ids=set(gaps),
        defect_video_ids=set(),
        options=kwargs.pop("options", RefreshClientOptions()),
        last_keyword_eval_at=kwargs.pop("last_eval", None),
    )


def signals(count: int, relevance: float = 0.9, first_seen_days_ago: float = 2):
    return [
        news_signal(
            f"Model {index}.0", topic_ref_ids=[], relevance=relevance, first_seen_days_ago=first_seen_days_ago
        )
        for index in range(count)
    ]


def test_ai_company_after_a_launch_refreshes_periodically() -> None:
    # 2 evergreen of 5 earning videos hold 40% of views; 8 signals; prior high (plan §5.4 example)
    states = states_for([(200, 40), (300, 40), (20, 40), (30, 40), (40, 40)])
    decision = evaluate(states, sensitivity=FreshnessSensitivity.high, signals=signals(8))
    assert decision.decision == "periodic"
    assert abs(decision.eligibility.keyword_refresh_need - (0.45 * 0.95 + 0.35 * 0.4)) < 1e-3
    assert decision.keyword_enabled
    assert decision.news_relevance_min == SETTINGS.periodic_news_relevance_min


def test_high_need_is_active() -> None:
    states = states_for([(200, 100), (300, 100), (400, 100)])
    decision = evaluate(
        states, sensitivity=FreshnessSensitivity.very_high, signals=signals(8), gaps={states[0].video_id}
    )
    assert decision.decision == "active"
    assert decision.eligibility.cadence_days == 3


def test_regulated_and_provisional_cap_at_periodic() -> None:
    states = states_for([(200, 100), (300, 100), (400, 100)])
    regulated = evaluate(states, sensitivity=FreshnessSensitivity.high, signals=signals(8), regulated=True)
    assert regulated.decision == "periodic" and regulated.review_required
    provisional_states = states_for([(200, 100), (300, 100), (400, 100)], snapshots=False)
    provisional = evaluate(provisional_states, sensitivity=FreshnessSensitivity.very_high, signals=signals(8))
    assert provisional.provisional
    assert provisional.decision in ("periodic", "hygiene_only")
    assert any("lifetime averages" in reason for reason in provisional.eligibility.reasons)


def test_no_earning_videos_means_no_refresh() -> None:
    decision = evaluate(
        states_for([(200, 1), (300, 2)]), sensitivity=FreshnessSensitivity.high, signals=signals(3)
    )
    assert decision.decision == "none"


def test_quiet_niche_without_gaps_stays_hygiene_only() -> None:
    decision = evaluate(states_for([(200, 30), (300, 30), (400, 30)]))
    assert decision.decision == "hygiene_only"
    assert not decision.keyword_enabled


def test_settings_overrides() -> None:
    states = states_for([(200, 100), (300, 100)])
    off = evaluate(
        states,
        sensitivity=FreshnessSensitivity.high,
        signals=signals(8),
        options=RefreshClientOptions(mode="off"),
    )
    assert off.decision == "none"
    limited = evaluate(
        states,
        sensitivity=FreshnessSensitivity.high,
        signals=signals(8),
        options=RefreshClientOptions(mode="hygiene_only"),
    )
    assert limited.decision == "hygiene_only"


def test_cadence_skips_keyword_checks_until_due_or_big_news() -> None:
    states = states_for([(200, 100), (300, 100)])
    recent = NOW - timedelta(days=1)
    gaps = {states[0].video_id}
    waiting = evaluate(
        states,
        sensitivity=FreshnessSensitivity.high,
        signals=signals(2, relevance=0.7),
        gaps=gaps,
        last_eval=recent,
    )
    assert waiting.decision == "active" and not waiting.keyword_checks_due
    assert not waiting.keyword_enabled
    fresh_news = signals(2, first_seen_days_ago=0.5)
    bypass = evaluate(
        states, sensitivity=FreshnessSensitivity.high, signals=fresh_news, gaps=gaps, last_eval=recent
    )
    assert bypass.keyword_checks_due


DEFAULT_OPTIONS = RefreshClientOptions()


def gate(state, *, decision=None, history=(), options=DEFAULT_OPTIONS, fit=0.9):
    decision = decision or evaluate(
        [state], sensitivity=FreshnessSensitivity.high, signals=signals(8), gaps={state.video_id}
    )
    return VideoGate(SETTINGS).evaluate(
        state,
        now=NOW,
        fit_score=fit,
        language_supported=True,
        decision=decision,
        history=list(history),
        options=options,
    )


def test_video_gate_filters() -> None:
    record = video("gate0000001", title="T", description="D", published_days_ago=200)
    state = build_video_states([record], [metrics(record, velocity=50)], [], NOW, SETTINGS)[0]
    assert gate(state).keyword_eligible and gate(state).cta_eligible
    private = build_video_states(
        [record.model_copy(update={"privacy_status": "unlisted"})],
        [metrics(record, velocity=50)],
        [],
        NOW,
        SETTINGS,
    )[0]
    assert not gate(private).hygiene_eligible
    stale = build_video_states(
        [record.model_copy(update={"metadata_fetched_at": NOW - timedelta(days=5)})],
        [metrics(record, velocity=50)],
        [],
        NOW,
        SETTINGS,
    )[0]
    assert "description needs a refetch" in gate(stale).skip_reasons
    assert (
        "excluded in Settings"
        in gate(state, options=RefreshClientOptions(excluded_video_ids=[record.video_id])).skip_reasons
    )
    assert not gate(state, fit=0.3).cta_eligible


def test_shorts_and_kids_videos_never_get_booking_links() -> None:
    short = video(
        "short000001",
        title="T",
        description="D",
        published_days_ago=200,
        duration_s=45,
        fmt=VideoFormat.short_form,
    )
    kids = video("kids0000001", title="T", description="D", published_days_ago=200).model_copy(
        update={"made_for_kids": True}
    )
    unsure = video("unsure00001", title="T", description="D", published_days_ago=200, duration_s=150)
    states = build_video_states(
        [short, kids, unsure],
        [metrics(item, velocity=50) for item in (short, kids, unsure)],
        [],
        NOW,
        SETTINGS,
    )
    assert [gate(state).cta_eligible for state in states] == [False, False, False]


def test_recently_applied_videos_cool_down(sample_refresh) -> None:
    record = video(sample_refresh.video_id, title="T", description="D", published_days_ago=200)
    state = build_video_states([record], [metrics(record, velocity=50)], [], NOW, SETTINGS)[0]
    applied = StoredRefresh(
        refresh=sample_refresh.model_copy(update={"status": "applied_claimed"}),
        applied_at=(NOW - timedelta(days=10)).date(),
        applied_source="user",
        updated_at=NOW,
    )
    result = gate(state, history=[applied])
    assert not result.hygiene_eligible
    assert any("refreshed within" in reason for reason in result.skip_reasons)
