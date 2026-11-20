import uuid
from datetime import date, datetime

from sqlalchemy import REAL, BigInteger, ForeignKey, Index, PrimaryKeyConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantColumns, Zone, client_fk


class OwnAnalyticsDaily(TenantColumns, Base):
    __tablename__ = "own_analytics_daily"
    __zone__ = Zone.tenant
    __table_args__ = (
        PrimaryKeyConstraint("client_id", "video_id", "day"),
        client_fk(),
        Index("ix_own_analytics_daily_client_id_day", "client_id", "day"),
    )

    # '' marks the channel-level row
    video_id: Mapped[str] = mapped_column(server_default=text("''"))
    day: Mapped[date]
    oauth_token_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("oauth_tokens.id", ondelete="CASCADE"), index=True
    )
    views: Mapped[int] = mapped_column(BigInteger)
    engaged_views: Mapped[int | None] = mapped_column(BigInteger)
    estimated_minutes_watched: Mapped[int | None] = mapped_column(BigInteger)
    average_view_duration_s: Mapped[int | None]
    subscribers_gained: Mapped[int | None]
    subscribers_lost: Mapped[int | None]
    likes: Mapped[int | None] = mapped_column(BigInteger)
    comments: Mapped[int | None] = mapped_column(BigInteger)
    shares: Mapped[int | None] = mapped_column(BigInteger)
    creator_content_type: Mapped[str | None]
    fetched_at: Mapped[datetime] = mapped_column(index=True)


class OwnTrafficSource(TenantColumns, Base):
    __tablename__ = "own_traffic_source"
    __zone__ = Zone.tenant
    __table_args__ = (
        PrimaryKeyConstraint("client_id", "video_id", "period_start", "period_end", "source_type"),
        client_fk(),
    )

    video_id: Mapped[str] = mapped_column(server_default=text("''"))
    period_start: Mapped[date]
    period_end: Mapped[date]
    source_type: Mapped[str]
    oauth_token_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("oauth_tokens.id", ondelete="CASCADE"), index=True
    )
    views: Mapped[int] = mapped_column(BigInteger)
    estimated_minutes_watched: Mapped[int | None] = mapped_column(BigInteger)
    fetched_at: Mapped[datetime] = mapped_column(index=True)


class OwnSearchTerm(TenantColumns, Base):
    __tablename__ = "own_search_term"
    __zone__ = Zone.tenant
    __table_args__ = (
        PrimaryKeyConstraint("client_id", "video_id", "period_start", "period_end", "term"),
        client_fk(),
    )

    video_id: Mapped[str] = mapped_column(server_default=text("''"))
    period_start: Mapped[date]
    period_end: Mapped[date]
    term: Mapped[str]
    oauth_token_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("oauth_tokens.id", ondelete="CASCADE"), index=True
    )
    views: Mapped[int] = mapped_column(BigInteger)
    estimated_minutes_watched: Mapped[int | None] = mapped_column(BigInteger)
    fetched_at: Mapped[datetime] = mapped_column(index=True)


class OwnVideoMetrics(TenantColumns, Base):
    __tablename__ = "own_video_metrics"
    __zone__ = Zone.tenant
    __table_args__ = (
        PrimaryKeyConstraint("client_id", "video_id", "period_start", "period_end"),
        client_fk(),
    )

    video_id: Mapped[str]
    period_start: Mapped[date]
    period_end: Mapped[date]
    oauth_token_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("oauth_tokens.id", ondelete="CASCADE"), index=True
    )
    creator_content_type: Mapped[str | None]
    views: Mapped[int] = mapped_column(BigInteger)
    engaged_views: Mapped[int | None] = mapped_column(BigInteger)
    estimated_minutes_watched: Mapped[int | None] = mapped_column(BigInteger)
    average_view_duration_s: Mapped[float | None] = mapped_column(REAL)
    average_view_percentage: Mapped[float | None] = mapped_column(REAL)
    subscribers_gained: Mapped[int | None]
    subscribers_lost: Mapped[int | None]
    likes: Mapped[int | None] = mapped_column(BigInteger)
    comments: Mapped[int | None] = mapped_column(BigInteger)
    shares: Mapped[int | None] = mapped_column(BigInteger)
    fetched_at: Mapped[datetime] = mapped_column(index=True)
