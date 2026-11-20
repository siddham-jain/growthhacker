from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import bindparam, func, select, update
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError

from app.models.youtube_cache import YtChannel, YtComment, YtVideo, YtVideoSnapshot
from app.repositories.shared.base import SharedRepository, chunked, validate_rows
from app.retention.partitions import DailyPartition

MAX_COMMENT_CHARS = 1500


def snapshot_slot(captured_at: datetime) -> datetime:
    return captured_at.replace(minute=0, second=0, microsecond=0)


class _MonotonicUpsert(SharedRepository):
    """an older api response never overwrites a newer row (guarded by the fetched-at clock)."""

    model: type[YtChannel] | type[YtVideo]
    key: str
    clock: str
    required: tuple[str, ...]
    insert_only: tuple[str, ...] = ("first_seen_at",)

    async def upsert_many(self, rows: Sequence[Mapping[str, Any]]) -> int:
        table = self.model.__table__
        validated = validate_rows(table, rows, self.required)
        written = 0
        for chunk in chunked(validated, len(table.columns)):
            statement = insert(table).values(list(chunk))
            columns = {name for row in chunk for name in row} - {self.key, *self.insert_only}
            statement = statement.on_conflict_do_update(
                index_elements=[self.key],
                set_={name: statement.excluded[name] for name in sorted(columns)},
                where=statement.excluded[self.clock] >= table.c[self.clock],
            )
            written += (await self.session.execute(statement)).rowcount
        return written

    async def get(self, key: str) -> Any:
        return await self.session.get(self.model, key)

    async def get_many(self, keys: Sequence[str]) -> Sequence[Any]:
        statement = select(self.model).where(getattr(self.model, self.key).in_(keys))
        return (await self.session.execute(statement)).scalars().all()


class ChannelRepository(_MonotonicUpsert):
    model = YtChannel
    key = "channel_id"
    clock = "metadata_fetched_at"
    required = ("channel_id", "metadata_fetched_at", "stats_fetched_at")


class VideoRepository(_MonotonicUpsert):
    model = YtVideo
    key = "video_id"
    clock = "metadata_fetched_at"
    required = (
        "video_id",
        "channel_id",
        "published_at",
        "effective_published_at",
        "ingest_source",
        "metadata_fetched_at",
        "stats_fetched_at",
    )

    async def update_stats(self, rows: Sequence[Mapping[str, Any]]) -> int:
        required = ("video_id", "view_count", "stats_fetched_at")
        validated = validate_rows(YtVideo.__table__, rows, required)
        table = YtVideo.__table__
        statement = (
            update(table)
            .where(table.c.video_id == bindparam("b_video_id"))
            .where(table.c.stats_fetched_at <= bindparam("b_stats_fetched_at"))
            .values(
                view_count=bindparam("b_view_count"),
                like_count=bindparam("b_like_count"),
                comment_count=bindparam("b_comment_count"),
                stats_fetched_at=bindparam("b_stats_fetched_at"),
            )
        )
        params = [
            {
                "b_video_id": row["video_id"],
                "b_view_count": row["view_count"],
                "b_like_count": row.get("like_count"),
                "b_comment_count": row.get("comment_count"),
                "b_stats_fetched_at": row["stats_fetched_at"],
            }
            for row in validated
        ]
        if not params:
            return 0
        result = await self.session.execute(statement, params)
        return result.rowcount

    async def mark_missing(self, video_ids: Sequence[str], *, at: datetime) -> int:
        statement = (
            update(YtVideo)
            .where(YtVideo.video_id.in_(video_ids), YtVideo.availability == "available")
            .values(availability="missing", first_missing_at=func.coalesce(YtVideo.first_missing_at, at))
        )
        return (await self.session.execute(statement)).rowcount

    async def list_for_channel(
        self, channel_id: str, *, published_after: datetime | None = None
    ) -> Sequence[YtVideo]:
        statement = select(YtVideo).where(
            YtVideo.channel_id == channel_id, YtVideo.availability == "available"
        )
        if published_after is not None:
            statement = statement.where(YtVideo.effective_published_at >= published_after)
        statement = statement.order_by(YtVideo.effective_published_at.desc())
        return (await self.session.execute(statement)).scalars().all()


class SnapshotRepository(SharedRepository):
    required = ("video_id", "slot_at", "captured_at", "age_hours", "view_count", "source")

    async def insert_many(self, rows: Sequence[Mapping[str, Any]]) -> int:
        table = YtVideoSnapshot.__table__
        validated = validate_rows(table, rows, self.required)
        inserted = 0
        for chunk in chunked(validated, len(table.columns)):
            statement = (
                insert(table)
                .values(list(chunk))
                .on_conflict_do_nothing(index_elements=["video_id", "slot_at"])
            )
            try:
                async with self.session.begin_nested():
                    inserted += (await self.session.execute(statement)).rowcount
            except DBAPIError as error:
                if "no partition of relation" not in str(error):
                    raise
                await self._create_partitions_for(chunk)
                async with self.session.begin_nested():
                    inserted += (await self.session.execute(statement)).rowcount
        return inserted

    async def _create_partitions_for(self, rows: Sequence[Mapping[str, Any]]) -> None:
        days = sorted({row["slot_at"].date() for row in rows})
        for day in days:
            await self.session.execute(
                sql_text(DailyPartition(YtVideoSnapshot.__tablename__, day).create_sql())
            )

    async def for_video(self, video_id: str, *, until: datetime | None = None) -> Sequence[YtVideoSnapshot]:
        statement = select(YtVideoSnapshot).where(YtVideoSnapshot.video_id == video_id)
        if until is not None:
            statement = statement.where(YtVideoSnapshot.captured_at <= until)
        return (await self.session.execute(statement.order_by(YtVideoSnapshot.captured_at))).scalars().all()


class CommentRepository(SharedRepository):
    required = (
        "comment_id",
        "video_id",
        "thread_id",
        "is_reply",
        "text_redacted",
        "published_at",
        "fetched_at",
    )

    async def upsert_many(self, rows: Sequence[Mapping[str, Any]]) -> int:
        table = YtComment.__table__
        validated = validate_rows(table, rows, self.required)
        for row in validated:
            if len(row["text_redacted"]) > MAX_COMMENT_CHARS:
                raise ValueError(
                    f"comment {row['comment_id']} exceeds {MAX_COMMENT_CHARS} chars; truncate at ingest"
                )
        written = 0
        for chunk in chunked(validated, len(table.columns)):
            statement = insert(table).values(list(chunk))
            mutable = (
                "text_redacted",
                "like_count",
                "reply_count",
                "relevance_rank",
                "source_updated_at",
                "fetched_at",
            )
            statement = statement.on_conflict_do_update(
                index_elements=["comment_id"],
                set_={name: statement.excluded[name] for name in mutable},
                where=statement.excluded.fetched_at >= table.c.fetched_at,
            )
            written += (await self.session.execute(statement)).rowcount
        return written

    async def for_video(self, video_id: str, *, limit: int = 100) -> Sequence[YtComment]:
        statement = (
            select(YtComment)
            .where(YtComment.video_id == video_id)
            .order_by(YtComment.published_at.desc())
            .limit(limit)
        )
        return (await self.session.execute(statement)).scalars().all()
