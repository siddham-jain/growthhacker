from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

import structlog
from pydantic import BaseModel, ValidationError
from sqlalchemy import Select, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.registry import adapter_registry
from app.domain.analytics import AgeMethod, ThumbnailTraits
from app.domain.common import ChannelRole
from app.domain.topics import AnswerDirectness, TopicMatch
from app.domain.youtube import (
    ChannelRecord,
    CommentRecord,
    OAuthConnectionStatus,
    OwnAnalytics,
    OwnVideoDayViews,
    OwnVideoMetrics,
    SearchTermRow,
    TrafficSourceRow,
    VideoRecord,
    VideoSnapshot,
)
from app.models.competitors import CompetitorSet as CompetitorSetRow
from app.models.own_analytics import OwnAnalyticsDaily, OwnSearchTerm, OwnTrafficSource
from app.models.tenancy import OAuthToken
from app.models.topics import AnswerDirectness as AnswerDirectnessRow
from app.models.topics import TopicMatch as TopicMatchRow
from app.models.website import BusinessProfile as BusinessProfileRow
from app.models.youtube_cache import YtChannel, YtComment, YtVideo, YtVideoSnapshot
from app.models.youtube_derived import (
    YtChannelBaseline,
    YtChannelRecurringText,
    YtCommentSignal,
    YtThumbnailTrait,
    YtVideoAgeValue,
    YtVideoMetric,
    YtVideoOutlier,
    YtVideoTrait,
)
from app.services.analytics.comments.lexicon import intent_pack_registry
from app.services.analytics.inputs import AnalysisRequest, AnalyticsInputs, ClientContext, ScopedChannel
from app.services.analytics.result import AnalyticsResult

log = structlog.get_logger(__name__)
CHUNK = 1_000
EPOCH = datetime(2005, 4, 23, tzinfo=UTC)
SHARED_AGE = 7
# marks a channel whose recurring text was computed even when nothing recurs
COMPUTED_MARKER = ("", -1)
# client-context trait fields never go into the shared traits table
CLIENT_TITLE_FIELDS = {"has_client_location", "has_niche_location"}
CLIENT_DESCRIPTION_FIELDS = {"has_client_site_link", "first_two_lines_have_client_keyword"}


def _validated[M: BaseModel](model: type[M], rows: Iterable[Mapping[str, Any]], what: str) -> list[M]:
    items = []
    for row in rows:
        try:
            items.append(model.model_validate(row))
        except ValidationError as error:
            log.warning("analytics.input_row_skipped", kind=what, errors=error.error_count())
    return items


def _confidence_label(value: float | None) -> str:
    if value is None:
        return "low"
    return "high" if value >= 0.8 else "medium" if value >= 0.5 else "low"


def channel_record(row: YtChannel) -> dict[str, Any]:
    return {
        "channel_id": row.channel_id,
        "title": row.title or "",
        "handle": row.handle,
        "description": row.description or "",
        "country": row.country,
        "default_language": row.default_language,
        "published_at": row.published_at or EPOCH,
        "uploads_playlist_id": row.uploads_playlist_id,
        "subscriber_count": None if row.hidden_subscriber_count else row.subscriber_count,
        "hidden_subscriber_count": row.hidden_subscriber_count,
        "view_count": row.view_count or 0,
        "video_count": row.video_count or 0,
        "keywords": row.keywords or [],
        "thumbnail_url": row.thumbnail_url,
        "made_for_kids": row.made_for_kids,
        "availability": "available" if row.availability == "available" else "unavailable",
        "unavailable_at": row.unavailable_at,
        "uploads_90d": row.uploads_90d or 0,
        "last_upload_at": row.last_upload_at,
        "recent_upload_ids": (row.recent_upload_ids or [])[:50],
        "cadence_flags": row.cadence_flags or [],
        "metadata_fetched_at": row.metadata_fetched_at,
        "stats_fetched_at": row.stats_fetched_at,
    }


def video_record(row: YtVideo) -> dict[str, Any]:
    return {
        "video_id": row.video_id,
        "channel_id": row.channel_id,
        "channel_title": row.channel_title or "",
        "published_at": row.published_at,
        "effective_published_at": row.effective_published_at,
        "live_broadcast_content": row.live_broadcast_content or "none",
        "live_actual_start_at": row.live_actual_start_at,
        "live_actual_end_at": row.live_actual_end_at,
        "live_scheduled_start_at": row.live_scheduled_start_at,
        "title": row.title or "",
        "description": row.description or "",
        "tags": row.tags or [],
        "content_hash": row.content_hash or "",
        "description_hash": row.description_hash or "",
        "default_language": row.default_language,
        "default_audio_language": row.default_audio_language,
        "duration_s": row.duration_s,
        "format": row.format or "unknown",
        "format_basis": row.format_basis or [],
        "format_confidence": _confidence_label(row.format_confidence),
        "made_for_kids": bool(row.made_for_kids),
        "has_paid_product_placement": row.has_paid_product_placement,
        "thumbnails": row.thumbnails or {},
        "thumbnail_url": row.thumbnail_url,
        "comments_state": row.comments_state or "unknown",
        "likes_visible": row.likes_visible is not False,
        "view_count": row.view_count or 0,
        "like_count": row.like_count,
        "comment_count": row.comment_count,
        "availability": row.availability or "available",
        "first_missing_at": row.first_missing_at,
        "ingest_source": row.ingest_source,
        "quality_flags": row.quality_flags or [],
        "metadata_fetched_at": row.metadata_fetched_at,
    }


def _columns(row: Any, names: Iterable[str]) -> dict[str, Any]:
    return {name: getattr(row, name) for name in names}


async def _chunked_scalars(session: AsyncSession, ids: Sequence[str], build: Any) -> list[Any]:
    found: list[Any] = []
    for start in range(0, len(ids), CHUNK):
        statement: Select = build(ids[start : start + CHUNK])
        found += (await session.execute(statement)).scalars().all()
    return found


class SharedCacheInputSource:
    """reads 02's shared cache plus the client's topic matches, profile and owner analytics."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], window_days: int = 90) -> None:
        self.session_factory = session_factory
        self.window_days = window_days

    async def load(self, request: AnalysisRequest) -> AnalyticsInputs:
        async with self.session_factory() as session:
            niche_ids = await self._niche_channel_ids(session, request)
            roles = {cid: ChannelRole.creator for cid in request.creator_channel_ids}
            roles |= {cid: ChannelRole.niche for cid in niche_ids}
            roles |= {cid: ChannelRole.competitor for cid in request.competitor_channel_ids}
            if request.own_channel_id:
                roles[request.own_channel_id] = ChannelRole.own
            channel_rows = await _chunked_scalars(
                session, sorted(roles), lambda ids: select(YtChannel).where(YtChannel.channel_id.in_(ids))
            )
            profile = await self._profile(session, request)
            channels = [
                ScopedChannel(
                    channel=record,
                    role=roles[record.channel_id],
                    website_domains=profile.website_domains
                    if roles[record.channel_id] is ChannelRole.own
                    else [],
                )
                for record in _validated(ChannelRecord, (channel_record(r) for r in channel_rows), "channel")
            ]
            videos = await self._videos(session, sorted(roles), request)
            video_ids = sorted(v.video_id for v in videos)
            window_start = request.as_of - timedelta(days=self.window_days)
            window_ids = sorted(v.video_id for v in videos if v.published_at >= window_start)
            return AnalyticsInputs(
                client=profile,
                own_channel_id=request.own_channel_id,
                channels=sorted(channels, key=lambda c: c.channel.channel_id),
                videos=videos,
                snapshots=await self._snapshots(session, video_ids, request),
                comments=await self._comments(session, window_ids, request),
                topic_refs=request.topic_refs,
                topic_matches=await self._topic_matches(session, request, video_ids),
                directness=await self._directness(session, request, video_ids),
                thumbnails=await self._thumbnails(session, window_ids),
                own_analytics=await self._own_analytics(session, request)
                if request.oauth_connected
                else None,
            )

    @staticmethod
    async def _niche_channel_ids(session: AsyncSession, request: AnalysisRequest) -> list[str]:
        statement = (
            select(CompetitorSetRow.niche_channel_ids)
            .where(CompetitorSetRow.client_id == request.client_id)
            .order_by(CompetitorSetRow.version.desc())
            .limit(1)
        )
        found = (await session.execute(statement)).scalar_one_or_none()
        return list(found or [])

    @staticmethod
    async def _profile(session: AsyncSession, request: AnalysisRequest) -> ClientContext:
        statement = (
            select(BusinessProfileRow.profile)
            .where(BusinessProfileRow.client_id == request.client_id)
            .order_by(BusinessProfileRow.version.desc())
            .limit(1)
        )
        profile = (await session.execute(statement)).scalar_one_or_none() or {}
        identity = profile.get("identity") or {}
        locations = profile.get("locations") or {}
        location_terms = list(locations.get("cities") or [])
        for item in locations.get("items") or []:
            location_terms += [item.get("name"), item.get("city"), *(item.get("aliases") or [])]
        keyword_terms = [ref.label for ref in request.topic_refs] + [
            alias.text for ref in request.topic_refs for alias in ref.aliases if alias.status == "confirmed"
        ]
        short_codes = [
            action.get("target", "")
            for action in profile.get("conversion_actions") or []
            if action.get("type") == "call"
            and str(action.get("target", "")).isdigit()
            and len(action["target"]) <= 5
        ]
        industry = identity.get("industry")
        return ClientContext(
            client_id=str(request.client_id),
            timezone=request.timezone or identity.get("timezone"),
            country=request.country or identity.get("country"),
            languages=list(identity.get("languages") or ([request.language] if request.language else [])),
            website_domains=list(identity.get("website_domains") or []),
            location_terms=sorted({t for t in location_terms if t}),
            keyword_terms=sorted({t for t in keyword_terms if t}),
            niche=industry if industry in intent_pack_registry else None,
            phone_short_codes=sorted(set(short_codes)),
        )

    @staticmethod
    async def _videos(
        session: AsyncSession, channel_ids: list[str], request: AnalysisRequest
    ) -> list[VideoRecord]:
        since = request.as_of - timedelta(days=request.lookback_days)
        rows = await _chunked_scalars(
            session,
            channel_ids,
            lambda ids: select(YtVideo).where(
                YtVideo.channel_id.in_(ids),
                YtVideo.effective_published_at >= since,
                YtVideo.published_at <= request.as_of,
            ),
        )
        return sorted(
            _validated(VideoRecord, (video_record(r) for r in rows), "video"), key=lambda v: v.video_id
        )

    @staticmethod
    async def _snapshots(
        session: AsyncSession, video_ids: list[str], request: AnalysisRequest
    ) -> dict[str, list[VideoSnapshot]]:
        names = (
            "video_id",
            "slot_at",
            "captured_at",
            "age_hours",
            "view_count",
            "like_count",
            "comment_count",
            "source",
            "counting_regime",
        )
        rows = await _chunked_scalars(
            session,
            video_ids,
            lambda ids: select(YtVideoSnapshot).where(
                YtVideoSnapshot.video_id.in_(ids), YtVideoSnapshot.captured_at <= request.as_of
            ),
        )
        grouped: dict[str, list[VideoSnapshot]] = defaultdict(list)
        for snap in _validated(VideoSnapshot, (_columns(r, names) for r in rows), "snapshot"):
            grouped[snap.video_id].append(snap)
        return {vid: sorted(items, key=lambda s: s.captured_at) for vid, items in sorted(grouped.items())}

    @staticmethod
    async def _comments(
        session: AsyncSession, video_ids: list[str], request: AnalysisRequest
    ) -> dict[str, list[CommentRecord]]:
        rows = await _chunked_scalars(
            session,
            video_ids,
            lambda ids: select(YtComment).where(
                YtComment.video_id.in_(ids), YtComment.fetched_at <= request.as_of
            ),
        )
        owner_threads = {r.thread_id for r in rows if r.is_reply and r.is_channel_owner_reply}
        grouped: dict[str, list[CommentRecord]] = defaultdict(list)
        for row in rows:
            if row.is_reply:
                continue
            grouped[row.video_id].append(
                CommentRecord(
                    comment_id=row.comment_id,
                    video_id=row.video_id,
                    text=row.text_redacted,
                    like_count=row.like_count or 0,
                    reply_count=row.reply_count or 0,
                    published_at=row.published_at,
                    relevance_rank=row.relevance_rank or 0,
                    is_channel_owner_reply=row.is_channel_owner_reply,
                    owner_replied=row.thread_id in owner_threads,
                    fetched_at=row.fetched_at,
                )
            )
        return dict(sorted(grouped.items()))

    @staticmethod
    async def _topic_matches(
        session: AsyncSession, request: AnalysisRequest, video_ids: list[str]
    ) -> list[TopicMatch]:
        names = (
            "client_id",
            "video_id",
            "topic_ref_id",
            "topic_kind",
            "profile_version",
            "universe_hash",
            "prepared_hash",
            "engine_version",
            "decision",
            "lexical_score",
            "semantic_score",
            "combined_score",
            "method",
            "confidence",
            "is_primary",
            "rank",
            "implied_from",
            "evidence",
            "review_reasons",
            "degraded",
            "explanation",
            "matched_at",
        )
        rows = await _chunked_scalars(
            session,
            video_ids,
            lambda ids: select(TopicMatchRow).where(
                TopicMatchRow.client_id == request.client_id,
                TopicMatchRow.video_id.in_(ids),
                TopicMatchRow.deleted_at.is_(None),
            ),
        )
        return _validated(TopicMatch, (_columns(r, names) for r in rows), "topic_match")

    @staticmethod
    async def _directness(
        session: AsyncSession, request: AnalysisRequest, video_ids: list[str]
    ) -> list[AnswerDirectness]:
        rows = await _chunked_scalars(
            session,
            video_ids,
            lambda ids: select(AnswerDirectnessRow).where(
                AnswerDirectnessRow.client_id == request.client_id, AnswerDirectnessRow.video_id.in_(ids)
            ),
        )
        payload = (
            {
                "client_id": r.client_id,
                "video_id": r.video_id,
                "buyer_question_ref_id": r.bq_ref_id,
                "universe_hash": r.universe_hash,
                "prepared_hash": r.prepared_hash,
                "score": r.score,
                "level": r.level,
                "components": r.components,
                "facets_covered": r.facets_covered,
                "facets_missing": r.facets_missing,
                "method": r.method,
                "explanation": r.explanation,
            }
            for r in rows
        )
        return _validated(AnswerDirectness, payload, "answer_directness")

    @staticmethod
    async def _thumbnails(session: AsyncSession, video_ids: list[str]) -> dict[str, ThumbnailTraits]:
        rows = await _chunked_scalars(
            session, video_ids, lambda ids: select(YtThumbnailTrait).where(YtThumbnailTrait.video_id.in_(ids))
        )
        names = [name for name in ThumbnailTraits.model_fields]
        return {
            t.video_id: t
            for t in _validated(ThumbnailTraits, (_columns(r, names) for r in rows), "thumbnail")
        }

    @staticmethod
    async def _own_analytics(session: AsyncSession, request: AnalysisRequest) -> OwnAnalytics | None:
        token = (
            await session.execute(
                select(OAuthToken)
                .where(
                    OAuthToken.client_id == request.client_id, OAuthToken.channel_id == request.own_channel_id
                )
                .order_by(OAuthToken.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if token is None:
            return None
        daily = (
            (
                await session.execute(
                    select(OwnAnalyticsDaily).where(
                        OwnAnalyticsDaily.client_id == request.client_id, OwnAnalyticsDaily.video_id != ""
                    )
                )
            )
            .scalars()
            .all()
        )
        if not daily:
            return None
        traffic = (
            (
                await session.execute(
                    select(OwnTrafficSource).where(OwnTrafficSource.client_id == request.client_id)
                )
            )
            .scalars()
            .all()
        )
        terms = (
            (await session.execute(select(OwnSearchTerm).where(OwnSearchTerm.client_id == request.client_id)))
            .scalars()
            .all()
        )
        status = (
            token.status
            if token.status in ("active", "revoked", "invalid_grant", "expired_testing")
            else "revoked"
        )
        return OwnAnalytics(
            connection=OAuthConnectionStatus(
                status=status,
                channel_id=token.channel_id,
                scopes=list(token.scopes),
                last_verified_at=token.authorization_verified_at,
            ),
            data_through=max(row.day for row in daily),
            video_period=_video_periods(daily),
            video_daily_views=[
                OwnVideoDayViews(video_id=r.video_id, day=r.day, views=r.views) for r in daily
            ],
            traffic_sources=[
                TrafficSourceRow(
                    video_id=r.video_id or None,
                    period_start=r.period_start,
                    period_end=r.period_end,
                    source_type=r.source_type,
                    views=r.views,
                    estimated_minutes_watched=r.estimated_minutes_watched or 0,
                )
                for r in traffic
            ],
            search_terms=[
                SearchTermRow(
                    video_id=r.video_id or None,
                    period_start=r.period_start,
                    period_end=r.period_end,
                    term=r.term,
                    views=r.views,
                    estimated_minutes_watched=r.estimated_minutes_watched or 0,
                )
                for r in terms
            ],
        )


def _video_periods(daily: Sequence[OwnAnalyticsDaily]) -> list[OwnVideoMetrics]:
    grouped: dict[str, list[OwnAnalyticsDaily]] = defaultdict(list)
    for row in daily:
        grouped[row.video_id].append(row)
    periods = []
    for video_id, rows in sorted(grouped.items()):
        views = sum(r.views for r in rows)
        minutes = sum(r.estimated_minutes_watched or 0 for r in rows)
        days: list[date] = [r.day for r in rows]
        periods.append(
            OwnVideoMetrics(
                video_id=video_id,
                period_start=min(days),
                period_end=max(days),
                creator_content_type=next(
                    (r.creator_content_type for r in rows if r.creator_content_type), None
                ),
                views=views,
                engaged_views=None,
                estimated_minutes_watched=minutes,
                average_view_duration_s=minutes * 60 / views if views else 0.0,
                # the daily table carries no duration share, so the percentage stays at its neutral zero
                average_view_percentage=0.0,
                subscribers_gained=sum(r.subscribers_gained or 0 for r in rows),
                subscribers_lost=sum(r.subscribers_lost or 0 for r in rows),
                likes=sum(r.likes or 0 for r in rows),
                comments=sum(r.comments or 0 for r in rows),
                shares=sum(r.shares or 0 for r in rows),
            )
        )
    return periods


@adapter_registry.register("analytics_inputs", "shared_cache")
def build_input_source(container: Any) -> SharedCacheInputSource:
    return SharedCacheInputSource(container.session_factory)


def _upsert(table: Any, rows: list[dict[str, Any]], keys: Sequence[str]) -> Any:
    statement = insert(table).values(rows)
    updates = {c: statement.excluded[c] for c in rows[0] if c not in keys}
    return statement.on_conflict_do_update(index_elements=list(keys), set_=updates)


class AnalyticsStore:
    """writes the shared, client-agnostic rows; owner-authorised values never enter shared tables."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def write(self, result: AnalyticsResult, inputs: AnalyticsInputs) -> dict[str, int]:
        rows = self.rows(result, inputs)
        async with self.session_factory() as session, session.begin():
            for table, keys, items in rows:
                for start in range(0, len(items), 500):
                    await session.execute(_upsert(table, items[start : start + 500], keys))
        return {table.name: len(items) for table, _, items in rows}

    def rows(
        self, result: AnalyticsResult, inputs: AnalyticsInputs
    ) -> list[tuple[Any, tuple[str, ...], list[dict[str, Any]]]]:
        metrics = result.metrics.get(SHARED_AGE, {})
        videos = {v.video_id: v for v in inputs.videos}
        oldest = {
            vid: min(
                (v.source_oldest_at for v in m.views_at_age.values() if v.views is not None),
                default=result.as_of,
            )
            for vid, m in metrics.items()
        }
        metric_rows, age_rows, outlier_rows = [], [], []
        for vid, m in sorted(metrics.items()):
            metric_rows.append(
                {
                    "video_id": vid,
                    "channel_id": m.channel_id,
                    "as_of": m.as_of,
                    "segment": m.segment.value,
                    "segment_evidence": m.segment_evidence,
                    "format_key": m.format_key,
                    "age_anchor_at": m.age_anchor_at,
                    "age_days": m.age_days,
                    "comparison_age_days": m.comparison_age_days,
                    "views_now": m.views_now,
                    "likes_now": m.likes_now,
                    "comments_now": m.comments_now,
                    "views_per_day_lifetime": m.views_per_day_lifetime,
                    "velocity_recent": m.velocity_recent.model_dump(mode="json")
                    if m.velocity_recent
                    else None,
                    "velocity_7d": m.velocity_7d.model_dump(mode="json") if m.velocity_7d else None,
                    "engagement_rate": m.engagement_rate,
                    "like_rate": m.like_rate,
                    "comment_rate": m.comment_rate,
                    "engagement_basis": m.engagement_basis,
                    "duplicate_group_id": m.duplicate_group_id,
                    "flags": [f.value for f in m.flags],
                    "metrics_version": m.metrics_version,
                    "source_oldest_at": oldest[vid],
                }
            )
            for value in m.views_at_age.values():
                if value.views is None or value.method is AgeMethod.owner_analytics:
                    continue
                age_rows.append(
                    {
                        "video_id": vid,
                        "age_days": value.age_days,
                        "source": value.source,
                        "views": value.views,
                        "likes": value.likes,
                        "comments": value.comments,
                        "method": value.method.value,
                        "observed_age_days": value.observed_age_days,
                        "bracket_lo_at": value.bracket_lo_at,
                        "bracket_hi_at": value.bracket_hi_at,
                        "bracket_spread": value.bracket_spread,
                        "as_of": value.as_of,
                        "source_oldest_at": value.source_oldest_at,
                    }
                )
        for by_video in result.metrics.values():
            for vid, m in sorted(by_video.items()):
                a = m.outlier
                if a is None or a.method is AgeMethod.owner_analytics:
                    continue
                outlier_rows.append(
                    {
                        "video_id": vid,
                        "as_of_date": result.as_of.date(),
                        "age_days": a.age_days,
                        "method": a.method.value,
                        "value": a.value,
                        "baseline_id": a.baseline_id,
                        "baseline_median": a.baseline_median,
                        "baseline_n": a.baseline_n,
                        "ratio": a.ratio,
                        "log_robust_z": a.log_robust_z,
                        "tier": a.tier.value,
                        "is_outlier": a.is_outlier,
                        "suppressed_reason": a.suppressed_reason,
                        "source_oldest_at": oldest.get(vid, result.as_of),
                    }
                )
        baseline_rows = [
            {
                "baseline_id": b.baseline_id,
                "channel_id": b.channel_id,
                "segment": b.segment.value,
                "metric": b.metric,
                "age_days": b.age_days,
                "method": b.method.value,
                "age_band": None,
                "anchor_video_id": b.anchor_video_id,
                "as_of": b.as_of,
                "median": b.median,
                "mean": b.mean,
                "trimmed_mean_10": b.trimmed_mean_10,
                "p25": b.p25,
                "p75": b.p75,
                "mad_log10": b.mad_log10,
                "n": b.n,
                "video_ids": b.video_ids,
                "fallback_level": b.fallback_level,
                "sufficient": b.sufficient,
                "source_oldest_at": min(
                    (oldest.get(v, result.as_of) for v in b.video_ids), default=result.as_of
                ),
            }
            for b in result.baselines
            if b.method is not AgeMethod.owner_analytics
        ]
        trait_rows = []
        for vid, traits in sorted(result.traits.items()):
            video = videos.get(vid)
            if video is None:
                continue
            trait_rows.append(
                {
                    "video_id": vid,
                    "traits": {
                        "segment": traits.segment.value,
                        "length_band": traits.length_band,
                        "format_key": traits.format_key,
                        "title": traits.title.model_dump(mode="json", exclude=CLIENT_TITLE_FIELDS),
                        "description": traits.description.model_dump(
                            mode="json", exclude=CLIENT_DESCRIPTION_FIELDS
                        ),
                    },
                    "traits_version": traits.traits_version,
                    "title_hash": video.content_hash,
                    "description_hash": video.description_hash,
                    "computed_at": result.as_of,
                    "source_oldest_at": video.metadata_fetched_at,
                }
            )
        signal_rows = [
            {
                "video_id": vid,
                **s.model_dump(mode="json", exclude={"video_id", "matched_buyer_questions", "excerpts"}),
                "as_of": s.as_of,
                "source_oldest_at": min(
                    (c.fetched_at for c in inputs.comments.get(vid, [])), default=result.as_of
                ),
            }
            for vid, s in sorted(result.comment_signals.items())
        ]
        tables = [
            (YtVideoMetric.__table__, ("video_id",), metric_rows),
            (YtVideoAgeValue.__table__, ("video_id", "age_days", "source"), age_rows),
            (YtChannelBaseline.__table__, ("baseline_id",), baseline_rows),
            (YtVideoOutlier.__table__, ("video_id", "as_of_date", "age_days", "method"), outlier_rows),
            (YtVideoTrait.__table__, ("video_id",), trait_rows),
            (YtCommentSignal.__table__, ("video_id",), signal_rows),
        ]
        return [(table, keys, items) for table, keys, items in tables if items]

    async def write_thumbnails(
        self, traits: Sequence[ThumbnailTraits], urls: Mapping[str, str], verified_at: datetime
    ) -> int:
        rows = [
            {
                **t.model_dump(),
                "thumbnail_url": urls[t.video_id],
                "image_etag": None,
                "source_verified_at": verified_at,
            }
            for t in traits
        ]
        if not rows:
            return 0
        async with self.session_factory() as session, session.begin():
            await session.execute(_upsert(YtThumbnailTrait.__table__, rows, ("video_id",)))
        return len(rows)

    async def write_recurring_text(
        self, channel_id: str, shares: Mapping[tuple[str, int], float], as_of: datetime
    ) -> int:
        rows = [
            {
                "channel_id": channel_id,
                "normalized_text": text,
                "grid_cell": cell,
                "share": share,
                "as_of": as_of,
                "source_oldest_at": as_of,
            }
            for (text, cell), share in sorted({**shares, COMPUTED_MARKER: 0.0}.items())
        ]
        async with self.session_factory() as session, session.begin():
            await session.execute(
                _upsert(
                    YtChannelRecurringText.__table__, rows, ("channel_id", "normalized_text", "grid_cell")
                )
            )
        return len(rows)

    async def recurring_text(self, channel_id: str, since: datetime) -> frozenset[tuple[str, int]] | None:
        async with self.session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(YtChannelRecurringText).where(
                            YtChannelRecurringText.channel_id == channel_id,
                            YtChannelRecurringText.as_of >= since,
                        )
                    )
                )
                .scalars()
                .all()
            )
        if not rows:
            return None
        return frozenset((r.normalized_text, r.grid_cell) for r in rows) - {COMPUTED_MARKER}
