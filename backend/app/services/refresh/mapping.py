from collections.abc import Sequence
from datetime import datetime

from app.domain.analytics import TRUE_AGE_METHODS, AgeMethod, ViewsAtAgeSummary
from app.domain.recommendations import (
    MAX_DISPLAYED_REFERENCES,
    MAX_REFERENCE_POOL,
    EvidenceNumber,
    EvidenceSource,
    ExpectedRange,
    Lens,
    LensAssignment,
    Reason,
    RecommendationDraft,
    RecType,
    ReferenceVideo,
    RefreshSubject,
    make_stable_key,
)
from app.domain.refresh import DescriptionRefresh
from app.domain.topics import TopicRef
from app.services.refresh.demand import DemandEvidence
from app.services.refresh.inputs import ReferenceCandidate
from app.services.refresh.triggers.base import Finding, TriggerKind

PRODUCED_BY = "refresh_v1"
CONVERSION_KINDS = frozenset({TriggerKind.missing_cta, TriggerKind.missing_contact, TriggerKind.broken_link})


def reference_video(candidate: ReferenceCandidate) -> ReferenceVideo:
    metrics, video = candidate.metrics, candidate.video
    age = metrics.comparison_age_days
    at_age = metrics.views_at_age.get(age)
    summary = (
        at_age.summary()
        if at_age is not None
        else ViewsAtAgeSummary(
            age_days=age, views=None, method=AgeMethod.unavailable, label=f"views at day {age} (unavailable)"
        )
    )
    true_day7 = summary.age_days == 7 and summary.method in TRUE_AGE_METHODS
    outlier = metrics.outlier
    return ReferenceVideo(
        video_id=video.video_id,
        url=video.url,
        title=video.title,
        channel=video.channel_title,
        channel_id=video.channel_id,
        channel_role=candidate.role,
        published_at=video.published_at,
        views_day7=summary.views if true_day7 else None,
        views_at_age=summary,
        outlier_ratio=outlier.ratio if outlier else None,
        channel_baseline_views_at_age=outlier.baseline_median if outlier else None,
        is_outlier=bool(outlier and outlier.is_outlier),
        format_key=metrics.format_key,
        matched_topic_ids=list(candidate.matched_topic_ids),
        thumbnail_url=video.thumbnail_url,
    )


def stable_key_for(refresh: DescriptionRefresh, window_days: int) -> str:
    keys = "+".join(sorted({trigger.dedupe_key for trigger in refresh.triggers}))
    return make_stable_key(
        refresh.client_id, RecType.description_refresh, f"{refresh.video_id}|{keys}", window_days
    )


def _demand_reason(finding: Finding, demand: DemandEvidence) -> Reason:
    count, channels = len(demand.outlier_refs), demand.outlier_channels
    return Reason(
        reason_id=f"{finding.trigger_id}_demand",
        code="REFRESH_KEYWORD_DEMAND",
        params={"keyword": demand.keyword, "videos": count, "channels": channels},
        statement=(
            f"{count} recent videos from {channels} channels use '{demand.keyword}' and beat their "
            "channel's usual views"
        ),
        numbers=[
            EvidenceNumber(value=count, unit="videos", display=str(count)),
            EvidenceNumber(value=channels, unit="channels", display=str(channels)),
        ],
        ref_video_ids=[ref.video.video_id for ref in demand.outlier_refs][:MAX_REFERENCE_POOL],
        source=EvidenceSource.youtube_public,
    )


def _news_reasons(finding: Finding) -> list[Reason]:
    reasons = []
    for index, source in enumerate(finding.evidence.news_sources[:2]):
        when = f" on {source.published_at:%d %b %Y}" if source.published_at else ""
        reasons.append(
            Reason(
                reason_id=f"{finding.trigger_id}_news_{index}",
                code="REFRESH_NEWS_CONTEXT",
                params={"publisher": source.publisher, "url": source.url},
                statement=f"{source.publisher} reported {finding.keyword}{when}",
                source=EvidenceSource.news,
            )
        )
    return reasons


def to_recommendation_draft(
    refresh: DescriptionRefresh,
    *,
    findings: Sequence[Finding],
    references: Sequence[ReferenceCandidate],
    velocity: float | None,
    topic_refs: Sequence[TopicRef],
    expected_range: ExpectedRange,
    window_days: int,
    as_of: datetime,
) -> RecommendationDraft:
    """a keyword refresh becomes a description_refresh card draft; hygiene-only refreshes never do (99 C19)."""
    stable_key = stable_key_for(refresh, window_days)
    ranked = sorted(
        references, key=lambda ref: ref.metrics.outlier.ratio if ref.metrics.outlier else 0.0, reverse=True
    )
    pool = [reference_video(ref) for ref in ranked[:MAX_REFERENCE_POOL]]
    views: list[Reason] = []
    conversion: list[Reason] = []
    for finding in findings:
        if finding.keyword_trigger and finding.demand_evidence is not None and finding.proposes_text:
            views.append(_demand_reason(finding, finding.demand_evidence))
            views += _news_reasons(finding)
        elif finding.kind in CONVERSION_KINDS and finding.evidence.defect:
            conversion.append(
                Reason(
                    reason_id=f"{finding.trigger_id}_defect",
                    code="REFRESH_HYGIENE_DEFECT",
                    params={"defect": finding.evidence.defect},
                    statement=finding.evidence.defect[:1].upper() + finding.evidence.defect[1:],
                    source=EvidenceSource.youtube_public,
                )
            )
    if velocity is not None:
        rounded = round(velocity)
        views.append(
            Reason(
                reason_id=f"{refresh.id}_earning",
                code="REFRESH_STILL_EARNING",
                params={"views_per_day": rounded},
                statement=f"This video still gets about {rounded} views a day (calculated by GrowthHacker)",
                numbers=[EvidenceNumber(value=rounded, unit="views", display=str(rounded))],
                source=EvidenceSource.youtube_public,
            )
        )
    fit = [
        Reason(
            reason_id=f"{refresh.id}_fit_{ref.id}",
            code="REFRESH_TOPIC_FIT",
            params={"topic": ref.label},
            statement=f"Matches your topic '{ref.label}'",
            source=EvidenceSource.client_config,
        )
        for ref in topic_refs
    ]
    lenses = [LensAssignment(lens=Lens.views, reasons=views)]
    if conversion:
        lenses.append(LensAssignment(lens=Lens.conversion, reasons=conversion))
    entities = [finding.keyword for finding in findings if finding.keyword]
    return RecommendationDraft(
        draft_id=f"draft_{stable_key[:16]}",
        stable_key=stable_key,
        client_id=refresh.client_id,
        type=RecType.description_refresh,
        window_days=window_days,
        as_of=as_of,
        produced_by=PRODUCED_BY,
        subject=RefreshSubject(
            video_id=refresh.video_id,
            operations=refresh.operations,
            advisories=refresh.advisories,
            review_required=refresh.review_required,
        ),
        topic_ids=[ref.id for ref in topic_refs],
        reference_pool=pool,
        references=pool[:MAX_DISPLAYED_REFERENCES],
        reasons=views + conversion,
        fit_reasons=fit,
        lenses=lenses,
        expected_range=expected_range,
        confidence=refresh.confidence,
        entities=list(dict.fromkeys(entities)),
        allowed_dates=[as_of.date()],
        flags=["review_required"] if refresh.review_required else [],
    )
