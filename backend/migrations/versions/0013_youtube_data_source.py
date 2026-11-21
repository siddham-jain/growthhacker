"""data_source on cached channels and videos, and yt_dlp as a snapshot source for the scraper fallback

Revision ID: 0013_data_source
Revises: 0012_citations
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_data_source"
down_revision: str | None = "0012_citations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("yt_channels", "yt_videos")


def upgrade() -> None:
    for table in TABLES:
        op.add_column(
            table, sa.Column("data_source", sa.Text(), server_default=sa.text("'data_api'"), nullable=False)
        )
        op.create_check_constraint(
            op.f(f"ck_{table}_data_source"), table, "data_source IN ('data_api', 'yt_dlp')"
        )
    op.drop_constraint(op.f("ck_yt_video_snapshots_source"), "yt_video_snapshots", type_="check")
    op.create_check_constraint(
        op.f("ck_yt_video_snapshots_source"),
        "yt_video_snapshots",
        "source IN ('batch_stats', 'videos_list', 'backtest_freeze', 'yt_dlp')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM yt_video_snapshots WHERE source = 'yt_dlp'")
    op.drop_constraint(op.f("ck_yt_video_snapshots_source"), "yt_video_snapshots", type_="check")
    op.create_check_constraint(
        op.f("ck_yt_video_snapshots_source"),
        "yt_video_snapshots",
        "source IN ('batch_stats', 'videos_list', 'backtest_freeze')",
    )
    for table in TABLES:
        op.drop_constraint(op.f(f"ck_{table}_data_source"), table, type_="check")
        op.drop_column(table, "data_source")
