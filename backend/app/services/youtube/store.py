from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from app.core.db import UnitOfWork
from app.domain.youtube import (
    ChannelRecord,
    LocalizedText,
    RegionRestriction,
    ThumbnailRef,
    VideoRecord,
    VideoSnapshot,
)
from app.models.youtube_cache import (
    YtChannel,
    YtChannelResolution,
    YtHttpEtag,
    YtVideo,
    YtVideoSnapshot,
)
from app.repositories.shared import ChannelRepository, SnapshotRepository, VideoRepository
from app.services.youtube.format_classifier import aspect_of

CONFIDENCE_SCORES = {"high": 1.0, "medium": 0.6, "low": 0.3}
ALIAS_MAX_TTL = timedelta(days=28)


class FirstSourceVideoRepository(VideoRepository):
    # the first ingest source is kept; later fetches through other paths do not relabel a video
    insert_only = ("first_seen_at", "ingest_source")


def confidence_from_score(score: float | None) -> str:
    if score is None:
        return "low"
    if score >= 0.9:
        return "high"
    return "medium" if score >= 0.5 else "low"


def channel_to_row(record: ChannelRecord) -> dict[str, Any]:
    # ingest-computed columns are written by the ingest service, never by an api refetch
    row = record.model_dump(exclude={"uploads_90d", "last_upload_at", "recent_upload_ids", "cadence_flags"})
    row["custom_url"] = record.handle
    return row


def row_to_channel(row: YtChannel) -> ChannelRecord:
    return ChannelRecord(
        channel_id=row.channel_id,
        title=row.title or "",
        handle=row.handle,
        description=row.description or "",
        country=row.country,
        default_language=row.default_language,
        published_at=row.published_at or row.metadata_fetched_at,
        uploads_playlist_id=row.uploads_playlist_id,
        subscriber_count=row.subscriber_count,
        hidden_subscriber_count=row.hidden_subscriber_count,
        view_count=row.view_count or 0,
        video_count=row.video_count or 0,
        keywords=list(row.keywords or []),
        thumbnail_url=row.thumbnail_url,
        made_for_kids=row.made_for_kids,
        availability=row.availability,
        unavailable_at=row.unavailable_at,
        uploads_90d=row.uploads_90d or 0,
        last_upload_at=row.last_upload_at,
        recent_upload_ids=list(row.recent_upload_ids or [])[:50],
        cadence_flags=list(row.cadence_flags or []),
        metadata_fetched_at=row.metadata_fetched_at,
        stats_fetched_at=row.stats_fetched_at,
    )


def video_to_row(record: VideoRecord, *, localized: bool = False) -> dict[str, Any]:
    # availability and quality flags belong to the ingest service, so an api refetch leaves them alone
    row = record.model_dump(
        exclude={"aspect", "format_confidence", "format", "availability", "first_missing_at", "quality_flags"}
    )
    row["format"] = record.format.value
    row["aspect"] = (
        record.embed_height / record.embed_width if record.embed_width and record.embed_height else None
    )
    row["format_confidence"] = CONFIDENCE_SCORES[record.format_confidence]
    row["region_restriction"] = record.region_restriction.model_dump() if record.region_restriction else None
    if localized:
        row["localizations"] = {key: value.model_dump() for key, value in record.localizations.items()}
        row["localizations_fetched_at"] = record.metadata_fetched_at
    else:
        # localizations were not requested, so what was stored earlier stays
        del row["localizations"]
    row["thumbnails"] = {key: value.model_dump() for key, value in record.thumbnails.items()}
    row["stats_fetched_at"] = record.metadata_fetched_at
    return row


def row_to_video(row: YtVideo) -> VideoRecord:
    return VideoRecord(
        video_id=row.video_id,
        channel_id=row.channel_id,
        channel_title=row.channel_title or "",
        published_at=row.published_at,
        effective_published_at=row.effective_published_at,
        live_broadcast_content=row.live_broadcast_content or "none",
        live_actual_start_at=row.live_actual_start_at,
        live_actual_end_at=row.live_actual_end_at,
        live_scheduled_start_at=row.live_scheduled_start_at,
        title=row.title or "",
        description=row.description or "",
        tags=list(row.tags or []),
        content_hash=row.content_hash or "",
        description_hash=row.description_hash or "",
        default_language=row.default_language,
        default_audio_language=row.default_audio_language,
        localizations={key: LocalizedText(**value) for key, value in (row.localizations or {}).items()},
        duration_s=row.duration_s,
        embed_width=row.embed_width,
        embed_height=row.embed_height,
        aspect=aspect_of(row.embed_width, row.embed_height),
        format=row.format,
        format_basis=list(row.format_basis or []),
        format_confidence=confidence_from_score(row.format_confidence),
        has_captions=bool(row.has_captions),
        licensed_content=bool(row.licensed_content),
        age_restricted=bool(row.age_restricted),
        region_restriction=RegionRestriction(**row.region_restriction) if row.region_restriction else None,
        made_for_kids=bool(row.made_for_kids),
        privacy_status=row.privacy_status or "public",
        upload_status=row.upload_status or "processed",
        embeddable=True if row.embeddable is None else row.embeddable,
        public_stats_viewable=True if row.public_stats_viewable is None else row.public_stats_viewable,
        has_paid_product_placement=row.has_paid_product_placement,
        thumbnails={key: ThumbnailRef(**value) for key, value in (row.thumbnails or {}).items()},
        thumbnail_url=row.thumbnail_url,
        comments_state=row.comments_state,
        likes_visible=True if row.likes_visible is None else row.likes_visible,
        view_count=row.view_count or 0,
        like_count=row.like_count,
        comment_count=row.comment_count,
        availability=row.availability,
        first_missing_at=row.first_missing_at,
        ingest_source=row.ingest_source,
        quality_flags=list(row.quality_flags or []),
        metadata_fetched_at=row.metadata_fetched_at,
    )


def snapshot_to_row(snapshot: VideoSnapshot) -> dict[str, Any]:
    return snapshot.model_dump()


def row_to_snapshot(row: YtVideoSnapshot) -> VideoSnapshot:
    return VideoSnapshot(
        video_id=row.video_id,
        slot_at=row.slot_at,
        captured_at=row.captured_at,
        age_hours=max(0.0, row.age_hours),
        view_count=row.view_count,
        like_count=row.like_count,
        comment_count=row.comment_count,
        source=row.source,
        counting_regime=row.counting_regime,
    )


class YouTubeStore:
    """persistence for the shared public cache; one unit of work per operation."""

    def __init__(self, uow_factory: Callable[[], UnitOfWork]) -> None:
        self._uow_factory = uow_factory

    def uow(self) -> UnitOfWork:
        return self._uow_factory()

    async def channels(self, ids: Sequence[str]) -> dict[str, YtChannel]:
        if not ids:
            return {}
        async with self._uow_factory() as uow:
            rows = await uow.repository(ChannelRepository).get_many(list(ids))
            uow.session.expunge_all()
        return {row.channel_id: row for row in rows}

    async def save_channels(self, records: Sequence[ChannelRecord]) -> None:
        if not records:
            return
        async with self._uow_factory() as uow:
            await uow.repository(ChannelRepository).upsert_many(
                [channel_to_row(record) for record in records]
            )
            await uow.commit()

    async def update_channel(self, channel_id: str, **values: Any) -> None:
        async with self._uow_factory() as uow:
            await uow.session.execute(
                update(YtChannel).where(YtChannel.channel_id == channel_id).values(**values)
            )
            await uow.commit()

    async def mark_channels_unavailable(self, ids: Sequence[str], at: datetime) -> None:
        if not ids:
            return
        async with self._uow_factory() as uow:
            await uow.session.execute(
                update(YtChannel)
                .where(YtChannel.channel_id.in_(list(ids)), YtChannel.availability == "available")
                .values(availability="unavailable", unavailable_at=at)
            )
            await uow.commit()

    async def videos(self, ids: Sequence[str]) -> dict[str, YtVideo]:
        if not ids:
            return {}
        async with self._uow_factory() as uow:
            rows = await uow.repository(VideoRepository).get_many(list(ids))
            uow.session.expunge_all()
        return {row.video_id: row for row in rows}

    async def channel_videos(
        self, channel_id: str, *, published_after: datetime | None = None
    ) -> list[YtVideo]:
        async with self._uow_factory() as uow:
            rows = await uow.repository(VideoRepository).list_for_channel(
                channel_id, published_after=published_after
            )
            uow.session.expunge_all()
        return list(rows)

    async def save_videos(
        self,
        records: Sequence[VideoRecord],
        snapshots: Sequence[VideoSnapshot] = (),
        *,
        localized: bool = False,
    ) -> None:
        if not records:
            return
        async with self._uow_factory() as uow:
            await uow.repository(FirstSourceVideoRepository).upsert_many(
                [video_to_row(record, localized=localized) for record in records]
            )
            await uow.session.execute(
                update(YtVideo)
                .where(
                    YtVideo.video_id.in_([record.video_id for record in records]),
                    YtVideo.availability != "available",
                )
                .values(availability="available", first_missing_at=None)
            )
            if snapshots:
                await uow.repository(SnapshotRepository).insert_many([snapshot_to_row(s) for s in snapshots])
            await uow.commit()

    async def save_stats(self, snapshots: Sequence[VideoSnapshot]) -> int:
        if not snapshots:
            return 0
        async with self._uow_factory() as uow:
            inserted = await uow.repository(SnapshotRepository).insert_many(
                [snapshot_to_row(s) for s in snapshots]
            )
            await uow.repository(VideoRepository).update_stats(
                [
                    {
                        "video_id": s.video_id,
                        "view_count": s.view_count,
                        "like_count": s.like_count,
                        "comment_count": s.comment_count,
                        "stats_fetched_at": s.captured_at,
                    }
                    for s in snapshots
                ]
            )
            await uow.commit()
        return inserted

    async def record_misses(
        self, ids: Sequence[str], now: datetime, removed_after: timedelta
    ) -> dict[str, str]:
        # first miss marks missing; a second miss at least removed_after later confirms removal
        if not ids:
            return {}
        async with self._uow_factory() as uow:
            session = uow.session
            await session.execute(
                update(YtVideo)
                .where(
                    YtVideo.video_id.in_(list(ids)),
                    YtVideo.availability == "missing",
                    YtVideo.first_missing_at <= now - removed_after,
                )
                .values(availability="removed")
            )
            await uow.repository(VideoRepository).mark_missing(list(ids), at=now)
            rows = await session.execute(
                select(YtVideo.video_id, YtVideo.availability).where(YtVideo.video_id.in_(list(ids)))
            )
            states = {video_id: availability for video_id, availability in rows}
            await uow.commit()
        return states

    async def add_quality_flag(self, video_ids: Sequence[str], flag: str) -> None:
        if not video_ids:
            return
        async with self._uow_factory() as uow:
            await uow.session.execute(
                update(YtVideo)
                .where(
                    YtVideo.video_id.in_(list(video_ids)),
                    func.array_position(YtVideo.quality_flags, flag).is_(None),
                )
                .values(quality_flags=YtVideo.quality_flags + [flag])
            )
            await uow.commit()

    async def snapshots(
        self, video_ids: Sequence[str], until: datetime | None = None
    ) -> dict[str, list[VideoSnapshot]]:
        if not video_ids:
            return {}
        statement = select(YtVideoSnapshot).where(YtVideoSnapshot.video_id.in_(list(video_ids)))
        if until is not None:
            statement = statement.where(YtVideoSnapshot.captured_at <= until)
        async with self._uow_factory() as uow:
            rows = (
                (await uow.session.execute(statement.order_by(YtVideoSnapshot.captured_at))).scalars().all()
            )
            uow.session.expunge_all()
        grouped: dict[str, list[VideoSnapshot]] = {video_id: [] for video_id in video_ids}
        for row in rows:
            grouped[row.video_id].append(row_to_snapshot(row))
        return grouped

    async def delete_snapshots(self, keys: Sequence[tuple[str, datetime]]) -> int:
        if not keys:
            return 0
        deleted = 0
        async with self._uow_factory() as uow:
            for start in range(0, len(keys), 500):
                batch = keys[start : start + 500]
                condition = [
                    and_(YtVideoSnapshot.video_id == v, YtVideoSnapshot.slot_at == s) for v, s in batch
                ]
                result = await uow.session.execute(delete(YtVideoSnapshot).where(or_(*condition)))
                deleted += result.rowcount
            await uow.commit()
        return deleted

    async def resolution(self, kind: str, value: str, now: datetime) -> YtChannelResolution | None:
        async with self._uow_factory() as uow:
            row = await uow.session.get(YtChannelResolution, (kind, value))
            uow.session.expunge_all()
        return row if row is not None and row.expires_at > now else None

    async def save_resolution(
        self, kind: str, value: str, channel_id: str | None, via: str, now: datetime, ttl: timedelta
    ) -> None:
        values = {
            "input_kind": kind,
            "input_normalized": value,
            "channel_id": channel_id,
            "resolved_via": via,
            "resolved_at": now,
            "expires_at": now + min(ttl, ALIAS_MAX_TTL),
        }
        statement = insert(YtChannelResolution).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=["input_kind", "input_normalized"],
            set_={
                name: statement.excluded[name]
                for name in values
                if name not in ("input_kind", "input_normalized")
            },
        )
        async with self._uow_factory() as uow:
            await uow.session.execute(statement)
            await uow.commit()

    async def etag(self, request_key: str) -> str | None:
        async with self._uow_factory() as uow:
            row = await uow.session.get(YtHttpEtag, request_key)
            return row.etag if row else None

    async def save_etag(self, request_key: str, etag: str, now: datetime) -> None:
        statement = insert(YtHttpEtag).values(request_key=request_key, etag=etag, stored_at=now)
        statement = statement.on_conflict_do_update(
            index_elements=["request_key"], set_={"etag": statement.excluded.etag, "stored_at": now}
        )
        async with self._uow_factory() as uow:
            await uow.session.execute(statement)
            await uow.commit()
