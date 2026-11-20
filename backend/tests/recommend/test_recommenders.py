from dataclasses import replace

from app.domain.analytics import LiftScope
from app.domain.recommendations import RecType
from app.services.recommend.inputs import ClientRecommendConfig, CreatorExclusion
from app.services.recommend.recommenders.creator import CreatorRecommender
from app.services.recommend.recommenders.patterns import (
    FormatRecommender,
    ThumbnailPatternRecommender,
    TitlePatternRecommender,
)
from app.services.recommend.recommenders.video_idea import VideoIdeaRecommender
from tests.recommend.conftest import context_for
from tests.recommend.fixtures import (
    AS_OF,
    VIDEOS,
    VideoSpec,
    apollo_inputs,
    channel_id,
    lifts,
    topic_id,
    trait_lift,
)


def ideas(ctx):
    return {
        (draft.subject.topic_id, draft.subject.format_key): draft
        for draft in VideoIdeaRecommender().generate(ctx)
    }


def test_video_idea_pairs_a_buyer_question_with_its_winning_format(ctx) -> None:
    knee = ideas(ctx)[(topic_id("bq_knee"), "long_form|3–8m")]

    assert knee.buyer_question_id is not None
    assert knee.gap.gap_type == "uncovered" and knee.gap.competitors_covering == 4
    assert [reason.code for reason in knee.reasons] == [
        "COMPETITORS_COVER_CLIENT_DOESNT",
        "FORMAT_LIFT",
        "OUTLIER_SHARE",
    ]
    assert {reason.code for reason in knee.fit_reasons} == {"BUYER_QUESTION_MATCH", "OWN_FORMAT_RESULT"}
    assert all(ref.outlier_ratio >= 1.0 for ref in knee.reference_pool)
    assert knee.reference_pool[0].format_key == "long_form|3–8m"


def test_broad_and_unproven_topics_seed_no_idea(ctx) -> None:
    topics = {topic for topic, _ in ideas(ctx)}

    assert topic_id("kw_health") not in topics
    assert topic_id("kw_spine") not in topics
    assert topic_id("bq_kidney") not in topics


def test_excluded_formats_are_skipped() -> None:
    config = ClientRecommendConfig(excluded_formats=["long_form|3–8m"])
    ctx = context_for(apollo_inputs(config=config))

    assert (topic_id("bq_knee"), "long_form|3–8m") not in ideas(ctx)


def test_bursts_of_videos_inside_72_hours_are_flagged_event_driven() -> None:
    burst = [
        VideoSpec(
            f"x{index}", channel, f"Spine update {index}", 3 + index * 0.4, 3.5, topics={"kw_spine": None}
        )
        for index, channel in enumerate(("fortis", "medanta", "manipal", "maxhc"))
    ]
    ctx = context_for(apollo_inputs(videos=[*VIDEOS, *burst]))
    spine = [draft for (topic, _), draft in ideas(ctx).items() if topic == topic_id("kw_spine")]

    assert spine and all("event_driven" in draft.flags for draft in spine)


def test_title_pattern_prefers_competitor_scope_and_uses_topic_relevant_outliers(ctx) -> None:
    (question,) = TitlePatternRecommender().generate(ctx)

    assert question.subject.traits == {"title.is_question": "true"}
    assert question.subject.scope is LiftScope.competitor_set
    assert question.subject.client_usage_share == 0.0
    assert all(ref.is_outlier and ref.matched_topic_ids for ref in question.reference_pool)
    assert {reason.code for reason in question.reasons} == {"TRAIT_LIFT", "MEDIAN_RATIO"}


def test_exploratory_lifts_never_become_patterns(ctx) -> None:
    drafts = [*TitlePatternRecommender().generate(ctx), *FormatRecommender().generate(ctx)]

    assert all("title.has_year" not in draft.subject.traits for draft in drafts)


def test_contradicting_scopes_are_flagged_and_large_samples_marked() -> None:
    against = trait_lift(
        LiftScope.niche,
        "title.is_question",
        "true",
        30,
        n_with=40,
        n_without=40,
        outliers_with=12,
        outliers_without=20,
        ci=(0.4, 0.9),
        refs=[],
    )
    analytics = apollo_inputs().analytics
    patched = [item.model_copy(update={"lifts": [*lifts(item.window_days), against]}) for item in analytics]
    ctx = context_for(apollo_inputs(analytics=patched))
    (question,) = TitlePatternRecommender().generate(ctx)

    assert "contradictory" in question.flags
    assert "MIXED_EVIDENCE" in {reason.code for reason in question.reasons}
    assert "contradictory_large_samples" not in question.flags


def test_thumbnail_and_format_patterns(ctx) -> None:
    thumbnails = ThumbnailPatternRecommender().generate(ctx)
    formats = {tuple(draft.subject.traits.items()) for draft in FormatRecommender().generate(ctx)}

    assert [draft.subject.traits for draft in thumbnails] == [{"thumbnail.text_present": "true"}]
    assert formats == {(("format_key", "long_form|3–8m"),), (("segment", "short_form"),)}


def creators(ctx):
    return {draft.subject.title: draft for draft in CreatorRecommender().generate(ctx)}


def test_creators_exclude_businesses_competitors_and_kids_channels(ctx) -> None:
    found = creators(ctx)

    assert {"Dr Heart Talks", "Ortho Simplified"} <= set(found)
    assert not {"Practo", "Kids Health Fun", "Fortis Healthcare", "Apollo Hospitals"} & set(found)


def test_creator_cards_carry_separate_stats_and_no_score(ctx) -> None:
    heart = creators(ctx)["Dr Heart Talks"]
    subject = heart.subject

    assert heart.score is None
    assert subject.relevance.match_share == 0.625
    assert subject.uploads_90d == 8 and subject.uploads_checked == 8
    assert subject.consistency.last_upload_at is not None
    assert "creator_score" not in subject.model_dump()


def test_collaborations_are_labelled_from_public_text_only(ctx) -> None:
    kinds = {(item.kind, item.matched_on) for item in creators(ctx)["Dr Heart Talks"].subject.collaborations}

    assert ("sponsor_language", "handle") in kinds
    assert ("disclosed_paid_promotion", "website_domain") in kinds


def test_excluded_and_inactive_creators_drop_out() -> None:
    exclusion = CreatorExclusion(channel_id=channel_id("drheart"), until=AS_OF.replace(year=2027))
    excluded = creators(
        context_for(apollo_inputs(config=ClientRecommendConfig(excluded_creators=[exclusion])))
    )
    quiet = [
        replace(spec, days_ago=spec.days_ago + 40) if spec.channel == "ortho" else spec for spec in VIDEOS
    ]
    inactive = creators(context_for(apollo_inputs(videos=quiet)))

    assert "Dr Heart Talks" not in excluded
    assert "Ortho Simplified" not in inactive


def test_creators_need_three_matching_uploads_inside_the_window() -> None:
    ctx = context_for(apollo_inputs(), window=7)

    assert "Dr Heart Talks" not in creators(ctx)


def test_disabled_types_are_not_generated(service) -> None:
    plan = service.plan(apollo_inputs(config=ClientRecommendConfig(disabled_types=["creator"])), 30)

    assert all(draft.type is not RecType.creator for draft in [*plan.selection.selected, *plan.creators])
